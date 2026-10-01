# Fraud Detection Algorithms: Neptune Deployment Guide

> **Your stack in one sentence:** Neptune DB (Gremlin, OLTP, live) → snapshot → Neptune Analytics (openCypher, OLAP, in-memory) → results → DynamoDB (fast read for the portal). Algorithms split cleanly into two groups based on which engine runs them.

---

## How the Two Neptune Engines Divide the Work

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Neptune DATABASE (live, always-on, Gremlin)                            │
│                                                                          │
│  Layer 1 — Property Queries  (these run HERE, not in Analytics)         │
│  ├── Bot Auth           → scan auth_events vertices + HAD_AUTH_EVENT    │
│  ├── ATO Downgrade      → walk HAD_AUTH_EVENT per member                │
│  ├── Step-Up Probing    → count HAD_STEP_UP per member                  │
│  ├── Ghost Agency       → filter Agency vertex properties               │
│  └── Collusion Cycles   → directed cycle on RELATED_TO edges            │
│                                                                          │
└───────────────────┬─────────────────────────────────────────────────────┘
                    │  daily snapshot
                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Neptune ANALYTICS (in-memory, openCypher, paused between runs)         │
│                                                                          │
│  Layer 2 — Structural Algorithms (these run HERE)                       │
│  ├── Degree Centrality  → CALL neptune.algo.degree.mutate(...)          │
│  └── Node Similarity    → CALL neptune.algo.jaccardSimilarity(...)      │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

The five "keep" queries and the two new ones (Ghost Agency, Collusion Cycles) are all **Layer 1 property queries**. They don't need Analytics. They run directly on Neptune DB through Gremlin or openCypher, making them your fastest, cheapest, most real-time detectors.

---

## The Five Keeps: Gremlin Queries for Neptune DB

These translate the existing Python CSV logic into Gremlin traversals that run live on the graph.

---

### 1. Bot Auth

**Current Python logic:** scan `syn-auth_events-part-*.csv`, compute `respondToAuthTime - initAuthTime`, flag members with ≥ 5 events < 1500 ms.

#### Neptune DB — Gremlin

```groovy
// Find all AuthEvent vertices with sub-1500ms response latency
// Then count per member, flag those with ≥5 fast events
g.V().hasLabel('AuthEvent')
  .has('initAuthTime', gt(0))
  .has('respondToAuthTime', gt(0))
  .filter(
    values('respondToAuthTime').as('r')
    .select('r').math('respondToAuthTime - initAuthTime').is(lt(1500))
  )
  .group()
    .by(__.in('HAD_AUTH_EVENT').hasLabel('Member'))
    .by(count())
  .unfold()
  .where(select(values).is(gte(5)))
  .project('member_id', 'fast_login_count')
    .by(select(keys).values('memberId'))
    .by(select(values))
  .order().by('fast_login_count', desc)
```

#### openCypher (Neptune Analytics or DB)

```cypher
MATCH (m:Member)-[:HAD_AUTH_EVENT]->(e:AuthEvent)
WHERE e.respondToAuthTime IS NOT NULL
  AND e.initAuthTime IS NOT NULL
  AND (toInteger(e.respondToAuthTime) - toInteger(e.initAuthTime)) < 1500
WITH m, count(e) AS fastLoginCount
WHERE fastLoginCount >= 5
RETURN m.memberId AS memberId, fastLoginCount
ORDER BY fastLoginCount DESC
```

#### Production Notes

- Pure per-event property filter — runs in milliseconds on Neptune DB. No Analytics needed.
- **Trigger:** run after each `analytics_runner.py` batch cycle, or as a scheduled Lambda every hour.
- **Output:** write flagged `memberId` list to DynamoDB `FraudFlags` table with `detector=BOT_AUTH`, `score=1.0`, `TTL=24h`.
- **D-CHAIN-002 fix:** add a `WHERE m.createdAt < (NOW - 90days)` filter to exclude freshly-created accounts. In Gremlin: `.has('createdAt', lt(cutoffTimestamp))` on the Member vertex.

---

### 2. Degree Centrality

**Current Python logic:** iterate device/IP nodes in NetworkX, count member neighbors per agency, flag if device-degree ≥ 3 from ≥ 2 agencies.

#### Neptune Analytics — openCypher (the right home for this)

```cypher
-- Step 1: stamp degree onto Device and IpAddress nodes (run once per batch)
CALL neptune.algo.degree.mutate({
  edgeLabels: ['LOGGED_IN'],
  writeProperty: 'memberLoginDegree',
  direction: 'INCOMING'
}) YIELD success

CALL neptune.algo.degree.mutate({
  edgeLabels: ['ACCESSED_FROM'],
  writeProperty: 'memberAccessDegree',
  direction: 'INCOMING'
}) YIELD success

-- Step 2: query — devices shared across ≥2 agencies with degree ≥3
MATCH (d:Device)<-[:LOGGED_IN]-(m:Member)-[:BELONGS_TO]->(a:Agency)
WITH d, collect(DISTINCT a.agencyId) AS agencyList, count(DISTINCT m) AS memberCount
WHERE size(agencyList) >= 2 AND memberCount >= 3
RETURN d.deviceId AS device, agencyList, memberCount
ORDER BY memberCount DESC
```

#### Neptune DB — Gremlin (real-time version)

```groovy
// Cross-agency device check — runs on live graph
g.V().hasLabel('Device')
  .where(
    __.in('LOGGED_IN').hasLabel('Member')
      .out('BELONGS_TO').hasLabel('Agency')
      .dedup()
      .count().is(gte(2))
  )
  .filter(
    __.in('LOGGED_IN').hasLabel('Member').count().is(gte(3))
  )
  .project('device', 'member_count', 'agency_count')
    .by('deviceId')
    .by(__.in('LOGGED_IN').hasLabel('Member').count())
    .by(__.in('LOGGED_IN').hasLabel('Member')
          .out('BELONGS_TO').hasLabel('Agency').dedup().count())
```

#### Production Notes

- Run the `.mutate` version in Neptune Analytics during the daily batch (inside `analytics_runner.py`). It stamps `memberLoginDegree` onto every Device node.
- The real-time Gremlin version runs on Neptune DB for on-demand drill-down (the `GraphQueryService` in your HLD already calls this pattern — see `02-hld.md`'s "Cross-Agency Device Sharing" query).
- **Batch output** → DynamoDB with `detector=DEGREE_CENTRALITY`.

---

### 3. ATO Downgrade

**Current Python logic:** walk `HAD_AUTH_EVENT` edges per member sorted by timestamp, look for ≥ 2 strength decreases within 30 minutes.

#### Neptune DB — Gremlin

```groovy
// Get auth event sequence per member, detect downgrade pattern
g.V().hasLabel('Member')
  .as('member')
  .out('HAD_AUTH_EVENT').hasLabel('AuthEvent')
  .order().by('timestamp', asc)
  .fold()
  .filter(
    __.unfold()
      .values('challengeName')
      // challenge strength map applied in application layer after fetch
  )
  .project('member_id', 'auth_sequence')
    .by(select('member').values('memberId'))
    .by(__.unfold().project('challenge','ts')
          .by('challengeName').by('timestamp').fold())
```

> Since Gremlin doesn't do inline arithmetic over ordered sequences well, the cleanest production pattern is a **hybrid**: Gremlin fetches the auth sequence per member, your Lambda/ECS job does the downgrade detection logic in Python. This matches exactly what `query_ato_downgrade.py` already does, just against live Neptune instead of CSVs.

#### Neptune Analytics — openCypher (cleaner for sequence logic)

```cypher
-- Fetch auth sequences per member for post-processing
MATCH (m:Member)-[:HAD_AUTH_EVENT]->(e:AuthEvent)
WHERE e.challengeName IS NOT NULL AND e.timestamp IS NOT NULL
WITH m, e ORDER BY m.memberId, e.timestamp
WITH m, collect({challenge: e.challengeName, ts: e.timestamp}) AS authSeq
WHERE size(authSeq) >= 3
RETURN m.memberId AS memberId, authSeq
```

#### Production Notes

- The downgrade detection (counting strength drops within 30-minute windows) stays in Python — it's O(n) per member and trivial. openCypher handles the graph fetch; Python handles the sequential logic.
- **Trigger:** Lambda on a schedule (hourly). Calls Neptune DB Gremlin to fetch auth sequences for members active in the last 2 hours. Applies downgrade detection in Python. Writes hits to DynamoDB.
- This is already modeled in your HLD's `GraphQueryService` — add an `ato_downgrade_check(member_id)` method.

---

### 4. Step-Up Probing

#### Neptune DB — Gremlin

```groovy
// Count HAD_STEP_UP edges per member, flag ≥3
g.V().hasLabel('Member')
  .where(__.out('HAD_STEP_UP').count().is(gte(3)))
  .project('member_id', 'stepup_count')
    .by('memberId')
    .by(__.out('HAD_STEP_UP').count())
  .order().by('stepup_count', desc)
```

#### openCypher equivalent

```cypher
MATCH (m:Member)-[:HAD_STEP_UP]->(s:StepUpEvent)
WITH m, count(s) AS stepupCount
WHERE stepupCount >= 3
RETURN m.memberId AS memberId, stepupCount,
       collect(s.reason) AS reasons
ORDER BY stepupCount DESC
```

#### Production Notes

- Cheapest detector in the suite. Single hop, no joins, sub-100ms on Neptune DB.
- **Real-time hook:** plug into the existing Adaptive Auth Agent. When a member triggers a step-up, query their cumulative count — if it just crossed 3, immediately escalate to a fraud alert rather than just a step-up.
- Also run in batch to catch historical patterns.

---

### 5. Node Similarity (Jaccard)

#### Neptune Analytics — openCypher (native built-in)

```cypher
-- Step 1: run Jaccard similarity between Agency nodes
-- based on their shared Device + IpAddress neighborhoods
CALL neptune.algo.jaccardSimilarity({
  sourceLabels: ['Agency'],
  targetLabels: ['Device', 'IpAddress'],
  sourceTargetEdgeLabels: ['LOGGED_IN', 'ACCESSED_FROM'],
  relationshipFilter: 'BELONGS_TO>LOGGED_IN|BELONGS_TO>ACCESSED_FROM',
  similarityCutoff: 0.2,
  writeProperty: 'agencyInfraSimilarity'
}) YIELD nodesCompared, similarityPairs
RETURN nodesCompared, similarityPairs

-- Step 2: find flagged pairs
MATCH (a1:Agency)-[s:SIMILAR_TO]->(a2:Agency)
WHERE s.agencyInfraSimilarity >= 0.2
  AND NOT EXISTS {
    MATCH (a1)-[:RELATED_TO]-(a2)   // exclude known relationships
  }
RETURN a1.agencyId, a2.agencyId, s.agencyInfraSimilarity AS jaccard
ORDER BY jaccard DESC
```

> **Note on the path:** `algo_node_similarity.py` goes `Agency → Members → Devices/IPs` (two hops). Neptune Analytics' `jaccardSimilarity` can do this with the `relationshipFilter` parameter. If it doesn't support two-hop paths in `relationshipFilter`, use the manual version below.

#### Manual Two-Hop Jaccard (fallback)

```cypher
MATCH (a:Agency)<-[:BELONGS_TO]-(m:Member)
MATCH (m)-[:LOGGED_IN|ACCESSED_FROM]->(infra)
WHERE NOT EXISTS { MATCH (a)-[:RELATED_TO]-(:Agency) }  // exclude known related
WITH a, collect(DISTINCT id(infra)) AS infraSet
WITH collect({agency: a, infra: infraSet}) AS agencyData
UNWIND agencyData AS a1
UNWIND agencyData AS a2
WITH a1, a2
WHERE a1.agency.agencyId < a2.agency.agencyId
WITH a1, a2,
     [x IN a1.infra WHERE x IN a2.infra] AS intersection,
     a1.infra + [x IN a2.infra WHERE NOT x IN a1.infra] AS union_set
WITH a1, a2,
     toFloat(size(intersection)) / size(union_set) AS jaccard
WHERE jaccard >= 0.2
RETURN a1.agency.agencyId, a2.agency.agencyId, jaccard
ORDER BY jaccard DESC
```

#### Production Notes

- Runs in the Neptune Analytics batch alongside Degree. Not suitable for real-time (O(n²) agency pairs).
- Schedule daily, write flagged agency pairs to DynamoDB.

---

## The New Algorithms: Also Gremlin / Property Queries

---

### A. Collusion Cycle Detection

#### Neptune DB — Gremlin (right engine — RELATED_TO is live)

```groovy
// Find all agencies in directed cycles via RELATED_TO
// Uses Gremlin's repeat().until() to detect cycles
g.V().hasLabel('Agency').as('start')
  .repeat(
    __.out('RELATED_TO').hasLabel('Agency').simplePath()
  )
  .until(
    __.out('RELATED_TO').where(eq('start'))
  )
  .out('RELATED_TO').where(eq('start'))
  .path()
  .by('agencyId')
  .dedup()  // remove reversed duplicates
```

#### openCypher (Neptune DB or Analytics — cleaner for cycles)

```cypher
-- Detect all directed cycles in RELATED_TO graph
-- Flag: cycle length 3-6, riskWeight = 0.9 on edges
MATCH path = (a:Agency)-[:RELATED_TO*2..6]->(a)
WITH nodes(path) AS cycleAgencies,
     relationships(path) AS cycleEdges
WHERE ALL(e IN cycleEdges WHERE toFloat(e.riskWeight) >= 0.7)
UNWIND cycleAgencies AS agency
RETURN DISTINCT agency.agencyId AS flaggedAgency,
       [n IN cycleAgencies | n.agencyId] AS cycle
```

#### Production Notes

- **D-CHAIN decoy exclusion is automatic:** `MATCH (a)-[:RELATED_TO*2..6]->(a)` requires a return to the start node. A linear chain A→B→C has no back-edge, so it produces zero results. No additional filter needed.
- `RELATED_TO` has only 36 edges — this query completes in single-digit milliseconds.
- **Real-time trigger:** when a new `RELATED_TO` edge is inserted, immediately run cycle detection on the affected agencies. Your ETL pipeline that loads core edges can trigger a Lambda on insert.
- Also include in the hourly Lambda sweep.

---

### B. Ghost Agency Detection

#### Neptune DB — Gremlin

```groovy
// Flag agencies created recently with high credit and no auth history
def cutoffDate = System.currentTimeMillis() - (90L * 24 * 60 * 60 * 1000)  // 90 days ago

g.V().hasLabel('Agency')
  .has('createdOn', gt(cutoffDate.toString()))    // young agency
  .has('creditLimit', gt(20000.0))                // high credit line
  .filter(
    // No first sign-in recorded OR no auth events at all
    __.choose(
      __.has('agencyFirstSignIn'),
      __.has('agencyFirstSignIn', ''),    // empty string = never signed in
      __.constant(true)                   // property absent = never signed in
    )
  )
  .project('agency_id', 'created_on', 'credit_limit')
    .by('agencyId')
    .by('createdOn')
    .by('creditLimit')
```

#### openCypher

```cypher
-- Ghost agency: new + high credit + no auth history
MATCH (a:Agency)
WHERE a.createdOn > toString(datetime() - duration({days: 90}))
  AND toFloat(a.creditLimit) > 20000.0
  AND (a.agencyFirstSignIn IS NULL OR a.agencyFirstSignIn = '')
-- Confirm no actual auth events via members
OPTIONAL MATCH (a)<-[:BELONGS_TO]-(m:Member)-[:HAD_AUTH_EVENT]->(e:AuthEvent)
WITH a, count(e) AS totalAuthEvents
WHERE totalAuthEvents = 0
RETURN a.agencyId AS agencyId,
       a.createdOn AS createdOn,
       a.creditLimit AS creditLimit
ORDER BY a.createdOn DESC
```

#### Production Notes

- The `agencyFirstSignIn = ''` check recovers the 2 rings that age/credit thresholds miss. F9-GHOST-010 has `credit=21k` (below 30k) but `agencyFirstSignIn` is empty — that field is the universal discriminator for all 10 ghost rings.
- Pure vertex property filtering — sub-millisecond on Neptune DB.
- **Real-time trigger:** when a new Agency vertex is upserted into Neptune, Lambda immediately checks it against these criteria. Flag on creation day, not the next batch.
- Also run in daily batch as a sweep for agencies that have aged into the risk zone (opened 89 days ago, now hits the threshold).

---

## The Full Production Run Order

```
HOURLY  (Neptune DB, Lambda)
├── Bot Auth                 (auth_events scan, ~50ms)
├── ATO Downgrade            (auth edges per member, ~100ms total)
├── Step-Up Probing          (single-hop count, ~10ms)
├── Collusion Cycles         (36 edges, ~5ms)
└── Ghost Agency             (property filter on new agencies, ~5ms)

DAILY BATCH  (Neptune Analytics, analytics_runner.py)
├── 1. Resume Analytics graph
├── 2. Degree Centrality     (CALL neptune.algo.degree.mutate)
├── 3. Node Similarity       (CALL neptune.algo.jaccardSimilarity or manual)
├── 4. Write results → S3 → DynamoDB
└── 5. Pause Analytics graph (~90% cost saving)
```

---

## DynamoDB Output Schema (Fraud Flag Store)

All detectors write to the same table, which the existing `RiskScoreService` already reads.

```
FraudFlags table
  PK: agencyId or memberId
  SK: detector#timestamp
```

```json
{
  "entityId": "member:5178",
  "entityType": "member",
  "detector": "BOT_AUTH",
  "score": 1.0,
  "confidence": "HIGH",
  "evidence": {
    "fast_login_count": 12,
    "avg_latency_ms": 340,
    "threshold_latency_ms": 1500
  },
  "flaggedAt": "2026-07-02T09:00:00Z",
  "ttl": 1753084800
}
```

For the portal's `GET /api/v1/agencies/{id}` endpoint, the `GraphQueryService` already fetches Neptune graph context. Add a `fraud_flags` key to that response by GSI-scanning this table by `agencyId`. The portal then renders which detectors fired and what evidence they collected.

---

## Key Deployment Watch-Outs

### 1. Neptune DB vs Analytics is not a choice — it's a sequence

Layer 1 queries (Bot Auth, ATO, Step-Up, Cycles, Ghost Agency) run against Neptune DB live. Layer 2 algorithms (Degree, Node Similarity) run against an Analytics snapshot. Never try to run `CALL neptune.algo.*` against Neptune DB — it will fail. That API only exists in the Analytics engine.

### 2. Analytics graph staleness = snapshot cadence

Degree and Node Similarity results are 24h stale at most. That's fine for structural patterns — fraud rings don't form and dissolve hourly. Bot Auth and ATO Downgrade are where freshness matters, and those run live against Neptune DB.

### 3. Surgical edit to analytics_runner.py

`analytics_runner.py` already runs Louvain, PageRank, and Degree. Replace the Louvain call with Node Similarity (or run both — Louvain adds nothing validated against your schema). Add the two `degree.mutate` calls (one for `LOGGED_IN` edges, one for `ACCESSED_FROM` edges). The skeleton is already there — it's a surgical edit to the `ALGORITHMS` list in that file.

### 4. Cost structure

Neptune DB (always-on, Gremlin) is your fixed cost. Neptune Analytics costs ~$9.60/hr at 32 m-NCUs minimum — pause it between batch runs and your daily cost is roughly **$2–3** instead of **$230**. `analytics_runner.py` already calls `pause_graph()` at the end. Don't skip that.

### 5. openCypher is the only language in Analytics

All Gremlin traversals need to be expressed in openCypher to run in Analytics. The translations above are all matched to your schema. For Neptune DB Layer 1 queries, Gremlin stays the primary language — it's faster for single-entity traversals.

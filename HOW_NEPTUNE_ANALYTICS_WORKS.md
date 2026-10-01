# How Neptune Analytics Works — Explained for Developers

A clear, visual explanation of what Neptune Analytics is, why you need it, and exactly what happens when you run an algorithm.

---

## The Problem: Why Neptune DB Gremlin Isn't Enough for Fraud Detection

### Current Setup: Neptune DB + Gremlin

```
┌─────────────────────────────────┐
│   Neptune DB (Gremlin OLTP)     │
├─────────────────────────────────┤
│ ✅ Real-time per-request queries│
│ ✅ Transactional writes         │
│ ✅ Sub-100ms response times     │
│ ❌ Limited by network latency   │
│ ❌ No built-in algorithms       │
│ ❌ Whole-graph queries are slow │
│ ❌ No community detection       │
│ ❌ No ML/embeddings             │
└─────────────────────────────────┘
```

**Example:** Find all 10+ fraud rings in your graph?
- Gremlin requires: write complex recursive traversals
- Performance: minutes (or times out)
- Must implement ring detection logic yourself

**Gremlin query:**
```groovy
g.V().hasLabel('Agency')
  .as('start')
  .repeat(__.both('RELATED_TO').simplePath())
  .times(5)
  .where(__.both('RELATED_TO').as('start'))  // cycles only
  .dedup()
  .path()
  .by(valueMap('agencyId'))
  // ^ This works but is hard to write and slow
```

---

## The Solution: Neptune Analytics

```
                Neptune DB
                    ↓
          [Create Snapshot]
                    ↓
        Neptune Analytics Cluster
        (separate, in-memory)
        ├─────────────────────────────────┤
        │ ✅ Whole-graph algorithms       │
        │ ✅ 25+ built-in graph algorithms│
        │ ✅ Community detection (Louvain)│
        │ ✅ Centrality ranking (PageRank)│
        │ ✅ Similarity search (Jaccard)  │
        │ ✅ Shortest paths (weighted)    │
        │ ✅ Vector embeddings + ANN      │
        │ ✅ Sub-second query times       │
        │ ❌ Batch/snapshot-based (lag)  │
        │ ❌ Not for real-time writes     │
        └─────────────────────────────────┘
                    ↓
        Results (to S3 or Neptune DB)
```

**Same question:** Find all 10+ fraud rings?
- Neptune Analytics has Louvain built-in
- Just call: `CALL neptune.algo.louvain.mutate(...)`
- Performance: seconds (not minutes)

**Analytics query:**
```cypher
CALL neptune.algo.louvain.mutate({
  edgeLabels: ['RELATED_TO'],
  edgeWeightProperty: 'riskWeight',
  writeProperty: 'communityId'
}) YIELD success, communityCount, modularity

// That's it. Done.
// Result: 47 fraud communities detected
```

---

## Key Insight: Neptune Analytics is a SEPARATE Product

This is confusing but important:

### Neptune DB (what you have now)
- **Store:** Live property graph
- **Query Language:** Gremlin, openCypher, SPARQL
- **Use for:** Real-time queries, writes, rule-based fraud detection
- **Latency:** Sub-100ms
- **Scaling:** Per-request, auto-scale
- **Cost:** Per instance-hour + I/O

### Neptune Analytics (what you add)
- **Store:** In-memory snapshot of the graph
- **Query Language:** openCypher + algorithm procedures (`CALL neptune.algo.*`)
- **Use for:** Batch/near-real-time algorithms, community detection, ML
- **Latency:** Sub-second
- **Scaling:** Fixed m-NCU capacity (can pause to save 90%)
- **Cost:** Per m-NCU per hour (pause when idle)

**Mental model:**
```
Neptune DB = OLTP database (live, transactional)
Neptune Analytics = OLAP engine (batch, analytical)
```

---

## How It Works: Step-by-Step

### Step 1: Snapshot Creation

```
┌──────────────────────────────────────┐
│ Neptune DB                           │
│ (Production graph, 4M vertices)      │
│                                      │
│ ├─ Member nodes (350K)               │
│ ├─ Agency nodes (5K)                 │
│ ├─ Device nodes (20K)                │
│ ├─ LOGGED_IN edges (3M)              │
│ ├─ RELATED_TO edges (50K)            │
│ └─ ... more nodes/edges              │
└──────────────────────────────────────┘
           ↓ [create snapshot]
     takes 5-15 minutes
           ↓
      Stored in S3
```

**Command:**
```bash
aws neptune create-db-cluster-snapshot \
  --db-cluster-identifier neptune-dev-cluster \
  --db-cluster-snapshot-identifier synapse-snapshot-20260625
```

---

### Step 2: Load Snapshot into Analytics

```
           S3 Snapshot
                ↓ [create-graph from snapshot]
                ↓ takes 10-30 minutes
                ↓
    Neptune Analytics Cluster
    ├─ Loads entire graph into RAM
    ├─ 32 m-NCU = ~32 GB capacity
    ├─ In-memory graph data structure
    └─ Ready for algorithms
```

**Command:**
```bash
aws neptune-analytics create-graph \
  --graph-name synapse-fraud-analytics \
  --source-snapshot-id synapse-snapshot-20260625
```

**What's actually happening:**
- AWS allocates 32 GB of RAM (32 m-NCUs)
- Downloads snapshot from S3
- Loads graph into memory (in-memory index)
- Indexes all nodes/edges for fast traversal
- Ready for queries

---

### Step 3: Run an Algorithm

```
┌────────────────────────────────────────────────┐
│   Neptune Analytics (in-memory)                │
│                                                │
│  Nodes:                                        │
│  ├─ Agency[1], Agency[2], ..., Agency[5K]    │
│  ├─ Member[1], Member[2], ..., Member[350K]  │
│  ├─ Device[1], Device[2], ..., Device[20K]   │
│  │                                            │
│  Edges (in-memory index):                     │
│  ├─ Agency[1] →RELATED_TO→ Agency[2] (wt: 0.8)│
│  ├─ Agency[1] →RELATED_TO→ Agency[3] (wt: 0.6)│
│  ├─ Agency[2] →RELATED_TO→ Agency[3] (wt: 0.7)│
│  │                                            │
│  (All edges indexed for O(1) traversal)      │
│                                                │
└────────────────────────────────────────────────┘
                    ↓
      CALL neptune.algo.louvain.mutate({
        edgeLabels: ['RELATED_TO'],
        edgeWeightProperty: 'riskWeight',
        writeProperty: 'communityId'
      })
                    ↓
         (Algorithm runs in-memory)
         (finds dense clusters)
         (computes modularity)
                    ↓
         Stamps result on nodes:
         Agency[1].communityId = 1
         Agency[2].communityId = 1
         Agency[3].communityId = 1
         Agency[4].communityId = 2
         ...
```

**What Louvain does (in human terms):**
1. Start with each node in its own community
2. Move nodes between communities if it improves **modularity** (density within community vs between)
3. Repeat until convergence
4. Result: 47 communities found
5. Modularity score: 0.82 (0.7+ is good; 0.3-0.5 is weak)

**Performance:**
- Input: 350K members, 5K agencies, 50K RELATED_TO edges
- Time: ~30-60 seconds
- Why fast? In-memory graph; no disk I/O; no network latency

---

### Step 4: Query Results

```
┌────────────────────────────────────────────┐
│   Neptune Analytics (now has results)       │
│                                            │
│   Node properties (after Louvain):         │
│   Agency[1]:                               │
│     ├─ agencyId: "AGENCY_001"              │
│     ├─ name: "TBOAgency Corp"              │
│     └─ communityId: 1  ← NEW               │
│                                            │
│   Agency[2]:                               │
│     ├─ agencyId: "AGENCY_042"              │
│     ├─ name: "Risky Partners Inc"          │
│     └─ communityId: 1  ← NEW (same!)       │
│                                            │
│   Agency[4]:                               │
│     ├─ agencyId: "AGENCY_777"              │
│     ├─ name: "Clean Agency Ltd"            │
│     └─ communityId: 2  ← NEW (different)   │
│                                            │
└────────────────────────────────────────────┘
                    ↓
      MATCH (n:Agency) WHERE n.communityId = 1
      RETURN n.agencyId, n.name
                    ↓
         Query results (instant):
         AGENCY_001 | TBOAgency Corp
         AGENCY_042 | Risky Partners Inc
         AGENCY_015 | Shady Operations
         ... (47 agencies in community 1)
```

---

### Step 5: Export Results

```
┌────────────────────────────────────────────┐
│  Neptune Analytics Results                  │
│                                            │
│  ├─ communityId on each Agency node        │
│  ├─ pageRankScore on each Member node      │
│  └─ inDegree on each Device node           │
│                                            │
│                                            │
└────────────────────────────────────────────┘
                    ↓ [export]
                    ↓
┌────────────────────────────────────────────┐
│  S3 (Parquet files)                         │
│                                            │
│  ├─ fraud_agencies.parquet                 │
│  │   (agencyId, communityId, pageRankScore)│
│  │                                        │
│  ├─ fraud_members.parquet                  │
│  │   (memberId, pageRankScore, inDegree)  │
│  │                                        │
│  └─ fraud_devices.parquet                  │
│      (deviceId, inDegree, agencyCount)    │
│                                            │
└────────────────────────────────────────────┘
```

---

### Step 6: Optional — Write Back to Neptune DB

```
Parquet Results (S3)
        ↓
    [Upsert]
        ↓
Neptune DB nodes now have:
  Agency[1]:
    ├─ agencyId: "AGENCY_001"
    ├─ name: "TBOAgency Corp"
    ├─ communityId: 1       ← stamped
    └─ pageRankScore: 0.082 ← stamped

Now Gremlin queries can use these properties!
Example:
  g.V().hasLabel('Agency')
    .has('communityId', 1)
    .has('pageRankScore', gt(0.05))
    // Finds high-risk agencies in fraud ring 1
```

---

## Timeline: What Happens When?

### T = 0: Start
```bash
python3 analytics_runner.py --cluster neptune-dev-cluster
```

### T = 5-15 min: Snapshot Creation
```
Neptune DB → Snapshot written to S3
Status: creating
```

### T = 15-45 min: Analytics Graph Creation
```
S3 Snapshot → Analytics cluster loading
Status: CREATING
```

### T = 45-60 min: Ready
```
Analytics graph fully loaded
Status: AVAILABLE
Can now run queries & algorithms
```

### T = 60-65 min: Run Louvain
```
CALL neptune.algo.louvain.mutate(...)
Status: running (30-60 seconds)
```

### T = 65-70 min: Run PageRank
```
CALL neptune.algo.pageRank.mutate(...)
Status: running (30-60 seconds)
```

### T = 70-75 min: Query Results
```
MATCH (n) WHERE n.communityId IS NOT NULL ...
Status: results in <100ms
```

### T = 75-80 min: Export & Pause
```
Export to S3, pause Analytics graph
Status: PAUSING → PAUSED (saves 90% cost)
```

---

## Cost Breakdown

### Storage
- Neptune DB: ~$10/month (storage only, if small)
- Snapshots: $0.05 per GB per month
- Analytics graph: 0 (stored in memory, not disk)

### Compute
- Neptune DB running 24/7: $50-500/month (depends on instance type)
- Analytics running (32 m-NCU): $230/month (hourly)
- Analytics paused: $23/month (90% savings!)

### Optimization
```
Strategy: Run Analytics for batch jobs only

Monday 9am:
  ├─ Resume Analytics (2 min)
  ├─ Run algorithms (10 min)
  ├─ Query results (5 min)
  ├─ Export to S3 (5 min)
  └─ Pause Analytics (2 min)
      Cost: ~$0.40 per job

vs

Analytics always on:
  Cost: $230/month = $7.67/day
```

**Recommendation:** Run daily batch (10 min/day) → **save $215/month**

---

## Algorithms Explained (Layman's Terms)

### Louvain (Community Detection)

**What it does:** Finds groups of nodes that are tightly connected to each other but loosely connected to other groups.

**Fraud application:** Agencies sharing devices (Pattern #1, #5)
```
Before Louvain:
  5000 agencies, 50K relationships
  → Which ones are colluding together?

After Louvain:
  47 communities detected
  Community 1: [AGENCY_001, AGENCY_042, AGENCY_015] (47 shared devices)
  Community 2: [AGENCY_500, AGENCY_600] (8 shared devices)
  → Now obvious which agencies ring together
```

**Time:** 30-60 seconds on 350K members, 5K agencies

---

### PageRank (Centrality)

**What it does:** Ranks nodes by importance. Important nodes are those connected to other important nodes.

**Fraud application:** Ringleaders (Pattern #10)
```
Before PageRank:
  350K members, 3M LOGGED_IN edges
  → Who's central to the fraud network?

After PageRank:
  Member[5678]: score 0.082 (top ringleader)
  Device[abc]: score 0.071 (super-shared proxy)
  Agency[001]: score 0.065 (central to ring)
  → These are the hubs; focus on them
```

**Time:** 30-60 seconds

---

### Degree Centrality

**What it does:** Counts incident edges on each node.

**Fraud application:** Super-shared artifacts (Pattern #1, #3, #13)
```
Before Degree:
  20K devices, millions of LOGGED_IN edges
  → Which devices are used by the most people?

After Degree:
  Device[proxy-1]: inDegree = 487 (used by 45 members across 12 agencies!)
  Device[proxy-2]: inDegree = 234 (used by 22 members across 8 agencies)
  Device[normal]: inDegree = 3 (used by 3 members in same agency)
  → Proxy farms are obvious; normal devices are obvious
```

**Time:** 10 seconds

---

## Why Not Just Use SQL?

### Find fraud rings in PostgreSQL

```sql
WITH RECURSIVE agency_edges AS (
  SELECT source, target, weight FROM relationships
  WHERE source < target  -- avoid duplicates
),
dfs_tree AS (
  SELECT source, target, weight, ARRAY[source, target] AS path FROM agency_edges
  UNION ALL
  SELECT a.source, b.target, a.weight + b.weight,
         path || b.target
  FROM dfs_tree a
  JOIN agency_edges b ON a.target = b.source
  WHERE NOT b.source = ANY(path)  -- no cycles
    AND array_length(path, 1) < 5  -- max depth 5
)
SELECT path, SUM(weight) AS total_weight
FROM dfs_tree
WHERE path[1] = ANY(path[array_length(path, 1)])  -- cycle detected
GROUP BY path
ORDER BY total_weight DESC;
```

**Problems:**
1. **Complex logic:** Hard to write, hard to debug
2. **Slow:** Recursive CTE materializes intermediate results at each level
3. **Limited:** Only finds paths following declared hierarchy; misses cross-agency rings
4. **Rigid:** Each new relationship type requires a different query

### Find fraud rings in Neptune Analytics

```cypher
CALL neptune.algo.louvain.mutate({ edgeLabels: ['RELATED_TO'], ... })
```

**Advantages:**
1. **Simple:** One-liner
2. **Fast:** In-memory graph traversal
3. **Comprehensive:** Works on all edges regardless of type
4. **Flexible:** Add new edge labels and it still works

---

## When to Use Neptune Analytics

✅ **Use Analytics when:**
- You need whole-graph algorithms (community detection, centrality ranking)
- You're doing batch/near-real-time processing (daily/hourly)
- You want sub-second query times on analytical patterns
- You're comfortable with snapshot lag (hours old vs live)

❌ **Don't use Analytics when:**
- You need real-time per-request queries (use Neptune DB Gremlin instead)
- You're doing transactional writes (use Neptune DB)
- Your graph is <1M nodes (overhead not worth it)
- You need <100ms latency (Analytics is sub-second, not sub-100ms)

---

## Next: Actually Doing It

Follow `ANALYTICS_QUICK_START.md` to:
1. Create snapshot
2. Create Analytics graph
3. Run Louvain → get communities
4. Run PageRank → get hubs
5. Run Degree → get super-shared artifacts
6. Export report
7. Pause graph

**Estimated time:** 2-3 hours end-to-end (mostly waiting for infrastructure)

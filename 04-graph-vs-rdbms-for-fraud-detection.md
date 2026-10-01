# Graph vs RDBMS for Fraud Detection — Why Neptune Wins Here

This document makes the case for Amazon Neptune (property graph) as the fraud detection layer
over the existing relational DB, using concrete examples from this system's schema and fraud patterns.

---

## The Core Problem with RDBMS for Fraud Detection

Relational databases are optimized for **predefined, structured queries on isolated entities**.
Fraud detection is the opposite: it requires **ad-hoc traversal across deeply connected entities**
where the connection itself is the signal.

The fundamental mismatch is this:

> In a relational DB, relationships are implicit — encoded as foreign keys and discovered at query time via JOINs.
> In a graph, relationships are first-class citizens — stored explicitly as edges and traversed in constant time regardless of data size.

---

## Side-by-Side: The Same Fraud Query in SQL vs Gremlin

### Example 1 — Find all agencies whose members share a device with members of another agency

**SQL:**
```sql
SELECT DISTINCT
    a1.agency_id, a1.name,
    a2.agency_id, a2.name,
    lh.device_id
FROM login_history lh
JOIN member m1 ON lh.member_id = m1.member_id
JOIN agency a1 ON m1.agency_id = a1.agency_id
JOIN login_history lh2 ON lh.device_id = lh2.device_id
    AND lh2.member_id != lh.member_id
JOIN member m2 ON lh2.member_id = m2.member_id
JOIN agency a2 ON m2.agency_id = a2.agency_id
WHERE a1.agency_id != a2.agency_id;
```

**Execution cost:** 3 self-joins across `login_history` (tens of millions of rows) + 2 member joins + 2 agency joins.
At 10M login rows, this query will full-scan `login_history` twice and produce a massive intermediate result set.
Query time: **minutes** without aggressive indexing, still **seconds** with it.

**Gremlin (Neptune):**
```groovy
g.V().hasLabel('Device')
  .where(__.in('LOGGED_IN').out('BELONGS_TO').dedup().count().is(gt(1)))
  .in('LOGGED_IN').out('BELONGS_TO').dedup()
  .valueMap('agencyId', 'name')
```

**Execution cost:** Neptune traverses from Device vertices (hub) outward — no joins, no table scans.
Each hop is a pointer follow in the adjacency list. Query time: **milliseconds** regardless of login volume,
because we are not scanning login rows — we are walking edges.

---

### Example 2 — Detect agency relationship cycles (collusion rings)

**SQL:**
```sql
-- PostgreSQL recursive CTE — cycle detection up to depth 4
WITH RECURSIVE agency_path AS (
    SELECT agency_id, parent_agency_id, ARRAY[agency_id] AS path, false AS cycle
    FROM agency
    WHERE parent_agency_id IS NOT NULL
    UNION ALL
    SELECT a.agency_id, a.parent_agency_id,
           path || a.agency_id,
           a.agency_id = ANY(path)
    FROM agency a
    JOIN agency_path ap ON a.parent_agency_id = ap.agency_id
    WHERE NOT cycle
      AND cardinality(path) < 5
)
SELECT path FROM agency_path WHERE cycle = true;
```

**Problems:**
- Recursive CTEs in PostgreSQL/MySQL are not optimized for graph traversal — they materialize
  intermediate results at every recursion level.
- This only traverses the `parent_agency_id` self-join, meaning it misses cross-agency relationships
  that don't follow a strict tree (e.g., consortium loops, GSA circular appointments).
- Adding a second relationship type (e.g., consortium linkage) requires a separate table and a
  completely different CTE or a UNION — query complexity grows multiplicatively.

**Gremlin:**
```groovy
g.V().hasLabel('Agency').as('start')
  .repeat(__.both('RELATED_TO').simplePath())
  .times(4)
  .where(__.both('RELATED_TO').as('start'))
  .path().by(valueMap('agencyId', 'name', 'agencyType'))
```

- Traverses all relationship types (HEAD_OFFICE, BRANCH_GSA, CONSORTIUM, etc.) in one query.
- `simplePath()` prevents re-visiting vertices — cycle detection is built into the traversal model.
- Adding a new relationship type requires no query change — it's a new edge label, same traversal.

---

### Example 3 — Risk propagation through agency hierarchy

**SQL:**
There is no clean way to do weighted risk propagation through a hierarchy in SQL without:
1. A recursive CTE that re-queries the table at each level, OR
2. A stored procedure that loops and accumulates state, OR
3. Pre-materializing the hierarchy into a closure table (expensive to maintain at scale)

Even with a closure table, you cannot encode **per-edge propagation rules** (`BIDIRECTIONAL`, `UP_ONLY`, `NONE`)
without adding another join per edge traversed.

**Gremlin:**
```groovy
g.V('AGENCY_ID')
  .repeat(
    __.bothE('RELATED_TO')
      .has('propagationRule', within('BIDIRECTIONAL', 'UP_ONLY'))
      .otherV().simplePath()
  )
  .until(__.cyclicPath().or().loops().is(5))
  .dedup()
  .valueMap('agencyId', 'name')
```

The `propagationRule` property on each edge is read **during traversal** — the graph engine decides
at each hop whether to continue or stop. This is architecturally impossible to replicate in SQL
without pre-materializing the decision into a lookup table and re-joining at every level.

---

## Full Pros and Cons

### Graph Database (Neptune) — Pros

| Pro | Detail |
|-----|--------|
| **Relationship traversal is O(1) per hop** | Following an edge in a property graph is a pointer dereference, not a table scan. Performance does not degrade as the dataset grows — only the number of connections to a vertex matters, not the total table size. |
| **Multi-hop queries are natural** | "Find all members who share a device with a member of an agency related to a flagged agency" is 4 hops in Gremlin. In SQL it is 4–5 JOINs on large tables with compounding intermediate result sets. |
| **Shared artifact detection is a single hop** | Promoting Device, IpAddress, StepUpRule to hub vertices means "who else used this device" is `.in('LOGGED_IN')` — one traversal step. In SQL it is a self-join on `login_history` with a full table scan. |
| **Schema is additive, not destructive** | Adding a new fraud signal (e.g., email domain as a hub vertex) means adding new vertices and edges. No `ALTER TABLE`, no migration, no downtime. Existing queries are unaffected. |
| **Heterogeneous data sources merge naturally** | OpenSearch auth events and relational DB bookings both write to the same Device/IpAddress hub vertices. The graph unifies them. In SQL, cross-source joins require either ETL into one schema or federated queries — both are expensive to maintain. |
| **Cycle detection is built-in** | Gremlin's `simplePath()` and `cyclicPath()` are first-class operators. SQL requires recursive CTEs or application-level loops. |
| **Per-edge metadata enables configurable behaviour** | `riskWeight` and `propagationRule` on each `RELATED_TO` edge lets traversal logic vary per relationship type without any additional table lookups. |
| **Real-time subgraph queries** | Neptune supports real-time Gremlin queries on live data. Fraud signals (device sharing, IP clustering) can be evaluated at the moment of a login or booking request. |

---

### Graph Database (Neptune) — Cons

| Con | Detail |
|-----|--------|
| **No transactional writes** | Neptune supports ACID transactions, but they are less battle-tested and tooled than PostgreSQL/MySQL for high-volume operational writes. This is why we keep the relational DB as source of truth and use Neptune as a read layer. |
| **Aggregation queries are weaker** | "Total amount booked by all members of flagged agencies in Q3" is a trivial SQL GROUP BY. In Gremlin it requires traversal + in-memory aggregation, which is slower and more verbose. OLAP-style reporting stays in the relational DB. |
| **No rich ad-hoc querying without Gremlin knowledge** | SQL is universally known. Gremlin has a steeper learning curve and fewer tools (no equivalent of pgAdmin, DBeaver, etc. with full Gremlin support). |
| **ETL pipeline adds operational complexity** | Every relational DB write and OpenSearch event must be propagated to Neptune via CDC pipeline. This is an additional system to monitor, tune, and recover from failures. |
| **Index tuning is less transparent** | Neptune's indexing model (SPO, OSP, etc.) is managed internally. You have less control over query plans compared to EXPLAIN ANALYZE in PostgreSQL. |
| **Cost at scale** | Neptune charges per instance-hour and I/O. At tens of millions of edges, storage and query costs can be significant. Neptune Serverless mitigates this for intermittent fraud query workloads. |
| **Eventual consistency from ETL lag** | Neptune lags the relational DB by seconds (Kinesis pipeline). A booking created 1 second ago may not be in the graph yet. For real-time fraud checks at booking creation time, this window must be accounted for. |

---

### Relational DB (PostgreSQL/MySQL) — Where It Still Wins

| Use case | Why RDBMS is better |
|----------|---------------------|
| Transactional writes (bookings, ledger updates) | ACID guarantees, mature tooling, row-level locking |
| OLAP / reporting queries | GROUP BY, window functions, aggregations on large datasets are faster in columnar or relational engines |
| Simple entity lookups by primary key | Single-row fetch by ID is O(1) in a B-tree index — same as Neptune, but with better tooling |
| Audit trails and compliance | Row-level history (triggers, temporal tables) is well-supported in relational DBs |
| Cross-table aggregations | Sum of ledger balances by country, booking counts by agency tier — SQL is purpose-built for this |

---

## Decision Summary

| Dimension | RDBMS | Neptune (Graph) |
|-----------|-------|-----------------|
| Relationship traversal (2–5 hops) | Slow (multi-join) | Fast (pointer walk) |
| Shared artifact detection | Table scan + self-join | Single-hop from hub vertex |
| Cycle detection | Recursive CTE, complex | Built-in (`simplePath`) |
| Per-edge conditional logic | Multiple JOINs + CASE | Edge property read during traversal |
| Cross-source data unification | Federated query / ETL | Natural (same hub vertices) |
| Aggregation / reporting | Excellent | Weak |
| Schema evolution | ALTER TABLE migration | Additive, no migration |
| Operational writes | Excellent | Not designed for this |
| Tooling maturity | Excellent | Moderate |
| Learning curve | Low (SQL) | Higher (Gremlin) |

**The verdict:** Use the relational DB for what it is good at — writes, reporting, and entity storage.
Use Neptune for what it is good at — traversal, correlation, and fraud pattern detection.
The two are not competitors in this system; they are complements with distinct roles.

---

## Concrete Performance Estimates at System Scale

Assuming: 500K members, 5K agencies, 50M login events, 10M bookings, 5M auth events.

| Query | SQL (estimated) | Neptune (estimated) |
|-------|-----------------|---------------------|
| Shared device across agencies (specific device) | 200–800ms (index scan on login_history) | 5–20ms (hub vertex traversal) |
| All shared devices across all agencies | 30–120s (full scan) | 2–10s (fan-out from Device vertices) |
| Agency cycle detection (depth 4) | 5–30s (recursive CTE) | 100–500ms (Gremlin repeat) |
| Risk propagation from 1 flagged agency | 2–10s (recursive CTE + joins) | 50–200ms (bounded repeat traversal) |
| Impossible travel (single member) | 50–200ms (index on member_id + loginAt) | 30–100ms (comparable) |
| Impossible travel (all members) | 10–60min (full scan) | 1–5min (vertex-parallel traversal) |
| Failed auth cluster on a device | N/A (auth in OpenSearch, not joined) | 10–50ms (hub vertex traversal) |
| Cross-agency step-up rule correlation | N/A (rules in OpenSearch JSON, not queryable as relations) | 20–100ms (StepUpRule hub vertex) |

> Estimates assume Neptune r5.4xlarge (128GB RAM) with warm cache, and PostgreSQL with standard B-tree indexes.
> Actual performance will vary; benchmark against your data before production sizing.

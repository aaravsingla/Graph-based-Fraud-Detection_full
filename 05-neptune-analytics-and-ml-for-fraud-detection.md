# Neptune Analytics & ML for Fraud Detection — Research

**Date:** 2026-06-11
**Status:** Research / evaluation (answers the open question in `[2026-04-29-fraud-detection-knowledge-graph-design.md](./superpowers/specs/2026-04-29-fraud-detection-knowledge-graph-design.md)` §6: *"Evaluate Neptune Analytics (openCypher + graph algorithms) for community detection on agency rings"*)
**Scope:** What ML, algorithms, and techniques can run on the Synapse fraud graph **after** it is deployed on Neptune, and exactly what Neptune Analytics gives us for TBO.

> All examples are written against the live Synapse schema in
> `[docs/diagrams/03-full-graph-schema.md](./diagrams/03-full-graph-schema.md)`
> (19 vertex labels, 27 edge labels) and the 15 fraud patterns in the
> [design spec](./superpowers/specs/2026-04-29-fraud-detection-knowledge-graph-design.md).

---

## 1. The big picture — three detection layers, not one

Fraud detection on a graph is not a single technique. It is a stack of three complementary layers, each with a different latency, cost, and "intelligence" profile. The current Synapse implementation only uses Layer 1.


| Layer                            | What it is                                                                                                            | Engine                                                           | Latency                                   | Catches                              | Status in Synapse                                                                                 |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- | ----------------------------------------- | ------------------------------------ | ------------------------------------------------------------------------------------------------- |
| **1. Rule / pattern queries**    | Hand-written Gremlin/openCypher traversals encoding known fraud shapes (shared device, impossible travel, etc.)       | **Neptune Database** (Gremlin)                                   | ms (real-time)                            | *Known* patterns you can describe    | ✅ Built — `[docs/queries/03-fraud-detection-queries.md](./queries/03-fraud-detection-queries.md)` |
| **2. Graph algorithms**          | Whole-graph algorithms (community detection, centrality, similarity) that surface *structure* no single query encodes | **Neptune Analytics** (openCypher `CALL neptune.algo.*`)         | seconds–minutes (batch/near-real-time)    | *Unknown* rings, hubs, look-alikes   | ❌ Not yet — this doc                                                                              |
| **3. Graph ML (GNN/embeddings)** | Models that *learn* fraud from labelled history and predict risk on new/changed nodes                                 | **Neptune ML** (SageMaker + DGL) and/or graph features → XGBoost | training: hours; inference: ms–sub-second | *Novel/adaptive* fraud, soft signals | ❌ Not yet — this doc                                                                              |


**Key architectural fact (2026):** Neptune is now two separate products that share a data model:

- **Neptune Database** — the OLTP graph store. Supports **Gremlin + openCypher + SPARQL**, Multi-AZ, up to ~100k queries/sec, serverless auto-scaling. This is what Synapse loads today via the CSV bulk loader / Gremlin upserts. Best for Layer 1 real-time pattern queries.
- **Neptune Analytics** — a *separate*, in-memory, memory-optimized OLAP engine. Supports **openCypher only**, ships **25+ built-in graph algorithms** + **native vector search**. You **load a snapshot** of the graph into it (from a Neptune DB endpoint or from S3 Parquet/CSV), run algorithms, write results back. Billed in **m-NCUs** (1 m-NCU ≈ 1 GB RAM + compute/hr); **no scale-to-zero** but **pause = ~10% cost**.
- **Neptune ML** — not a third store; it's a *feature* of Neptune Database that exports the graph, trains a **GNN (DGL) on SageMaker**, and lets you query predictions inline from Gremlin/openCypher.

> Mental model: **Neptune DB answers "does this exact shape exist right now?"** · **Neptune Analytics answers "what is the hidden structure of the whole graph?"** · **Neptune ML answers "given everything we've seen, how risky is this node?"**

---

## 2. Neptune Analytics — full capability map for Synapse

Neptune Analytics exposes algorithms as openCypher procedures: `CALL neptune.algo.<name>(...) YIELD ...`. Every algorithm has a read form (returns results) and most have a `**.mutate`** form (writes the result back as a node/edge property, so downstream queries and ML can use it). Below is the **complete catalog** mapped to TBO fraud use cases.

### 2.1 Community detection / clustering — *the headline use case (fraud rings)*

This is the single biggest reason to adopt Neptune Analytics for Synapse. Rule queries find rings *you already described* (e.g. `RELATED_TO` cycles). Community detection finds rings **you didn't know to look for** — clusters that are far denser internally than chance would predict.


| Algorithm                                                        | What it does                                                                               | Synapse fraud application                                                                                                                                                                                                                                                                                    |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `neptune.algo.louvain` (+ `.mutate`)                             | Modularity-optimizing hierarchical communities; weighted or unweighted                     | **Agency collusion rings (pattern 5).** Run over `RELATED_TO` (weighted by `riskWeight`) → agencies that cluster together beyond their declared hierarchy. Run over the *shared-artifact* graph (Members linked through shared `Device`/`IpAddress`/`Address`) → fraud rings that span "unrelated" agencies. |
| `neptune.algo.wcc` (Weakly Connected Components) (+ `.mutate`)   | Groups all nodes reachable ignoring edge direction                                         | **Identity clusters / "one operator, many accounts".** Project Members ↔ shared Device/IP/Address and label each connected blob with a `clusterId`. A WCC component with many Members but few real humans = synthetic-identity ring. Cheap first pass before Louvain.                                        |
| `neptune.algo.labelPropagation` (+ `.mutate`)                    | Fast community detection by iterative label spreading; also semi-supervised classification | **Risk label spreading.** Seed known-fraud Members/Agencies with a label; propagate across the graph to find guilt-by-association. Cheaper than a GNN for a first cut.                                                                                                                                       |
| `neptune.algo.scc` (Strongly Connected Components) (+ `.mutate`) | Components where every node reaches every other following edge direction                   | **Circular collusion** in directed flows (e.g. `UPDATED_LEDGER` / `RELATED_TO` directed cycles) — money/credit moving in a loop.                                                                                                                                                                             |


Example — collusion rings over the declared agency hierarchy, weighted by risk:

```cypher
CALL neptune.algo.louvain.mutate({
  edgeLabels: ['RELATED_TO'],
  edgeWeightProperty: 'riskWeight',
  writeProperty: 'agencyCommunity'
}) YIELD success
RETURN success
```

Example — find suspicious communities that mix *unrelated* agencies sharing devices (the pattern SQL can never see):

```cypher
// After running Louvain/WCC.mutate to stamp `community` on Member nodes
MATCH (m:Member)-[:BELONGS_TO]->(a:Agency)
WITH m.community AS community,
     count(DISTINCT a)        AS agencyCount,
     count(DISTINCT m)        AS memberCount,
     collect(DISTINCT a.agencyId) AS agencies
WHERE agencyCount > 3            // one community spanning many agencies = red flag
RETURN community, agencyCount, memberCount, agencies
ORDER BY agencyCount DESC
```

### 2.2 Centrality — *find the kingpins and super-hubs*


| Algorithm                                                                     | What it does                                                  | Synapse fraud application                                                                                                                                                                                                                                         |
| ----------------------------------------------------------------------------- | ------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `neptune.algo.degree` (+ `.mutate`)                                           | Count of incident edges                                       | **Super-shared artifacts.** A `Device` or `IpAddress` with abnormally high in-degree on `LOGGED_IN`/`ACCESSED_FROM` = proxy farm / device emulator (patterns 1, 3, 13). Stamp `useDegree` then threshold.                                                         |
| `neptune.algo.pageRank` (+ `.mutate`, supports a `**personalization*`* param) | Importance weighted by importance of neighbours               | **Ringleader detection.** Run on the Member/Device/IP correlation graph → high PageRank nodes are the structural centre of a ring. **Personalized PageRank** seeded from *known-fraud* nodes = "risk diffusion": rank every node by proximity to confirmed fraud. |
| `neptune.algo.closenessCentrality` (+ `.mutate`)                              | How close a node is to all others (BFS-sampled, `numSources`) | **Bridge accounts** that connect otherwise separate agency clusters — mule/relay members.                                                                                                                                                                         |


Example — personalized PageRank as a graph-native risk score seeded from known-bad members:

```cypher
MATCH (seed:Member {status: 'FRAUD_CONFIRMED'})
CALL neptune.algo.pageRank.mutate({
  edgeLabels: ['LOGGED_IN','ACCESSED_FROM','CREATED_BOOKING','BELONGS_TO'],
  sourceNodes: collect(seed),       // personalization seeds
  writeProperty: 'fraudProximityScore'
}) YIELD success
RETURN success
```

### 2.3 Similarity — *look-alike / synthetic-identity detection*


| Algorithm                                                        | What it does                       | Synapse fraud application                                                                                                                                         |
| ---------------------------------------------------------------- | ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `neptune.algo.jaccardSimilarity`                                 | |A∩B| / |A∪B| over neighbour sets  | **Near-duplicate members/agencies.** Two Members sharing most of their Device/IP/Address neighbourhood but in different agencies = same actor, multiple accounts. |
| `neptune.algo.overlapSimilarity`                                 | |A∩B| / min(|A|,|B|)               | Detects a *small* new account whose entire footprint is a subset of an established one — account-cloning.                                                         |
| `neptune.algo.neighbors.common` / `neptune.algo.neighbors.total` | Count of shared / total neighbours | Cheap building block for "how many artifacts do these two members share" without computing full similarity.                                                       |


### 2.4 Path finding — *connect-the-dots investigations*


| Algorithm                                                                       | What it does                                           | Synapse fraud application                                                                                                                          |
| ------------------------------------------------------------------------------- | ------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `neptune.algo.bfs`, `bfs.levels`                                                | Breadth-first reachability / hop distance              | **"How is flagged member A connected to flagged member B?"** — investigator tooling; blast-radius of a confirmed fraud node.                       |
| `neptune.algo.sssp.bellmanFord(.path)`, `sssp.deltaStepping(.path)`, `topksssp` | Weighted shortest paths                                | **Risk-propagation paths** through `RELATED_TO` using `` as edge weight (pattern 7) — the shortest "risk path" from a clean agency to a dirty one. |
| `neptune.algo.egonet`                                                           | Extract the n-hop neighbourhood subgraph around a node | **Investigation subgraph extraction** and — importantly — **the exact subgraph-extraction step needed for real-time GNN inference** (see §3.4).    |


### 2.5 Vector similarity search (HNSW) — *embeddings inside the graph*

Neptune Analytics stores **vector embeddings as node properties** and runs approximate-nearest-neighbour search (HNSW, cosine/euclidean):

- `neptune.algo.vectors.topK.byEmbedding(<vec>, {topK})` — given a query vector, find nearest nodes.
- `neptune.algo.vectors.topK.byNode(<node>, {topK})` — given a node, find similar nodes.
- `neptune.algo.vectors.distance.byNode/byEmbedding` — pairwise distance.

**Synapse application:** generate an embedding per Member/Device that encodes its behavioural footprint (e.g. from the GNN in §3, or from `node2vec`-style features), store it on the node, then "**find the 20 accounts most similar to this confirmed fraudster**" becomes one ANN call combined with a graph traversal for context. This is also the backbone of a **GraphRAG** fraud-investigation assistant (LLM + graph context).

### 2.6 Miscellaneous

`neptune.algo.degreeDistribution`, property-graph summary/schema procedures — useful for **data-quality and drift monitoring** of the graph before you trust any algorithm output.

### 2.7 Operational shape of a Neptune Analytics job

```
Neptune DB (live graph)
   └─ snapshot / export ──▶ Neptune Analytics graph (in-memory, m-NCUs)
                               ├─ CALL neptune.algo.louvain.mutate(...)      // stamp communities
                               ├─ CALL neptune.algo.pageRank.mutate(...)     // stamp risk
                               ├─ CALL neptune.algo.degree.mutate(...)       // stamp hub-ness
                               └─ export results ──▶ S3 (Parquet/CSV) ──▶ fraud-score store / alerts
```

Run it as a **scheduled batch** (e.g. hourly/daily), **pause the graph between runs** (~10% cost), and write the stamped properties (`agencyCommunity`, `fraudProximityScore`, `useDegree`) back into Neptune DB so Layer-1 real-time queries can use them. Supports CSV **and Parquet** import/export to/from S3.

---

## 3. Neptune ML — graph machine learning for Synapse

Neptune ML uses **Graph Neural Networks** via the **Deep Graph Library (DGL)** trained on **SageMaker**, and you query predictions directly from Gremlin/openCypher. It exists for the fraud you *can't* write a rule for and that pure algorithms only hint at.

### 3.1 Why GNNs beat tabular ML here

A classic XGBoost model sees each Member/booking as an isolated row. A GNN does three things a flat model cannot:

1. **Relational learning** — it uses *who you are connected to* (agency, devices, IPs, bookings) as signal.
2. **Feature propagation** — it aggregates neighbour features, so a clean-looking account inherits risk from a dirty neighbourhood.
3. **Structural pattern learning** — it learns subgraph shapes that indicate fraud, instead of you enumerating them.

### 3.2 The four task types and how they map to Synapse


| Neptune ML task         | Model family                                      | Synapse fraud use                                                                                                                                                             |
| ----------------------- | ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Node classification** | R-GCN (GNN)                                       | Predict `isFraud` / risk-tier on a **Member**, **Agency**, **Device**, or **HotelItinerary** node. *The primary fraud task.*                                                  |
| **Node regression**     | R-GCN (GNN)                                       | Predict a continuous **fraud score** or expected booking volume; deviation = anomaly.                                                                                         |
| **Link prediction**     | R-GCN **or** KGE (`TransE`, `DistMult`, `RotatE`) | Predict edges that *should* exist but are hidden — e.g. a likely `RELATED_TO` between agencies that behave as one ring but never declared it; or a hidden Member↔Device link. |
| **Edge classification** | R-GCN (GNN)                                       | Classify an existing edge — e.g. is this `LOGGED_IN` / `CREATED_BOOKING` event fraudulent?                                                                                    |


> KGE models (`TransE`/`DistMult`/`RotatE`) are **link-prediction only**; GNN/R-GCN models cover all four tasks. For Synapse's "is this account/booking fraud?" the answer is **node classification with R-GCN**; for "find undeclared agency collusion edges" use **link prediction**.

### 3.3 What the model trains on (feature engineering from the Synapse graph)

Neptune ML auto-extracts features during export, but quality depends on what you put on the nodes/edges. Strong inputs from the current schema:

- **Node properties** already present: `Member.status/createdAt`, `Agency.agencyType/priorityCategory`, `Device.useCount/firstSeen/lastSeen`, `IpAddress.ipThreatIntel/ipCountryCode`, `HotelItinerary.totalAmount/status`, `AuthEvent.isSuccessfulLogin/responseLatency`, `StepUpEvent.`*.
- **Engineered properties to add before training** — *stamp the Neptune Analytics algorithm outputs from §2 onto the nodes first*: `agencyCommunity` (Louvain), `fraudProximityScore` (personalized PageRank), `useDegree` (degree), `clusterId` (WCC). **The two layers compound: algorithm outputs become the GNN's most predictive features.**
- **Labels:** you need a `FraudCase` ground-truth source. The design spec already lists adding a `**FraudCase` vertex** (§6) — that's the label store. Confirmed-fraud Members/Agencies/bookings become the training labels.

### 3.4 Transductive vs inductive — the real-time catch

- **Transductive inference** — predictions are pre-computed at training time for nodes that existed then. Cheap, but **cannot score a brand-new node** (a member who just signed up, a booking just created). Fine for batch re-scoring.
- **Inductive (real-time) inference** — scores **new/changed nodes on the fly** by extracting their n-hop subgraph and running it through the deployed SageMaker endpoint. This is what you need to score a booking **at creation time**.

The real-time GNN flow (matches the AWS reference architecture):

```
booking/login event
   ▶ upsert node+edges into Neptune DB
   ▶ extract n-hop subgraph around the new node   (egonet)
   ▶ pull node/edge features for that subgraph
   ▶ POST subgraph+features to SageMaker GNN endpoint (inductive mode)
   ▶ fraud probability ▶ Adaptive Auth Agent / step-up decision
```

Gremlin inline inference (node classification + confidence), inductive + deterministic:

```groovy
g.with("Neptune#ml.endpoint", "synapse-fraud-nc-ep")
 .with("Neptune#ml.iamRoleArn", "arn:aws:iam::<acct>:role/NeptuneMLRole")
 .V('member-12345')
 .properties("riskTier", "Neptune#ml.score")
   .with("Neptune#ml.classification")
   .with("Neptune#ml.inductiveInference")
   .with("Neptune#ml.deterministic")
```

This plugs **directly** into the existing [Adaptive Auth Agent](./adaptive-auth-agent/01-overview.md): the GNN risk tier becomes another signal node / step-up trigger.

### 3.5 Neptune ML workflow stages

1. **Export** — Neptune exports graph + features to S3 (`neptune-export`).
2. **Data processing** — SageMaker processing job builds the DGL graph + feature encoders.
3. **Training** — SageMaker trains the R-GCN/KGE; **HPO** picks the best model.
4. **Deploy** — model → SageMaker endpoint.
5. **Inference** — Gremlin/openCypher `Neptune#ml.`* predicates (transductive or inductive).
  "Hours, not weeks" is AWS's pitch; realistically budget days for the first iteration incl. label curation.

---

## 4. Techniques beyond the AWS-managed path

You are not limited to managed Neptune ML. Common, well-supported alternatives:


| Technique                              | Tooling                                                                                                             | When to use for Synapse                                                                                                     |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| **Graph features → gradient boosting** | Export Louvain/PageRank/degree/Jaccard features from Neptune Analytics → **XGBoost/LightGBM** on SageMaker          | Fastest path to a *supervised* model; far cheaper/simpler than a GNN; very strong baseline. **Recommended first ML model.** |
| **Node embeddings**                    | `node2vec` / `FastRP` (compute externally or via DGL) → store as vectors in Neptune Analytics for ANN               | Unsupervised "find similar accounts"; feeds vector search (§2.5) and GraphRAG.                                              |
| **Custom GNN (DGL/PyG)**               | DGL or PyTorch Geometric on SageMaker, Neptune only as the graph store                                              | When you outgrow Neptune ML's auto-ML (custom architectures, GraphSAGE, GAT, temporal GNNs).                                |
| **Anomaly / outlier detection**        | Degree-distribution + statistical thresholds; isolation forest on graph features                                    | Unsupervised cold-start before you have enough fraud labels.                                                                |
| **Entity resolution → graph**          | **AWS Entity Resolution** to merge duplicate Members/Agencies by shared PII, then Louvain/WCC on the resolved graph | Directly mirrors AWS's published fraud-ring reference architecture; tightens the "same human, many accounts" signal.        |
| **GraphRAG fraud assistant**           | Neptune Analytics vector search + Bedrock                                                                           | Analyst-facing: "explain why this account is risky" with graph-grounded LLM answers.                                        |


---

## 5. Mapping the 15 Synapse fraud patterns to techniques


| #   | Pattern                             | Best technique                                                                                 | Layer     |
| --- | ----------------------------------- | ---------------------------------------------------------------------------------------------- | --------- |
| 1   | Shared device across agencies       | `degree` + WCC/Louvain on Member–Device graph                                                  | 1 + 2     |
| 2   | Impossible travel (velocity)        | Rule query (time math in app) **+** edge classification (GNN) for soft cases                   | 1 + 3     |
| 3   | Shared IP across agencies           | `degree` + community detection; `ipThreatIntel` as GNN feature                                 | 1 + 2 + 3 |
| 4   | Ghost booking ring                  | Louvain on booking↔device↔agency; node classification on `HotelItinerary`                      | 2 + 3     |
| 5   | **Agency collusion ring**           | **Louvain / SCC on `RELATED_TO` + shared-artifact graph; link prediction for undeclared ties** | 2 + 3     |
| 6   | Address reuse across agencies       | Jaccard/overlap similarity; WCC                                                                | 2         |
| 7   | Risk propagation in hierarchy       | Personalized PageRank / weighted SSSP on `riskWeight`                                          | 2         |
| 8   | Ledger manipulation                 | SCC (circular updates) + node regression on ledger deltas                                      | 2 + 3     |
| 9   | Booking volume spike                | Degree/aggregation + node regression anomaly                                                   | 1 + 3     |
| 10  | Composite fraud score               | **GNN node classification** consuming all algorithm features                                   | 3         |
| 11  | Credential stuffing (failed auths)  | `degree` on AuthEvent↔Device/IP + label propagation                                            | 1 + 2     |
| 12  | Auth method cycling                 | Rule query + edge features into GNN                                                            | 1 + 3     |
| 13  | Bot via response latency            | Degree + node classification using `responseLatency`                                           | 1 + 3     |
| 14  | Cross-agency step-up correlation    | Community detection on `TRIGGERED_RULE` graph                                                  | 2         |
| 15  | Forgot-password abuse (enumeration) | `degree` on AuthEvent(FORGOT_PASSWORD)↔IP + label propagation                                  | 1 + 2     |


**Takeaway:** patterns 5, 4, 6, 7, 8, 14 are *exactly* what Neptune Analytics algorithms are built for; pattern 10 and the adaptive/soft versions of everything else are where Neptune ML earns its cost.

---

## 6. Recommended phased roadmap for TBO

**Phase 0 (done):** Graph in Neptune DB; Layer-1 Gremlin rule queries.

**Phase 1 — Neptune Analytics (highest ROI, low effort):**

- Stand up a Neptune Analytics graph from a Neptune DB snapshot.
- Run `wcc` → `louvain` (weighted by `riskWeight`) for ring detection; `degree` + `pageRank` for hubs/ringleaders; `jaccardSimilarity` for look-alikes.
- `.mutate` results back into Neptune DB as node properties (`community`, `fraudProximityScore`, `hubDegree`).
- Schedule as a batch job; **pause between runs** for ~90% cost saving.
- Deliverable: ring/hotspot dashboard + scores feeding the [Analytics Portal](./analytics-portal/01-overview.md).

**Phase 2 — Supervised baseline (graph features → XGBoost):**

- Stand up the `**FraudCase` vertex** as the label store (already in design backlog).
- Export Phase-1 algorithm features + node properties → XGBoost on SageMaker.
- Cheap, explainable, strong baseline; integrate score into the Adaptive Auth Agent.

**Phase 3 — Neptune ML (GNN):**

- Node classification (R-GCN) for composite risk (pattern 10); link prediction for undeclared collusion (pattern 5).
- **Inductive/real-time inference** at booking/login time → step-up decisions.

**Phase 4 — Embeddings + GraphRAG:**

- Store node embeddings in Neptune Analytics; ANN "similar fraudster" search; Bedrock-backed investigator assistant.

---

## 7. Cost, limits, and watch-outs

- **Neptune Analytics is openCypher-only.** Synapse's Layer-1 queries are written in **Gremlin** — they must be re-expressed in openCypher to run *inside* Analytics (most translate cleanly; some Gremlin traversal steps have no 1:1 openCypher form). Algorithms are `CALL`-based and identical regardless.
- **Two stores, explicit sync.** Analytics does **not** read Neptune DB live — you load a snapshot. Plan for staleness equal to your batch cadence.
- **No scale-to-zero on Analytics.** It's always-on capacity (m-NCUs, min 32/64 m-NCU). **Pause when idle** for ~10% cost. Start small in dev.
- **Latency.** Analytics is **sub-second**, not sub-10ms. Real-time per-request fraud checks stay on Neptune DB (Layer 1) and the GNN endpoint; Analytics is batch/near-real-time.
- **Neptune ML needs labels.** No `FraudCase` ground truth → no supervised model. Build the label pipeline *before* committing to Phase 3.
- **GNN real-time inference is non-trivial** — requires the egonet-extraction + feature-fetch + endpoint-call path and inductive-mode models. Budget engineering time.
- **ETL lag already noted** in `[04-graph-vs-rdbms-for-fraud-detection.md](./04-graph-vs-rdbms-for-fraud-detection.md)`: the graph trails source systems by seconds; any real-time score inherits that window.

---

## 8. Sources

- Neptune Analytics algorithms (catalog): [https://docs.aws.amazon.com/neptune-analytics/latest/userguide/algorithms.html](https://docs.aws.amazon.com/neptune-analytics/latest/userguide/algorithms.html)
- Clustering / community detection (`louvain`, `wcc`, `labelPropagation`, `scc`): [https://docs.aws.amazon.com/neptune-analytics/latest/userguide/clustering-algorithms.html](https://docs.aws.amazon.com/neptune-analytics/latest/userguide/clustering-algorithms.html)
- Centrality (`degree`, `pageRank`, `closenessCentrality`): [https://docs.aws.amazon.com/neptune-analytics/latest/userguide/centrality-algorithms.html](https://docs.aws.amazon.com/neptune-analytics/latest/userguide/centrality-algorithms.html)
- Similarity (`jaccardSimilarity`, `overlapSimilarity`, `neighbors.`*): [https://docs.aws.amazon.com/neptune-analytics/latest/userguide/similarity-algorithms.html](https://docs.aws.amazon.com/neptune-analytics/latest/userguide/similarity-algorithms.html)
- SSSP / pathfinding: [https://docs.aws.amazon.com/neptune-analytics/latest/userguide/sssp-algorithms.html](https://docs.aws.amazon.com/neptune-analytics/latest/userguide/sssp-algorithms.html)
- Neptune Analytics vs Neptune Database: [https://docs.aws.amazon.com/neptune-analytics/latest/userguide/neptune-analytics-vs-neptune-database.html](https://docs.aws.amazon.com/neptune-analytics/latest/userguide/neptune-analytics-vs-neptune-database.html)
- Neptune ML overview (GNN, R-GCN, KGE, tasks): [https://docs.aws.amazon.com/neptune/latest/userguide/machine-learning.html](https://docs.aws.amazon.com/neptune/latest/userguide/machine-learning.html)
- Gremlin node-classification & inductive inference: [https://docs.aws.amazon.com/neptune/latest/userguide/machine-learning-gremlin-vertex-classification-queries.html](https://docs.aws.amazon.com/neptune/latest/userguide/machine-learning-gremlin-vertex-classification-queries.html)
- AWS: Build fraud detection with Entity Resolution + Neptune Analytics: [https://aws.amazon.com/blogs/database/build-fraud-detection-systems-using-aws-entity-resolution-and-amazon-neptune-analytics/](https://aws.amazon.com/blogs/database/build-fraud-detection-systems-using-aws-entity-resolution-and-amazon-neptune-analytics/)
- AWS: Real-time GNN fraud detection (SageMaker + Neptune + DGL): [https://aws.amazon.com/blogs/machine-learning/build-a-gnn-based-real-time-fraud-detection-solution-using-amazon-sagemaker-amazon-neptune-and-the-deep-graph-library/](https://aws.amazon.com/blogs/machine-learning/build-a-gnn-based-real-time-fraud-detection-solution-using-amazon-sagemaker-amazon-neptune-and-the-deep-graph-library/)


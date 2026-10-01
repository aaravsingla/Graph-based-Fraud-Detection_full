# Neptune Analytics Quick Start — 30 Minutes to Fraud Insights

**Estimated time:** 30 min setup + 2-3 hours compute time + 10 min queries = fraud detection live

---

## The 8-Step Flow (Visual)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Neptune DB (Your Graph)                            │
│                    ✅ Already loaded from CSV export                        │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    │  STEP 1: Create Snapshot       │
                    │  (5-15 min)                    │
                    └────────────────┬────────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    │  STEP 2: Load Analytics Graph  │
                    │  (10-30 min, fully automatic)  │
                    └────────────────┬────────────────┘
                                     │
              ┌──────────────────────┴──────────────────────┐
              │                                             │
    ┌─────────▼─────────┐                      ┌───────────▼────────┐
    │ STEP 3: Verify    │                      │ STEP 4-6: Algorithms
    │ Data Loaded       │                      │                    │
    │ ✓ Node counts     │                      │ ✓ Louvain (rings)  │
    │ ✓ Edge counts     │                      │ ✓ PageRank (hubs)  │
    └─────────┬─────────┘                      │ ✓ Degree (shared)  │
              │                                │                    │
              │ (1-2 min)                      │ (2-5 min each)     │
              │                                │                    │
              └──────────────────┬─────────────┴────────────────────┘
                                 │
              ┌──────────────────┴──────────────────┐
              │      STEP 7: Query Results         │
              │    (milliseconds, live endpoint)   │
              │                                    │
              │ ✓ Top 10 communities              │
              │ ✓ Top 20 hubs                     │
              │ ✓ Cross-agency device sharing     │
              └──────────────────┬──────────────────┘
                                 │
              ┌──────────────────┴──────────────────┐
              │    STEP 8: Generate Report         │
              │  (fraud_analytics_report.json)     │
              │                                    │
              │ ✓ Top fraud signals               │
              │ ✓ Key findings & rankings          │
              │ ✓ Ready for dashboard              │
              └──────────────────┬──────────────────┘
                                 │
              ┌──────────────────┴──────────────────┐
              │   (Optional) Pause to Save Costs    │
              │   90% savings when idle             │
              └────────────────────────────────────┘
```

---

## Copy-Paste Commands (No Configuration Required)

### Terminal Session Setup

```bash
# Open a new terminal and set these environment variables
# (Replace with your actual values)

export NEPTUNE_DB_CLUSTER="neptune-dev-cluster"
export AWS_REGION="eu-west-1"

# You'll fill these in as you go:
export SNAPSHOT_ID=""
export ANALYTICS_GRAPH_ID=""
export ANALYTICS_ENDPOINT=""
```

### Phase 1: Create Infrastructure (30 minutes)

**1. Create snapshot (run this, then wait 5-15 min for "available")**

```bash
SNAPSHOT_ID="synapse-analytics-snapshot-$(date +%Y%m%d-%H%M%S)"

aws neptune create-db-cluster-snapshot \
  --db-cluster-identifier $NEPTUNE_DB_CLUSTER \
  --db-cluster-snapshot-identifier $SNAPSHOT_ID \
  --region $AWS_REGION

echo "Created snapshot: $SNAPSHOT_ID"
echo "Waiting for completion... (check status below)"

# Monitor status (stop when it says "available")
watch -n 10 "aws neptune describe-db-cluster-snapshots \
  --db-cluster-snapshot-identifier '$SNAPSHOT_ID' \
  --region $AWS_REGION \
  --query 'DBClusterSnapshots[0].Status' \
  --output text"
```

**2. Create Analytics graph (run this, then wait 10-30 min for "AVAILABLE")**

```bash
aws neptune-analytics create-graph \
  --graph-name "synapse-fraud-analytics" \
  --source-snapshot-id "$SNAPSHOT_ID" \
  --region $AWS_REGION

# This will print your ANALYTICS_GRAPH_ID — copy it!
# Output looks like: "id": "g-abc123xyz789"
# Set it:
export ANALYTICS_GRAPH_ID="g-abc123xyz789"

echo "Created graph: $ANALYTICS_GRAPH_ID"
echo "Waiting for availability... (check status below)"

# Monitor status (stop when it says "AVAILABLE")
watch -n 20 "aws neptune-analytics get-graph \
  --graph-identifier '$ANALYTICS_GRAPH_ID' \
  --region $AWS_REGION \
  --query 'graph.status' \
  --output text"
```

**3. Get endpoint**

```bash
export ANALYTICS_ENDPOINT=$(aws neptune-analytics get-graph \
  --graph-identifier $ANALYTICS_GRAPH_ID \
  --region $AWS_REGION \
  --query "graph.endpoint" \
  --output text)

echo "Endpoint: $ANALYTICS_ENDPOINT"

# Verify it's accessible
awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d '{"query": "MATCH (n) RETURN count(n) AS totalNodes"}' | jq '.results[0].totalNodes'

# Should print a large number (your vertex count)
```

### Phase 2: Run Algorithms (5 minutes)

**4. Louvain Community Detection (find fraud rings)**

```bash
echo "[*] Running Louvain community detection..."

awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d '{"query": "CALL neptune.algo.louvain.mutate({ edgeLabels: ['\''RELATED_TO'\'', '\''BELONGS_TO'\'', '\''LOGGED_IN'\'', '\''CREATED_BOOKING'\''], edgeWeightProperty: '\''riskWeight'\'', writeProperty: '\''communityId'\'' }) YIELD success, communityCount, modularity RETURN success, communityCount, modularity"}' \
  | jq '.results[0]'

# Expected output:
# {
#   "success": true,
#   "communityCount": 47,
#   "modularity": 0.823
# }
```

**5. PageRank Centrality (find ringleaders & hubs)**

```bash
echo "[*] Running PageRank centrality..."

awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d '{"query": "CALL neptune.algo.pageRank.mutate({ edgeLabels: ['\''RELATED_TO'\'', '\''BELONGS_TO'\'', '\''LOGGED_IN'\'', '\''CREATED_BOOKING'\'', '\''ACCESSED_FROM'\''], writeProperty: '\''pageRankScore'\'' }) YIELD success, iterations RETURN success, iterations"}' \
  | jq '.results[0]'

# Expected output:
# {
#   "success": true,
#   "iterations": 20
# }
```

**6. Degree Centrality (find super-shared artifacts)**

```bash
echo "[*] Running degree centrality..."

awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d '{"query": "CALL neptune.algo.degree.mutate({ edgeLabels: ['\''LOGGED_IN'\'', '\''ACCESSED_FROM'\'', '\''CREATED_BOOKING'\''], writeProperty: '\''inDegree'\'', direction: '\''INCOMING'\'' }) YIELD success RETURN success"}' \
  | jq '.results[0]'

# Expected output:
# { "success": true }
```

### Phase 3: Query Results (2 minutes)

**7a. Top 10 Fraud Communities**

```bash
echo "=== TOP 10 FRAUD COMMUNITIES ==="

awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d '{"query": "MATCH (n) WHERE n.communityId IS NOT NULL WITH n.communityId AS community, count(n) AS nodeCount ORDER BY nodeCount DESC LIMIT 10 RETURN community, nodeCount"}' \
  | jq '.results[] | "\(.community): \(.nodeCount) nodes"'
```

**7b. Top 20 Hubs (by PageRank)**

```bash
echo "=== TOP 20 HUBS (Ringleaders & Super-Shared Artifacts) ==="

awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d '{"query": "MATCH (n) WHERE n.pageRankScore IS NOT NULL RETURN labels(n)[0] AS nodeType, coalesce(n.agencyId, n.memberId, n.deviceId, n.ipAddress) AS identifier, round(n.pageRankScore, 4) AS score ORDER BY score DESC LIMIT 20"}' \
  | jq '.results[] | "\(.nodeType): \(.identifier) (score: \(.score))"'
```

**7c. Cross-Agency Device Sharing (Pattern #1)**

```bash
echo "=== AGENCIES SHARING DEVICES (Pattern #1 Fraud Rings) ==="

awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d '{"query": "MATCH (d:Device)<-[:LOGGED_IN]-(m1:Member)-[:BELONGS_TO]->(a1:Agency), (d)<-[:LOGGED_IN]-(m2:Member)-[:BELONGS_TO]->(a2:Agency) WHERE a1.agencyId < a2.agencyId WITH d, a1, a2, count(DISTINCT m1) AS members1, count(DISTINCT m2) AS members2 RETURN a1.agencyId AS agency1, a2.agencyId AS agency2, members1, members2, count(DISTINCT d) AS sharedDeviceCount ORDER BY sharedDeviceCount DESC LIMIT 20"}' \
  | jq '.results[] | "\(.agency1) ↔ \(.agency2): \(.sharedDeviceCount) shared devices (\(.members1) members, \(.members2) members)"'
```

**7d. Super-Shared Devices Across Agencies**

```bash
echo "=== SUPER-SHARED DEVICES (10+ agencies) ==="

awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d '{"query": "MATCH (n:Device) WHERE n.inDegree IS NOT NULL WITH n MATCH (n)<-[:LOGGED_IN]-(m:Member)-[:BELONGS_TO]->(a:Agency) WITH n, count(DISTINCT a) AS agencyCount, count(DISTINCT m) AS memberCount, n.inDegree AS degree WHERE agencyCount > 10 RETURN n.deviceId AS device, degree, agencyCount, memberCount ORDER BY degree DESC LIMIT 20"}' \
  | jq '.results[] | "\(.device): \(.degree) logins from \(.agencyCount) agencies (\(.memberCount) members)"'
```

### Phase 4: Generate Report (1 minute)

**8. Create JSON Report**

```bash
cat > fraud_report.json << 'EOF'
{
  "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "analytics_endpoint": "$ANALYTICS_ENDPOINT",
  "findings": {
    "communities": "See '7a' above",
    "hubs": "See '7b' above",
    "device_sharing": "See '7c' above",
    "super_shared_devices": "See '7d' above"
  }
}
EOF

echo "Report template created: fraud_report.json"
echo "Fill in the results from 7a-7d above"
```

### Phase 5: Cost Optimization (1 minute)

**9. Pause Analytics to Save 90% (Important!)**

```bash
echo "[*] Pausing analytics graph to save costs..."

aws neptune-analytics update-graph \
  --graph-identifier $ANALYTICS_GRAPH_ID \
  --action "pause" \
  --region $AWS_REGION

echo "✓ Graph pausing..."
echo "Check status with:"
echo "  aws neptune-analytics get-graph --graph-identifier $ANALYTICS_GRAPH_ID --region $AWS_REGION"
```

**10. Resume Later (when you need to run more queries)**

```bash
aws neptune-analytics update-graph \
  --graph-identifier $ANALYTICS_GRAPH_ID \
  --action "resume" \
  --region $AWS_REGION

echo "Graph resuming (wait 2-3 minutes)..."
```

---

## Sample Output Interpretation

### If you see these results, you have active fraud signals:

**✅ Good Signs (Fraud Detected)**
```
Top 10 communities:
  1: 523 nodes    ← Large community = potential ring
  2: 412 nodes
  3: 284 nodes

Top hubs (by PageRank):
  Device: device-abc123 (score: 0.0856)  ← High-score device = shared
  Agency: AGENCY_001 (score: 0.0734)     ← Central agency = ringlegader?
  Device: device-xyz789 (score: 0.0612)

Agencies sharing devices:
  AGENCY_001 ↔ AGENCY_042: 47 shared devices   ← RED FLAG
  AGENCY_001 ↔ AGENCY_015: 23 shared devices   ← RED FLAG
  AGENCY_042 ↔ AGENCY_015: 31 shared devices   ← RING DETECTED
```

**⚠️ Warnings (Check Infrastructure)**
```
Communities: 5 total, max 42 nodes      ← Graph too small or disconnected
Hubs: very uniform scores (all ~0.001)  ← Graph structure unclear
Device sharing: 0 pairs                 ← No detected fraud (or no devices)
```

---

## Save These Commands to a Script

Create `run_fraud_analytics.sh`:

```bash
#!/bin/bash

# Set these once
export NEPTUNE_DB_CLUSTER="neptune-dev-cluster"
export AWS_REGION="eu-west-1"
export ANALYTICS_GRAPH_ID="g-abc123xyz789"        # from step 2
export ANALYTICS_ENDPOINT="g-abc123xyz789..."     # from step 3

# Quick commands
case "$1" in
  communities)
    echo "=== Top 10 Communities ==="
    awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
      -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
      -d '{"query": "MATCH (n) WHERE n.communityId IS NOT NULL WITH n.communityId AS community, count(n) AS nodeCount ORDER BY nodeCount DESC LIMIT 10 RETURN community, nodeCount"}' | jq '.results'
    ;;
  hubs)
    echo "=== Top 20 Hubs ==="
    awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
      -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
      -d '{"query": "MATCH (n) WHERE n.pageRankScore IS NOT NULL RETURN labels(n)[0] AS type, coalesce(n.agencyId, n.deviceId) AS id, n.pageRankScore AS score ORDER BY score DESC LIMIT 20"}' | jq '.results'
    ;;
  devices)
    echo "=== Cross-Agency Device Sharing ==="
    awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
      -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
      -d '{"query": "MATCH (d:Device)<-[:LOGGED_IN]-(m1:Member)-[:BELONGS_TO]->(a1:Agency), (d)<-[:LOGGED_IN]-(m2:Member)-[:BELONGS_TO]->(a2:Agency) WHERE a1.agencyId < a2.agencyId WITH d, a1, a2 RETURN a1.agencyId, a2.agencyId, count(DISTINCT d) AS devices ORDER BY devices DESC LIMIT 20"}' | jq '.results'
    ;;
  pause)
    echo "Pausing graph..."
    aws neptune-analytics update-graph --graph-identifier $ANALYTICS_GRAPH_ID --action pause --region $AWS_REGION
    ;;
  *)
    echo "Usage: $0 {communities|hubs|devices|pause}"
    ;;
esac
```

Then run:
```bash
chmod +x run_fraud_analytics.sh
./run_fraud_analytics.sh communities
./run_fraud_analytics.sh hubs
./run_fraud_analytics.sh devices
./run_fraud_analytics.sh pause  # Don't forget this!
```

---

## Estimated Costs

| Resource | Time | Cost |
|----------|------|------|
| Neptune DB cluster | Always running | ~$15/day |
| Snapshot storage | 1 week | ~$0.50 |
| Analytics graph (32 m-NCU) | Active 2 hours | ~$20 |
| Analytics graph (32 m-NCU) | Paused 22 hours | ~$0.70 |
| **Total daily** (with pause) | — | **~$16** |

**Savings tip:** Pause after queries → 90% cost reduction on Analytics!

---

## Next Steps After Getting Results

1. **Share findings with stakeholders** — show top communities & hubs
2. **Investigate top communities manually** — validate if real fraud
3. **Build dashboard** — queries → automated daily reports
4. **Phase 2: Train XGBoost model** — algorithm outputs → fraud scores
5. **Phase 3: Deploy GNN** — real-time fraud scoring on bookings

You now have fraud detection running! 🎉

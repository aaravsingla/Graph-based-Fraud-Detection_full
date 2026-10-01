# Neptune Analytics Setup & Fraud Detection — Step-by-Step Guide

## Prerequisites

- Neptune DB cluster with your Synapse graph loaded (from `neptune_serial_load.py`)
- AWS CLI configured
- Python 3.8+ with boto3
- awscurl installed (`pip install awscurl`)

---

## Step 1: Create Neptune Analytics Cluster from DB Snapshot

### 1.1 Get your Neptune DB cluster identifier

```bash
# List your Neptune DB clusters
aws neptune describe-db-clusters --region eu-west-1 --query 'DBClusters[*].[DBClusterIdentifier,Status]'

# Example output:
# [
#     ["neptune-dev-cluster", "available"],
#     ...
# ]

# Save the cluster ID (e.g., "neptune-dev-cluster")
NEPTUNE_DB_CLUSTER="neptune-dev-cluster"
```

### 1.2 Create a snapshot of your loaded graph

```bash
# Create a snapshot
aws neptune create-db-cluster-snapshot \
  --db-cluster-identifier $NEPTUNE_DB_CLUSTER \
  --db-cluster-snapshot-identifier synapse-analytics-snapshot-$(date +%Y%m%d-%H%M%S) \
  --region eu-west-1

# Example output:
# {
#     "DBClusterSnapshot": {
#         "DBClusterSnapshotIdentifier": "synapse-analytics-snapshot-20260625-143022",
#         "Status": "creating",
#         ...
#     }
# }

SNAPSHOT_ID="synapse-analytics-snapshot-20260625-143022"
```

### 1.3 Wait for snapshot to complete

```bash
# Poll the snapshot status (takes 5-15 minutes)
watch -n 10 'aws neptune describe-db-cluster-snapshots \
  --db-cluster-snapshot-identifier '$SNAPSHOT_ID' \
  --region eu-west-1 \
  --query "DBClusterSnapshots[0].Status"'

# Stop watch when status changes to "available"
# (Ctrl+C)
```

### 1.4 Create Neptune Analytics graph from snapshot

This is the **key step** — Neptune Analytics is a *separate* product that loads data from a snapshot.

```bash
# Create the Analytics graph
aws neptune-analytics create-graph \
  --graph-name "synapse-fraud-analytics" \
  --source-snapshot-id "$SNAPSHOT_ID" \
  --region eu-west-1 \
  --public-connectivity=false

# Example output:
# {
#     "id": "g-xyz1234567",
#     "name": "synapse-fraud-analytics",
#     "status": "CREATING",
#     ...
# }

ANALYTICS_GRAPH_ID="g-xyz1234567"
```

### 1.5 Wait for Analytics graph to be ready

```bash
# Poll the graph status (takes 10-30 minutes)
watch -n 20 'aws neptune-analytics get-graph \
  --graph-identifier '$ANALYTICS_GRAPH_ID' \
  --region eu-west-1 \
  --query "graph.status"'

# Stop when status is "AVAILABLE"
```

### 1.6 Get the endpoint for your Analytics graph

```bash
aws neptune-analytics get-graph \
  --graph-identifier $ANALYTICS_GRAPH_ID \
  --region eu-west-1 \
  --query "graph.endpoint" \
  --output text

# Example output:
# g-xyz1234567.us-east-1.neptune-analytics.amazonaws.com

ANALYTICS_ENDPOINT="g-xyz1234567.us-east-1.neptune-analytics.amazonaws.com"
```

---

## Step 2: Verify the Analytics Graph Loaded Your Data

### 2.1 Query node and edge counts

```bash
# Neptune Analytics uses openCypher (not Gremlin)
# Create a query script

cat > query_counts.cypher << 'EOF'
// Count total nodes by label
MATCH (n) 
RETURN labels(n)[0] AS label, count(n) AS count
ORDER BY count DESC
EOF

# Run the query via awscurl
awscurl -X POST \
  https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d @- << EOF
{
  "query": "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count ORDER BY count DESC"
}
EOF
```

### 2.2 Quick sanity check

```bash
# Count all edges
awscurl -X POST \
  https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d @- << EOF
{
  "query": "MATCH ()-[r]->() RETURN type(r) AS edgeType, count(r) AS count ORDER BY count DESC LIMIT 10"
}
EOF

# Should show your fraud edges (LOGGED_IN, CREATED_BOOKING, etc.)
```

---

## Step 3: Run Louvain Community Detection (Find Fraud Rings)
### 3.1 What Louvain does (in plain English)

Louvain finds **dense clusters** of nodes that are highly connected to each other but loosely connected to other clusters.

For fraud:
- **Agencies sharing artifacts** (devices, IPs, addresses) → clustered together
- **Members of the same fraud ring** → have more connections within the ring than outside
- Synapse fraud pattern #5 (agency collusion) is exactly what Louvain detects

### 3.2 Run Louvain algorithm

```bash
# Louvain detects communities weighted by edge properties
# We'll weight by riskWeight (already on RELATED_TO edges)

cat > louvain_query.cypher << 'EOF'
CALL neptune.algo.louvain.mutate({
  edgeLabels: ['RELATED_TO', 'BELONGS_TO', 'LOGGED_IN', 'CREATED_BOOKING'],
  edgeWeightProperty: 'riskWeight',
  writeProperty: 'communityId',
  seedProperty: 'seed'
}) YIELD success, communityCount, modularity
RETURN success, communityCount, modularity
EOF

awscurl -X POST \
  https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d @- << EOF
{
  "query": "CALL neptune.algo.louvain.mutate({ edgeLabels: ['RELATED_TO', 'BELONGS_TO', 'LOGGED_IN', 'CREATED_BOOKING'], edgeWeightProperty: 'riskWeight', writeProperty: 'communityId' }) YIELD success, communityCount, modularity RETURN success, communityCount, modularity"
}
EOF

# Expected output:
# {
#   "results": [
#     {
#       "success": true,
#       "communityCount": 47,
#       "modularity": 0.823
#     }
#   ]
# }
```

### 3.3 Examine the largest 10 communities (potential fraud rings)

```bash
cat > find_top_communities.cypher << 'EOF'
// Find communities by size
MATCH (n) 
WHERE n.communityId IS NOT NULL
WITH n.communityId AS community, count(n) AS nodeCount, collect(labels(n)[0]) AS nodeLabels
ORDER BY nodeCount DESC
LIMIT 10
RETURN community, nodeCount, nodeLabels
EOF

awscurl -X POST \
  https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d @- << EOF
{
  "query": "MATCH (n) WHERE n.communityId IS NOT NULL WITH n.communityId AS community, count(n) AS nodeCount, collect(labels(n)[0]) AS nodeLabels ORDER BY nodeCount DESC LIMIT 10 RETURN community, nodeCount, nodeLabels"
}
EOF

# Example output:
# {
#   "results": [
#     {"community": 1, "nodeCount": 523, "nodeLabels": ["Member", "Device", "Agency"]},
#     {"community": 5, "nodeCount": 412, "nodeLabels": ["Member", "Device"]},
#     {"community": 3, "nodeCount": 284, "nodeLabels": ["Agency", "Member"]},
#     ...
#   ]
# }
```

### 3.4 Inspect a specific suspicious community in detail

```bash
# Replace COMMUNITY_ID with one from above (e.g., 1)
COMMUNITY_ID=1

awscurl -X POST \
  https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d @- << EOF
{
  "query": "MATCH (n:Agency) WHERE n.communityId = $communityId WITH n MATCH (n)-[r]-(m:Agency) WHERE m.communityId = $communityId RETURN n.agencyId AS agency, m.agencyId AS relatedAgency, type(r) AS relationship, count(r) AS connectionCount ORDER BY connectionCount DESC LIMIT 20",
  "parameters": {"communityId": $COMMUNITY_ID}
}
EOF

# Shows which agencies in the same community are most heavily connected
```

---

## Step 4: Run PageRank Centrality (Find Ringleaders)

### 4.1 What PageRank does

PageRank ranks nodes by **importance** — high-importance nodes are those connected to other important nodes.

For fraud:
- **Ringleaders** are structurally central in the ring
- **Super-hubs** (shared devices, IP addresses) rank high
- Synapse pattern #3 (shared IP), #1 (shared device) surface naturally

### 4.2 Run PageRank

```bash
awscurl -X POST \
  https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d @- << EOF
{
  "query": "CALL neptune.algo.pageRank.mutate({ edgeLabels: ['RELATED_TO', 'BELONGS_TO', 'LOGGED_IN', 'CREATED_BOOKING', 'ACCESSED_FROM'], writeProperty: 'pageRankScore' }) YIELD success, iterations RETURN success, iterations"
}
EOF

# Expected output:
# {
#   "results": [
#     {
#       "success": true,
#       "iterations": 20
#     }
#   ]
# }
```

### 4.3 Find top 20 hubs by PageRank

```bash
awscurl -X POST \
  https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d @- << EOF
{
  "query": "MATCH (n) WHERE n.pageRankScore IS NOT NULL RETURN labels(n)[0] AS nodeType, n.nodeId AS id, coalesce(n.agencyId, n.memberId, n.deviceId, n.ipAddress) AS identifier, n.pageRankScore AS score ORDER BY score DESC LIMIT 20"
}
EOF

# Example output (top hubs):
# {
#   "results": [
#     {"nodeType": "Device", "id": "device-12345", "identifier": "192.168.1.100", "score": 0.0856},
#     {"nodeType": "Agency", "id": "AGENCY_001", "identifier": "AGENCY_001", "score": 0.0734},
#     {"nodeType": "Member", "id": "member-5678", "identifier": "member-5678", "score": 0.0612},
#     ...
#   ]
# }
```

---

## Step 5: Run Degree Centrality (Find Super-Shared Artifacts)

### 5.1 What Degree does

Degree counts **incident edges** on each node.

For fraud:
- A `Device` with 500+ logins = shared across many members = red flag
- An `IpAddress` with connections to 10+ different `Agency` nodes = coordinated activity
- Synapse pattern #13 (bot via response latency on shared devices) uses this

### 5.2 Run degree algorithm

```bash
awscurl -X POST \
  https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d @- << EOF
{
  "query": "CALL neptune.algo.degree.mutate({ edgeLabels: ['LOGGED_IN', 'ACCESSED_FROM', 'CREATED_BOOKING'], writeProperty: 'inDegree', direction: 'INCOMING' }) YIELD success RETURN success"
}
EOF
```

### 5.3 Find top 20 super-shared devices and IPs

```bash
awscurl -X POST \
  https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d @- << EOF
{
  "query": "MATCH (n:Device) WHERE n.inDegree IS NOT NULL WITH n MATCH (n)<-[:LOGGED_IN]-(m:Member)-[:BELONGS_TO]->(a:Agency) WITH n, count(DISTINCT a) AS agencyCount, count(DISTINCT m) AS memberCount, n.inDegree AS degree WHERE agencyCount > 1 RETURN 'Device' AS type, n.deviceId AS identifier, degree, agencyCount, memberCount ORDER BY degree DESC LIMIT 20"
}
EOF

# Example output:
# {
#   "results": [
#     {"type": "Device", "identifier": "device-abc123", "degree": 487, "agencyCount": 12, "memberCount": 45},
#     {"type": "Device", "identifier": "device-xyz789", "degree": 234, "agencyCount": 8, "memberCount": 22},
#     ...
#   ]
# }

# Same for IPs
awscurl -X POST \
  https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d @- << EOF
{
  "query": "MATCH (n:IpAddress) WHERE n.inDegree IS NOT NULL WITH n MATCH (n)<-[:ACCESSED_FROM]-(m:Member)-[:BELONGS_TO]->(a:Agency) WITH n, count(DISTINCT a) AS agencyCount, count(DISTINCT m) AS memberCount, n.inDegree AS degree WHERE agencyCount > 1 RETURN 'IpAddress' AS type, n.ipAddress AS identifier, degree, agencyCount, memberCount ORDER BY degree DESC LIMIT 20"
}
EOF
```

---

## Step 6: Find Cross-Agency Device Sharing (Pattern #1 Directly)
### 6.1 Direct pattern query — agencies sharing devices

This is a **Layer 1 rule query** (not an algorithm), but it's powerful in Analytics because openCypher can run it fast on the in-memory graph:

```bash
awscurl -X POST \
  https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d @- << EOF
{
  "query": "MATCH (d:Device)<-[:LOGGED_IN]-(m1:Member)-[:BELONGS_TO]->(a1:Agency), (d)<-[:LOGGED_IN]-(m2:Member)-[:BELONGS_TO]->(a2:Agency) WHERE a1.agencyId < a2.agencyId WITH d, a1, a2, count(DISTINCT m1) AS members1, count(DISTINCT m2) AS members2 RETURN a1.agencyId AS agency1, a2.agencyId AS agency2, d.deviceId AS sharedDevice, members1, members2, count(DISTINCT d) AS sharedDeviceCount ORDER BY sharedDeviceCount DESC LIMIT 20"
}
EOF

# Example output:
# {
#   "results": [
#     {
#       "agency1": "AGENCY_001",
#       "agency2": "AGENCY_042",
#       "sharedDevice": "device-abc",
#       "members1": 5,
#       "members2": 8,
#       "sharedDeviceCount": 47
#     },
#     ...
#   ]
# }

# This says: AGENCY_001 and AGENCY_042 share 47 devices in common
# with 5 members from AGENCY_001 and 8 members from AGENCY_042
```

---

## Step 7: Export Results to a Report

### 7.1 Create a Python script to generate the fraud report

```bash
cat > generate_fraud_report.py << 'EOF'
#!/usr/bin/env python3
"""
Generate a fraud analytics report from Neptune Analytics results
"""

import json
import subprocess
from datetime import datetime

ANALYTICS_ENDPOINT = "g-xyz1234567.us-east-1.neptune-analytics.amazonaws.com"

def run_query(query_str):
    """Run an openCypher query and return JSON results"""
    cmd = ["awscurl", "-X", "POST",
           f"https://{ANALYTICS_ENDPOINT}/openCypher",
           "-H", "Content-Type: application/x-amzn-neptune-open-cypher-query+json",
           "-d", json.dumps({"query": query_str})]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Query failed: {result.stderr}")
        return None
    return json.loads(result.stdout)

def main():
    report = {
        "generated_at": datetime.now().isoformat(),
        "analytics_endpoint": ANALYTICS_ENDPOINT,
        "sections": {}
    }

    # 1. Communities
    print("[1/5] Fetching top communities...")
    communities_query = """
    MATCH (n) WHERE n.communityId IS NOT NULL
    WITH n.communityId AS community, count(n) AS nodeCount
    ORDER BY nodeCount DESC
    LIMIT 10
    RETURN community, nodeCount
    """
    report["sections"]["top_communities"] = run_query(communities_query).get("results", [])

    # 2. PageRank hubs
    print("[2/5] Fetching top hubs...")
    hubs_query = """
    MATCH (n) WHERE n.pageRankScore IS NOT NULL
    RETURN labels(n)[0] AS nodeType, 
           coalesce(n.agencyId, n.memberId, n.deviceId, n.ipAddress) AS identifier,
           n.pageRankScore AS score
    ORDER BY score DESC
    LIMIT 20
    """
    report["sections"]["top_hubs"] = run_query(hubs_query).get("results", [])

    # 3. Shared devices
    print("[3/5] Fetching cross-agency device sharing...")
    devices_query = """
    MATCH (d:Device)<-[:LOGGED_IN]-(m1:Member)-[:BELONGS_TO]->(a1:Agency),
          (d)<-[:LOGGED_IN]-(m2:Member)-[:BELONGS_TO]->(a2:Agency)
    WHERE a1.agencyId < a2.agencyId
    WITH d, a1, a2, count(DISTINCT m1) AS members1, count(DISTINCT m2) AS members2
    RETURN a1.agencyId, a2.agencyId, members1, members2, 
           count(DISTINCT d) AS sharedDeviceCount
    ORDER BY sharedDeviceCount DESC
    LIMIT 20
    """
    report["sections"]["cross_agency_devices"] = run_query(devices_query).get("results", [])

    # 4. Summary stats
    print("[4/5] Calculating summary stats...")
    stats_query = "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count ORDER BY count DESC"
    report["sections"]["node_counts"] = run_query(stats_query).get("results", [])

    # 5. Write report
    print("[5/5] Writing report...")
    with open("fraud_analytics_report.json", "w") as f:
        json.dump(report, f, indent=2)
    
    print("\n✅ Report written to fraud_analytics_report.json")
    print(f"\nKey findings:")
    print(f"  - Communities detected: {len(report['sections']['top_communities'])}")
    print(f"  - Top hub: {report['sections']['top_hubs'][0] if report['sections']['top_hubs'] else 'N/A'}")
    print(f"  - Cross-agency device sharing pairs: {len(report['sections']['cross_agency_devices'])}")

if __name__ == "__main__":
    main()
EOF

chmod +x generate_fraud_report.py
```

### 7.2 Run the report generator

```bash
# First, set your endpoint
export ANALYTICS_ENDPOINT="g-xyz1234567.us-east-1.neptune-analytics.amazonaws.com"

# Edit the Python script to use this endpoint
sed -i "s|g-xyz1234567.us-east-1.neptune-analytics.amazonaws.com|$ANALYTICS_ENDPOINT|g" generate_fraud_report.py

# Run it
python3 generate_fraud_report.py
```

---

## Step 8: Pause Neptune Analytics to Save Costs (Important!)

### 8.1 Why pause?

Neptune Analytics is priced per m-NCU per hour. If you're not running queries, you're paying for idle capacity.

- Running analytics: ~$0.30 per m-NCU per hour (32 m-NCU ≈ $9.60/hour)
- Paused analytics: ~$0.03 per m-NCU per hour (32 m-NCU ≈ $0.96/hour)

**Pause when not in use → 90% savings.**

### 8.2 Pause the graph

```bash
aws neptune-analytics update-graph \
  --graph-identifier $ANALYTICS_GRAPH_ID \
  --public-connectivity=false \
  --action "pause" \
  --region eu-west-1

# Confirmation:
# {
#     "id": "g-xyz1234567",
#     "status": "PAUSING"
# }

# Wait for it to pause (1-2 minutes)
aws neptune-analytics get-graph \
  --graph-identifier $ANALYTICS_GRAPH_ID \
  --region eu-west-1
```

### 8.3 Resume the graph later

```bash
aws neptune-analytics update-graph \
  --graph-identifier $ANALYTICS_GRAPH_ID \
  --public-connectivity=false \
  --action "resume" \
  --region eu-west-1

# Wait 2-3 minutes for it to be ready again
```

---

## Step 9: Automated Workflow (Shell Script)

### 9.1 Create a one-shot runner

```bash
cat > run_analytics.sh << 'EOF'
#!/bin/bash

# Neptune Analytics fraud detection runner
# Usage: ./run_analytics.sh [ENDPOINT] [ACTION]
# Actions: query, report, pause

ANALYTICS_ENDPOINT="${1:-g-xyz1234567.us-east-1.neptune-analytics.amazonaws.com}"
ACTION="${2:-query}"

echo "[*] Endpoint: $ANALYTICS_ENDPOINT"

case "$ACTION" in
  query)
    echo "[*] Running fraud queries..."
    
    # Top communities
    echo "  ▶ Top 10 communities..."
    awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
      -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
      -d '{"query": "MATCH (n) WHERE n.communityId IS NOT NULL WITH n.communityId AS community, count(n) AS nodeCount ORDER BY nodeCount DESC LIMIT 10 RETURN community, nodeCount"}' \
      | jq '.results'
    
    # Top hubs
    echo "  ▶ Top 20 hubs..."
    awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
      -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
      -d '{"query": "MATCH (n) WHERE n.pageRankScore IS NOT NULL RETURN labels(n)[0] AS nodeType, coalesce(n.agencyId, n.memberId, n.deviceId, n.ipAddress) AS identifier, n.pageRankScore AS score ORDER BY score DESC LIMIT 20"}' \
      | jq '.results'
    ;;

  report)
    echo "[*] Generating fraud report..."
    python3 generate_fraud_report.py
    ;;

  pause)
    echo "[*] Pausing analytics graph..."
    GRAPH_ID=$(echo $ANALYTICS_ENDPOINT | cut -d. -f1)
    aws neptune-analytics update-graph \
      --graph-identifier $GRAPH_ID \
      --action "pause" \
      --region eu-west-1
    echo "✓ Graph pausing (check status in 1-2 minutes)"
    ;;

  *)
    echo "Unknown action: $ACTION"
    echo "Use: query, report, or pause"
    ;;
esac
EOF

chmod +x run_analytics.sh
```

### 9.2 Use it

```bash
# Set your endpoint
export ANALYTICS_ENDPOINT="g-xyz1234567.us-east-1.neptune-analytics.amazonaws.com"

# Run queries
./run_analytics.sh $ANALYTICS_ENDPOINT query

# Generate report
./run_analytics.sh $ANALYTICS_ENDPOINT report

# Pause when done
./run_analytics.sh $ANALYTICS_ENDPOINT pause
```

---

## Quick Reference: All Commands in One Place
### Complete Checklist

```bash
# ============================================================================
# STEP 1: Create snapshot from Neptune DB
# ============================================================================
NEPTUNE_DB_CLUSTER="neptune-dev-cluster"
SNAPSHOT_ID="synapse-analytics-snapshot-$(date +%Y%m%d-%H%M%S)"

aws neptune create-db-cluster-snapshot \
  --db-cluster-identifier $NEPTUNE_DB_CLUSTER \
  --db-cluster-snapshot-identifier $SNAPSHOT_ID \
  --region eu-west-1

# Wait for snapshot to be "available"
aws neptune describe-db-cluster-snapshots \
  --db-cluster-snapshot-identifier $SNAPSHOT_ID \
  --region eu-west-1

# ============================================================================
# STEP 2: Create Analytics graph from snapshot
# ============================================================================
aws neptune-analytics create-graph \
  --graph-name "synapse-fraud-analytics" \
  --source-snapshot-id "$SNAPSHOT_ID" \
  --region eu-west-1

# Wait 10-30 minutes for graph to be "AVAILABLE"
ANALYTICS_GRAPH_ID="g-xyz1234567"  # Replace with actual ID
aws neptune-analytics get-graph \
  --graph-identifier $ANALYTICS_GRAPH_ID \
  --region eu-west-1

# Get endpoint
ANALYTICS_ENDPOINT=$(aws neptune-analytics get-graph \
  --graph-identifier $ANALYTICS_GRAPH_ID \
  --region eu-west-1 \
  --query "graph.endpoint" \
  --output text)

# ============================================================================
# STEP 3: Verify data loaded
# ============================================================================
awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d '{"query": "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count ORDER BY count DESC"}'

# ============================================================================
# STEP 4: Run Louvain (community detection)
# ============================================================================
awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d '{"query": "CALL neptune.algo.louvain.mutate({ edgeLabels: ['\''RELATED_TO'\'', '\''BELONGS_TO'\'', '\''LOGGED_IN'\'', '\''CREATED_BOOKING'\''], edgeWeightProperty: '\''riskWeight'\'', writeProperty: '\''communityId'\'' }) YIELD success, communityCount, modularity RETURN success, communityCount, modularity"}'

# ============================================================================
# STEP 5: Run PageRank (centrality/importance)
# ============================================================================
awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d '{"query": "CALL neptune.algo.pageRank.mutate({ edgeLabels: ['\''RELATED_TO'\'', '\''BELONGS_TO'\'', '\''LOGGED_IN'\'', '\''CREATED_BOOKING'\'', '\''ACCESSED_FROM'\''], writeProperty: '\''pageRankScore'\'' }) YIELD success, iterations RETURN success, iterations"}'

# ============================================================================
# STEP 6: Run Degree (hub detection)
# ============================================================================
awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d '{"query": "CALL neptune.algo.degree.mutate({ edgeLabels: ['\''LOGGED_IN'\'', '\''ACCESSED_FROM'\'', '\''CREATED_BOOKING'\''], writeProperty: '\''inDegree'\'', direction: '\''INCOMING'\'' }) YIELD success RETURN success"}'

# ============================================================================
# STEP 7: Query results
# ============================================================================

# Top 10 communities
awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d '{"query": "MATCH (n) WHERE n.communityId IS NOT NULL WITH n.communityId AS community, count(n) AS nodeCount ORDER BY nodeCount DESC LIMIT 10 RETURN community, nodeCount"}'

# Top 20 hubs by PageRank
awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d '{"query": "MATCH (n) WHERE n.pageRankScore IS NOT NULL RETURN labels(n)[0] AS nodeType, coalesce(n.agencyId, n.memberId, n.deviceId, n.ipAddress) AS identifier, n.pageRankScore AS score ORDER BY score DESC LIMIT 20"}'

# Top 20 super-shared devices
awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d '{"query": "MATCH (n:Device) WHERE n.inDegree IS NOT NULL WITH n MATCH (n)<-[:LOGGED_IN]-(m:Member)-[:BELONGS_TO]->(a:Agency) WITH n, count(DISTINCT a) AS agencyCount, count(DISTINCT m) AS memberCount, n.inDegree AS degree WHERE agencyCount > 1 RETURN '\''Device'\'' AS type, n.deviceId AS identifier, degree, agencyCount, memberCount ORDER BY degree DESC LIMIT 20"}'

# Cross-agency device sharing (Pattern #1)
awscurl -X POST https://$ANALYTICS_ENDPOINT/openCypher \
  -H "Content-Type: application/x-amzn-neptune-open-cypher-query+json" \
  -d '{"query": "MATCH (d:Device)<-[:LOGGED_IN]-(m1:Member)-[:BELONGS_TO]->(a1:Agency), (d)<-[:LOGGED_IN]-(m2:Member)-[:BELONGS_TO]->(a2:Agency) WHERE a1.agencyId < a2.agencyId WITH d, a1, a2, count(DISTINCT m1) AS members1, count(DISTINCT m2) AS members2 RETURN a1.agencyId AS agency1, a2.agencyId AS agency2, d.deviceId AS sharedDevice, members1, members2, count(DISTINCT d) AS sharedDeviceCount ORDER BY sharedDeviceCount DESC LIMIT 20"}'

# ============================================================================
# STEP 8: Pause to save costs
# ============================================================================
aws neptune-analytics update-graph \
  --graph-identifier $ANALYTICS_GRAPH_ID \
  --action "pause" \
  --region eu-west-1

# ============================================================================
# RESUME LATER
# ============================================================================
aws neptune-analytics update-graph \
  --graph-identifier $ANALYTICS_GRAPH_ID \
  --action "resume" \
  --region eu-west-1
```

---

## Interpreting the Results

### Community Detection (Louvain)

**What you're looking for:**
- `communityCount`: 40-100 communities is typical (shows natural grouping)
- `modularity`: 0.7+ is good (0.3-0.5 means weak communities)

**Fraud signals:**
- A single community with 500+ nodes = massive collusion ring
- Communities that cluster by Agency (many agencies in one community) = undeclared relationships
- Communities that cluster by shared Device/IP = coordinated activity

### PageRank Centrality

**What you're looking for:**
- Top nodes should be either:
  - **Hub artifacts** (Device, IpAddress with high centrality) = shared across many users
  - **Central Members** = ringleaders structurally central to the network
  - **Gateway Agencies** = bridge between separate collusion rings

**Fraud signals:**
- Top 5 nodes are all from the same agency → that agency is central to the graph (collusion?)
- Top 5 nodes are all Devices with 100+ degree → proxy farm / emulator
- Top node is a Member connecting 10+ agencies → possible mule/relay account

### Degree Distribution

**What you're looking for:**
- Devices with 50-500 logins = shared but reasonable
- Devices with 1000+ logins from different agencies = red flag
- IPs accessed from 15+ agencies = coordinated attack or VPN service

**Fraud signals:**
- A single Device with logins from 20+ agencies
- An IP with successful bookings in every agency
- Address reused across 10+ Members in different agencies

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Graph not found" | Check ANALYTICS_GRAPH_ID is correct; wait for AVAILABLE status |
| "Query timeout" | Graph is still loading data; try again in a few minutes |
| "No results" | Algorithm hasn't run yet (run Louvain/PageRank steps first) |
| "awscurl command not found" | `pip install awscurl` |
| "Connection refused" | Endpoint wrong or graph paused; check status and resume if needed |
| High costs | **Pause the graph when not querying** (see Step 8) |

---

## Next: Building the Analytics Portal

Once you have results from Steps 1-7, the next phase is building a dashboard:

1. **Automated Analytics Refresh**: Schedule Step 4-7 to run daily/weekly
2. **Results Store**: Save algorithm outputs (communityId, pageRankScore, inDegree) back to Neptune DB
3. **Dashboard**: Query Neptune DB to show top communities, hubs, shared artifacts
4. **Investigator UI**: Click on a community → see all nodes and edges in that ring

This is the **Analytics Portal** from the Phase-1 roadmap.

---

## Quick Links & References

- [AWS Neptune Analytics Docs](https://docs.aws.amazon.com/neptune-analytics/latest/userguide/what-is-analytics.html)
- [OpenCypher Query Language](https://opencypher.org/)
- [Neptune Algorithms Reference](https://docs.aws.amazon.com/neptune-analytics/latest/userguide/algorithms.html)
- Synapse fraud patterns: `docs/05-neptune-analytics-and-ml-for-fraud-detection.md`

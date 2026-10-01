#!/usr/bin/env python3
"""
Neptune Analytics Fraud Detection Runner
==========================================

Automates the complete workflow:
  1. Create snapshot from Neptune DB
  2. Create Analytics graph from snapshot
  3. Run algorithms (Louvain, PageRank, Degree)
  4. Query results
  5. Generate report
  6. Pause graph to save costs

Usage:
  python3 analytics_runner.py --help
  python3 analytics_runner.py --cluster neptune-dev-cluster
  python3 analytics_runner.py --graph-id g-xyz1234567 --skip-setup
"""

import subprocess
import json
import time
import argparse
import sys
from datetime import datetime
from typing import Dict, List, Any, Optional

# ============================================================================
# Configuration
# ============================================================================

DEFAULT_REGION = "eu-west-1"
DEFAULT_GRAPH_NAME = "synapse-fraud-analytics"

ALGORITHMS = [
    {
        "name": "Louvain Community Detection",
        "query": """
CALL neptune.algo.louvain.mutate({
  edgeLabels: ['RELATED_TO', 'BELONGS_TO', 'LOGGED_IN', 'CREATED_BOOKING'],
  edgeWeightProperty: 'riskWeight',
  writeProperty: 'communityId'
}) YIELD success, communityCount, modularity
RETURN success, communityCount, modularity
        """.strip()
    },
    {
        "name": "PageRank Centrality",
        "query": """
CALL neptune.algo.pageRank.mutate({
  edgeLabels: ['RELATED_TO', 'BELONGS_TO', 'LOGGED_IN', 'CREATED_BOOKING', 'ACCESSED_FROM'],
  writeProperty: 'pageRankScore'
}) YIELD success, iterations
RETURN success, iterations
        """.strip()
    },
    {
        "name": "Degree Centrality",
        "query": """
CALL neptune.algo.degree.mutate({
  edgeLabels: ['LOGGED_IN', 'ACCESSED_FROM', 'CREATED_BOOKING'],
  writeProperty: 'inDegree',
  direction: 'INCOMING'
}) YIELD success
RETURN success
        """.strip()
    },
]

QUERIES = [
    {
        "name": "Top 10 Communities",
        "query": """
MATCH (n) WHERE n.communityId IS NOT NULL
WITH n.communityId AS community, count(n) AS nodeCount
ORDER BY nodeCount DESC
LIMIT 10
RETURN community, nodeCount
        """.strip()
    },
    {
        "name": "Top 20 Hubs by PageRank",
        "query": """
MATCH (n) WHERE n.pageRankScore IS NOT NULL
RETURN labels(n)[0] AS nodeType,
       coalesce(n.agencyId, n.memberId, n.deviceId, n.ipAddress) AS identifier,
       n.pageRankScore AS score
ORDER BY score DESC
LIMIT 20
        """.strip()
    },
    {
        "name": "Cross-Agency Device Sharing",
        "query": """
MATCH (d:Device)<-[:LOGGED_IN]-(m1:Member)-[:BELONGS_TO]->(a1:Agency),
      (d)<-[:LOGGED_IN]-(m2:Member)-[:BELONGS_TO]->(a2:Agency)
WHERE a1.agencyId < a2.agencyId
WITH d, a1, a2, count(DISTINCT m1) AS members1, count(DISTINCT m2) AS members2
RETURN a1.agencyId, a2.agencyId, members1, members2, count(DISTINCT d) AS sharedDeviceCount
ORDER BY sharedDeviceCount DESC
LIMIT 20
        """.strip()
    },
    {
        "name": "Super-Shared Devices",
        "query": """
MATCH (n:Device) WHERE n.inDegree IS NOT NULL
WITH n MATCH (n)<-[:LOGGED_IN]-(m:Member)-[:BELONGS_TO]->(a:Agency)
WITH n, count(DISTINCT a) AS agencyCount, count(DISTINCT m) AS memberCount, n.inDegree AS degree
WHERE agencyCount > 1
RETURN n.deviceId AS device, degree, agencyCount, memberCount
ORDER BY degree DESC
LIMIT 20
        """.strip()
    },
]

# ============================================================================
# Utilities
# ============================================================================

def log(msg: str, level: str = "INFO"):
    """Log with timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}", flush=True)

def run_command(cmd: List[str]) -> Dict[str, Any]:
    """Run shell command and return JSON result"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            log(f"Command failed: {' '.join(cmd)}", "ERROR")
            log(f"stderr: {result.stderr}", "ERROR")
            return {"error": result.stderr}
        if not result.stdout.strip():
            return {"error": "Empty response"}
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        log(f"Invalid JSON response: {result.stdout[:200]}", "ERROR")
        return {"error": "JSON parse error"}
    except subprocess.TimeoutExpired:
        log("Command timed out", "ERROR")
        return {"error": "Timeout"}

def run_query(endpoint: str, query: str) -> Dict[str, Any]:
    """Run openCypher query on Analytics endpoint"""
    cmd = [
        "awscurl",
        "-X", "POST",
        f"https://{endpoint}/openCypher",
        "-H", "Content-Type: application/x-amzn-neptune-open-cypher-query+json",
        "-d", json.dumps({"query": query})
    ]
    return run_command(cmd)

def wait_for_status(
    check_fn,
    target_status: str,
    timeout: int = 1800,
    interval: int = 10,
) -> bool:
    """Poll until status matches target"""
    start = time.time()
    while time.time() - start < timeout:
        status = check_fn()
        if status == target_status:
            return True
        elapsed = int(time.time() - start)
        remaining = (timeout - elapsed) // 60
        log(f"Status: {status}... ({remaining}m remaining)", "INFO")
        time.sleep(interval)
    log(f"Timeout waiting for {target_status}", "ERROR")
    return False

# ============================================================================
# Core Functions
# ============================================================================

def create_snapshot(db_cluster: str, region: str) -> Optional[str]:
    """Create snapshot from Neptune DB cluster"""
    log(f"Creating snapshot from {db_cluster}...", "INFO")
    
    snapshot_id = f"synapse-analytics-snapshot-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    
    cmd = [
        "aws", "neptune", "create-db-cluster-snapshot",
        "--db-cluster-identifier", db_cluster,
        "--db-cluster-snapshot-identifier", snapshot_id,
        "--region", region
    ]
    
    result = run_command(cmd)
    if "error" in result:
        return None
    
    log(f"Snapshot created: {snapshot_id}", "INFO")
    
    # Wait for snapshot to be ready
    def check_status():
        cmd2 = [
            "aws", "neptune", "describe-db-cluster-snapshots",
            "--db-cluster-snapshot-identifier", snapshot_id,
            "--region", region,
            "--query", "DBClusterSnapshots[0].Status",
            "--output", "text"
        ]
        result2 = subprocess.run(cmd2, capture_output=True, text=True)
        return result2.stdout.strip() if result2.returncode == 0 else "UNKNOWN"
    
    if not wait_for_status(check_status, "available", timeout=1200, interval=30):
        return None
    
    log(f"Snapshot ready: {snapshot_id}", "INFO")
    return snapshot_id

def create_analytics_graph(snapshot_id: str, graph_name: str, region: str) -> Optional[str]:
    """Create Analytics graph from snapshot"""
    log(f"Creating Analytics graph from snapshot...", "INFO")
    
    cmd = [
        "aws", "neptune-analytics", "create-graph",
        "--graph-name", graph_name,
        "--source-snapshot-id", snapshot_id,
        "--region", region
    ]
    
    result = run_command(cmd)
    if "error" in result:
        log(f"Failed to create graph: {result['error']}", "ERROR")
        return None
    
    graph_id = result.get("id")
    log(f"Analytics graph created: {graph_id}", "INFO")
    
    # Wait for graph to be ready
    def check_status():
        cmd2 = [
            "aws", "neptune-analytics", "get-graph",
            "--graph-identifier", graph_id,
            "--region", region,
            "--query", "graph.status",
            "--output", "text"
        ]
        result2 = subprocess.run(cmd2, capture_output=True, text=True)
        return result2.stdout.strip() if result2.returncode == 0 else "UNKNOWN"
    
    if not wait_for_status(check_status, "AVAILABLE", timeout=1800, interval=30):
        return None
    
    log(f"Analytics graph ready: {graph_id}", "INFO")
    return graph_id

def get_analytics_endpoint(graph_id: str, region: str) -> Optional[str]:
    """Get endpoint for Analytics graph"""
    cmd = [
        "aws", "neptune-analytics", "get-graph",
        "--graph-identifier", graph_id,
        "--region", region,
        "--query", "graph.endpoint",
        "--output", "text"
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        endpoint = result.stdout.strip()
        log(f"Analytics endpoint: {endpoint}", "INFO")
        return endpoint
    return None

def verify_graph_loaded(endpoint: str) -> bool:
    """Verify graph has data"""
    log("Verifying graph data...", "INFO")
    
    result = run_query(endpoint, "MATCH (n) RETURN count(n) AS count")
    
    if "error" in result:
        log(f"Failed to query graph: {result['error']}", "ERROR")
        return False
    
    count = result.get("results", [{}])[0].get("count", 0)
    log(f"Graph contains {count} nodes", "INFO")
    
    if count == 0:
        log("Graph appears empty!", "ERROR")
        return False
    
    return True

def run_algorithms(endpoint: str) -> Dict[str, Any]:
    """Run all algorithms"""
    log("Running algorithms...", "INFO")
    
    results = {}
    for algo in ALGORITHMS:
        log(f"  Running: {algo['name']}...", "INFO")
        result = run_query(endpoint, algo["query"])
        results[algo["name"]] = result.get("results", [{}])[0] if "results" in result else {"error": "Failed"}
        log(f"    ✓ {algo['name']}", "INFO")
        time.sleep(5)  # Brief pause between algorithms
    
    return results

def run_queries(endpoint: str) -> Dict[str, Any]:
    """Run all fraud queries"""
    log("Running fraud queries...", "INFO")
    
    results = {}
    for query_def in QUERIES:
        log(f"  Querying: {query_def['name']}...", "INFO")
        result = run_query(endpoint, query_def["query"])
        results[query_def["name"]] = result.get("results", []) if "results" in result else []
    
    return results

def generate_report(
    db_cluster: str,
    snapshot_id: str,
    graph_id: str,
    endpoint: str,
    algo_results: Dict[str, Any],
    query_results: Dict[str, Any],
) -> None:
    """Generate JSON fraud report"""
    report = {
        "generated_at": datetime.now().isoformat(),
        "neptune_db_cluster": db_cluster,
        "snapshot_id": snapshot_id,
        "analytics_graph_id": graph_id,
        "analytics_endpoint": endpoint,
        "algorithms": algo_results,
        "findings": query_results,
    }
    
    filename = f"fraud_analytics_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, "w") as f:
        json.dump(report, f, indent=2)
    
    log(f"Report saved: {filename}", "INFO")
    
    # Print summary
    print("\n" + "="*80)
    print("FRAUD ANALYTICS REPORT SUMMARY")
    print("="*80)
    print(f"Generated: {report['generated_at']}")
    print(f"Endpoint:  {endpoint}")
    print(f"Graph ID:  {graph_id}")
    print("\nAlgorithm Results:")
    for name, result in algo_results.items():
        if "error" not in result:
            print(f"  ✓ {name}")
            for key, val in result.items():
                print(f"    {key}: {val}")
    print("\nTop Findings:")
    for query_name, results in query_results.items():
        print(f"  {query_name}: {len(results)} results")
    print("="*80 + "\n")

def pause_graph(graph_id: str, region: str) -> bool:
    """Pause Analytics graph to save costs"""
    log(f"Pausing graph {graph_id}...", "INFO")
    
    cmd = [
        "aws", "neptune-analytics", "update-graph",
        "--graph-identifier", graph_id,
        "--action", "pause",
        "--region", region
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        log("Graph pausing (check status in 1-2 minutes)", "INFO")
        return True
    else:
        log(f"Failed to pause: {result.stderr}", "ERROR")
        return False

# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Neptune Analytics Fraud Detection")
    parser.add_argument("--cluster", default="neptune-dev-cluster", help="Neptune DB cluster ID")
    parser.add_argument("--region", default=DEFAULT_REGION, help="AWS region")
    parser.add_argument("--graph-name", default=DEFAULT_GRAPH_NAME, help="Analytics graph name")
    parser.add_argument("--graph-id", help="Skip setup, use existing graph ID")
    parser.add_argument("--skip-setup", action="store_true", help="Skip snapshot & graph creation")
    parser.add_argument("--skip-algorithms", action="store_true", help="Skip algorithm runs")
    parser.add_argument("--skip-pause", action="store_true", help="Don't pause graph after completion")
    
    args = parser.parse_args()
    
    log("Neptune Analytics Fraud Detection Runner", "INFO")
    log(f"Region: {args.region}", "INFO")
    
    # Step 1: Setup (snapshot + graph)
    snapshot_id = None
    graph_id = args.graph_id
    
    if not args.skip_setup:
        log("STEP 1: Creating snapshot and Analytics graph", "INFO")
        
        snapshot_id = create_snapshot(args.cluster, args.region)
        if not snapshot_id:
            log("Failed to create snapshot", "ERROR")
            sys.exit(1)
        
        graph_id = create_analytics_graph(snapshot_id, args.graph_name, args.region)
        if not graph_id:
            log("Failed to create Analytics graph", "ERROR")
            sys.exit(1)
    else:
        if not graph_id:
            log("--skip-setup requires --graph-id", "ERROR")
            sys.exit(1)
    
    # Step 2: Get endpoint
    log("STEP 2: Getting Analytics endpoint", "INFO")
    endpoint = get_analytics_endpoint(graph_id, args.region)
    if not endpoint:
        log("Failed to get endpoint", "ERROR")
        sys.exit(1)
    
    # Step 3: Verify data
    log("STEP 3: Verifying graph data", "INFO")
    if not verify_graph_loaded(endpoint):
        log("Graph verification failed", "ERROR")
        sys.exit(1)
    
    # Step 4: Run algorithms
    algo_results = {}
    if not args.skip_algorithms:
        log("STEP 4: Running algorithms", "INFO")
        algo_results = run_algorithms(endpoint)
    
    # Step 5: Run fraud queries
    log("STEP 5: Running fraud queries", "INFO")
    query_results = run_queries(endpoint)
    
    # Step 6: Generate report
    log("STEP 6: Generating report", "INFO")
    generate_report(
        args.cluster,
        snapshot_id or "N/A",
        graph_id,
        endpoint,
        algo_results,
        query_results,
    )
    
    # Step 7: Pause to save costs
    if not args.skip_pause:
        log("STEP 7: Pausing graph to save costs", "INFO")
        pause_graph(graph_id, args.region)
    
    log("✅ Fraud analytics complete!", "INFO")

if __name__ == "__main__":
    main()

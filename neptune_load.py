"""Two-phase Neptune bulk loader.

Neptune loads all files in a directory in parallel, so edges can be processed
before the vertices they reference — causing insert failures on the edge files.

This script solves the problem by running two sequential load jobs:
  Phase 1 — vertices only  (s3://.../neptune-load/vertices/)
  Phase 2 — edges only     (s3://.../neptune-load/edges/)

Phase 2 only starts after Phase 1 reaches LOAD_COMPLETED.

Usage:
    python neptune_load.py \\
        --endpoint https://neptune-dev-cluster.cluster-xxx.eu-west-1.neptune.amazonaws.com:8182 \\
        --source   s3://dev-login-graph-data/synapse/neptune-load \\
        --iam-role arn:aws:iam::696292622193:role/TTL-custom-rdstos3Policy \\
        --region   eu-west-1

All options can also be set via environment variables (see --help).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

# ---------------------------------------------------------------------------
# awscurl-style signed requests via boto3 + requests (no awscurl binary needed)
# ---------------------------------------------------------------------------

def _make_request(
    method: str,
    url: str,
    *,
    region: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a SigV4-signed request to Neptune and return the parsed JSON response."""
    try:
        import boto3
        import requests
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
        from botocore.credentials import Credentials
    except ImportError as exc:
        raise RuntimeError(
            "boto3 and requests are required. Install with: pip install boto3 requests"
        ) from exc

    # Get credentials from environment or boto3 session
    access_key = os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    session_token = os.getenv("AWS_SESSION_TOKEN")

    if access_key and secret_key:
        credentials = Credentials(access_key, secret_key, session_token)
    else:
        session = boto3.Session()
        creds = session.get_credentials()
        if creds is None:
            raise RuntimeError(
                "AWS credentials not found. Set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY "
                "(and AWS_SESSION_TOKEN if using temporary credentials), or configure boto3."
            )
        credentials = creds.get_frozen_credentials()

    payload = json.dumps(body).encode() if body else b""
    headers = {"Content-Type": "application/json"} if body else {}

    aws_request = AWSRequest(method=method, url=url, data=payload, headers=headers)
    SigV4Auth(credentials, "neptune-db", region).add_auth(aws_request)

    resp = requests.request(
        method,
        url,
        data=payload,
        headers=dict(aws_request.headers),
        timeout=120,
    )
    return resp.json()


# ---------------------------------------------------------------------------
# Neptune loader API wrappers
# ---------------------------------------------------------------------------

def _submit_load(
    endpoint: str,
    source: str,
    iam_role: str,
    region: str,
    *,
    parallelism: str = "HIGH",
    fail_on_error: str = "FALSE",
    update_single_cardinality: str = "FALSE",
) -> str:
    """Submit a load job and return the loadId."""
    url = f"{endpoint.rstrip('/')}/loader"
    body = {
        "source": source,
        "format": "csv",
        "iamRoleArn": iam_role,
        "region": region,
        "failOnError": fail_on_error,
        "parallelism": parallelism,
        "updateSingleCardinalityProperties": update_single_cardinality,
        "queueRequest": "TRUE",
    }
    print(f"[loader] Submitting load: {source}")
    resp = _make_request("POST", url, region=region, body=body)
    print(f"[loader] Response: {json.dumps(resp, indent=2)}")

    load_id = resp.get("payload", {}).get("loadId")
    if not load_id:
        raise RuntimeError(f"No loadId in response: {resp}")
    return load_id


def _poll_load(endpoint: str, load_id: str, region: str, *, poll_interval: int = 15) -> str:
    """Poll until the load job reaches a terminal state. Returns the final status."""
    url = f"{endpoint.rstrip('/')}/loader/{load_id}"
    print(f"[loader] Polling load {load_id} (every {poll_interval}s)...")

    while True:
        resp = _make_request("GET", url, region=region)
        overall = resp.get("payload", {}).get("overallStatus", {})
        status = overall.get("status", "UNKNOWN")
        total = overall.get("totalRecords", "?")
        parse_errs = overall.get("parsingErrors", 0)
        insert_errs = overall.get("insertErrors", 0)
        elapsed = overall.get("totalTimeSpent", "?")

        print(
            f"[loader] status={status}  records={total}  "
            f"parseErrors={parse_errs}  insertErrors={insert_errs}  "
            f"elapsed={elapsed}s"
        )

        if status in ("LOAD_COMPLETED", "LOAD_FAILED", "LOAD_CANCELLED"):
            return status

        time.sleep(poll_interval)


def _run_phase(
    name: str,
    source: str,
    endpoint: str,
    iam_role: str,
    region: str,
    *,
    poll_interval: int = 15,
) -> bool:
    """Run a single load phase. Returns True on success, False on failure."""
    print(f"\n{'='*60}")
    print(f"  Phase: {name}")
    print(f"  Source: {source}")
    print(f"{'='*60}")

    load_id = _submit_load(endpoint, source, iam_role, region)
    final_status = _poll_load(endpoint, load_id, region, poll_interval=poll_interval)

    if final_status == "LOAD_COMPLETED":
        print(f"[loader] ✓ {name} completed successfully (loadId={load_id})")
        return True
    else:
        print(f"[loader] ✗ {name} ended with status={final_status} (loadId={load_id})")
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Two-phase Neptune bulk loader: vertices first, then edges."
    )
    parser.add_argument(
        "--endpoint",
        default=os.getenv("NEPTUNE_ENDPOINT", ""),
        help="Neptune cluster endpoint URL, e.g. https://cluster.xxx.region.neptune.amazonaws.com:8182",
    )
    parser.add_argument(
        "--source",
        default=os.getenv("NEPTUNE_LOAD_SOURCE", ""),
        help="Base S3 URI containing vertices/ and edges/ subdirs, e.g. s3://bucket/synapse/neptune-load",
    )
    parser.add_argument(
        "--iam-role",
        default=os.getenv("NEPTUNE_IAM_ROLE", ""),
        help="IAM role ARN Neptune uses to read from S3.",
    )
    parser.add_argument(
        "--region",
        default=os.getenv("AWS_REGION", "eu-west-1"),
        help="AWS region (default: eu-west-1).",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=int(os.getenv("NEPTUNE_POLL_INTERVAL", "15")),
        help="Seconds between status polls (default: 15).",
    )
    parser.add_argument(
        "--skip-vertices",
        action="store_true",
        help="Skip Phase 1 (vertices). Useful for re-running edges only.",
    )
    parser.add_argument(
        "--skip-edges",
        action="store_true",
        help="Skip Phase 2 (edges). Useful for loading vertices only.",
    )
    args = parser.parse_args()

    # Validate required args
    missing = [name for name, val in [
        ("--endpoint / NEPTUNE_ENDPOINT", args.endpoint),
        ("--source / NEPTUNE_LOAD_SOURCE", args.source),
        ("--iam-role / NEPTUNE_IAM_ROLE", args.iam_role),
    ] if not val]
    if missing:
        parser.error(f"Missing required arguments: {', '.join(missing)}")

    base = args.source.rstrip("/")
    vertices_source = f"{base}/vertices/"
    edges_source = f"{base}/edges/"

    # Phase 1: vertices
    if not args.skip_vertices:
        ok = _run_phase(
            "Phase 1 — Vertices",
            vertices_source,
            args.endpoint,
            args.iam_role,
            args.region,
            poll_interval=args.poll_interval,
        )
        if not ok:
            print("\n[loader] Phase 1 failed. Aborting — edges will not be loaded.")
            print("[loader] Fix vertex errors, then re-run with --skip-vertices to load edges only.")
            sys.exit(1)
    else:
        print("[loader] Skipping Phase 1 (vertices) as requested.")

    # Phase 2: edges
    if not args.skip_edges:
        ok = _run_phase(
            "Phase 2 — Edges",
            edges_source,
            args.endpoint,
            args.iam_role,
            args.region,
            poll_interval=args.poll_interval,
        )
        if not ok:
            print("\n[loader] Phase 2 failed. Vertices are loaded; only edges had issues.")
            sys.exit(1)
    else:
        print("[loader] Skipping Phase 2 (edges) as requested.")

    print("\n[loader] ✓ All phases completed successfully.")


if __name__ == "__main__":
    main()

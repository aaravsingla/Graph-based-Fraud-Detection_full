#!/usr/bin/env python3
"""Load an explicit list of S3 CSV files into Neptune, one job at a time.

Unlike neptune_load.py (which loads a whole directory and therefore also picks
up the known-broken opensearch-edges-auth.csv / -v2.csv files), this loader
takes an explicit list of S3 keys and submits one Neptune load job per file,
polling each to completion before the next.

Use --skip to exclude filenames (substring match). The two permanently-broken
backbone auth files are skipped by default.

Usage (from batchjobs/csv_export, with venv active and .env sourced):
    python load_files.py --prefix neptune-csv-export/20260624T140021Z/final/edges
    python load_files.py --prefix neptune-csv-export/20260624T140021Z/final/edges --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


DEFAULT_SKIP = ["opensearch-edges-auth.csv", "opensearch-edges-auth-v2.csv"]


def _make_request(method, url, *, region, body=None):
    import boto3, requests
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials

    key = os.getenv("AWS_ACCESS_KEY_ID")
    secret = os.getenv("AWS_SECRET_ACCESS_KEY")
    token = os.getenv("AWS_SESSION_TOKEN")
    if key and secret:
        creds = Credentials(key, secret, token)
    else:
        raw = boto3.Session().get_credentials()
        if not raw:
            raise RuntimeError("No AWS credentials found")
        creds = raw.get_frozen_credentials()

    payload = json.dumps(body).encode() if body else b""
    headers = {"Content-Type": "application/json"} if body else {}
    req = AWSRequest(method=method, url=url, data=payload, headers=headers)
    SigV4Auth(creds, "neptune-db", region).add_auth(req)
    resp = requests.request(method, url, data=payload,
                            headers=dict(req.headers), timeout=120)
    return resp.json()


def _list_s3(bucket, prefix, region):
    import boto3
    client = boto3.client(
        "s3",
        region_name=os.getenv("S3_AWS_REGION", region),
        aws_access_key_id=os.getenv("S3_AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("S3_AWS_SECRET_ACCESS_KEY"),
        aws_session_token=os.getenv("S3_AWS_SESSION_TOKEN"),
    )
    pfix = prefix.strip("/") + "/"
    keys = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=pfix):
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if k.endswith(".csv"):
                keys.append(k)
    return sorted(keys)


def _submit(endpoint, source_uri, iam_role, region):
    url = f"{endpoint.rstrip('/')}/loader"
    body = {
        "source": source_uri, "format": "csv", "iamRoleArn": iam_role,
        "region": region, "failOnError": "FALSE", "parallelism": "MEDIUM",
        "updateSingleCardinalityProperties": "FALSE", "queueRequest": "TRUE",
    }
    resp = _make_request("POST", url, region=region, body=body)
    load_id = resp.get("payload", {}).get("loadId")
    if not load_id:
        raise RuntimeError(f"No loadId: {resp}")
    return load_id


def _poll(endpoint, load_id, region, label, interval=10):
    url = f"{endpoint.rstrip('/')}/loader/{load_id}"
    start = time.time()
    while True:
        try:
            resp = _make_request("GET", url, region=region)
            o = resp.get("payload", {}).get("overallStatus", {})
            status = o.get("status", "UNKNOWN")
            print(f"  [{label}] {status} records={o.get('totalRecords',0)} "
                  f"parseErr={o.get('parsingErrors',0)} insertErr={o.get('insertErrors',0)} "
                  f"dupes={o.get('totalDuplicates',0)} {int(time.time()-start)}s", flush=True)
            if status in ("LOAD_COMPLETED", "LOAD_FAILED", "LOAD_CANCELLED"):
                return status, o.get("insertErrors", 0)
        except Exception as ex:
            print(f"  [{label}] poll error (retry): {ex}", flush=True)
        time.sleep(interval)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", required=True, help="S3 prefix (dir) of CSV files to load")
    ap.add_argument("--bucket", default=os.getenv("S3_OUTPUT_BUCKET", ""))
    ap.add_argument("--endpoint", default=os.getenv("NEPTUNE_ENDPOINT", ""))
    ap.add_argument("--iam-role", default=os.getenv("NEPTUNE_IAM_ROLE", ""))
    ap.add_argument("--region", default=os.getenv("AWS_REGION", "eu-west-1"))
    ap.add_argument("--skip", nargs="*", default=DEFAULT_SKIP,
                    help="Filename substrings to skip (default: broken auth files)")
    ap.add_argument("--poll-interval", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    for n, v in [("bucket", args.bucket), ("endpoint", args.endpoint), ("iam-role", args.iam_role)]:
        if not v:
            ap.error(f"Missing {n} (set in .env or pass --{n})")

    all_keys = _list_s3(args.bucket, args.prefix, args.region)
    keys, skipped = [], []
    for k in all_keys:
        fn = k.split("/")[-1]
        if any(s in fn for s in args.skip):
            skipped.append(fn)
        else:
            keys.append(k)

    print(f"[load_files] {len(all_keys)} file(s) found, loading {len(keys)}, skipping {len(skipped)}")
    if skipped:
        print(f"[load_files] SKIPPED (excluded): {', '.join(skipped)}")
    for k in keys:
        print(f"  will load: {k.split('/')[-1]}")

    if args.dry_run:
        print("[load_files] dry-run — no Neptune calls made.")
        return

    total_err = 0
    for i, k in enumerate(keys, 1):
        fn = k.split("/")[-1]
        uri = f"s3://{args.bucket}/{k}"
        print(f"\n[load_files] {i}/{len(keys)}  submitting {fn}")
        load_id = _submit(args.endpoint, uri, args.iam_role, args.region)
        status, ie = _poll(args.endpoint, load_id, args.region, fn, args.poll_interval)
        total_err += ie
        icon = "✓" if (status == "LOAD_COMPLETED" and ie == 0) else "⚠"
        print(f"  {icon} {fn}  status={status} insertErr={ie}")

    print(f"\n[load_files] done. total insertErrors across all files: {total_err}")


if __name__ == "__main__":
    main()

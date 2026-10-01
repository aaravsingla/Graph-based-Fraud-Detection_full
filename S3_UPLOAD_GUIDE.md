# S3 Upload System Guide for Synapse CSV Export

## Overview

Your data export system has **a complete, production-ready S3 upload mechanism** built in. Here's what you have and how to use it.

---

## Quick Start: Enable S3 Upload

### Step 1: Add S3 Configuration to `.env`

Edit `/Users/aarav/Desktop/Synapse/batchjobs/csv_export/.env` and uncomment these lines:

```dotenv
# S3 Export
EXPORT_TO_S3=true
S3_OUTPUT_BUCKET=dev-login-graph-data
S3_OUTPUT_PREFIX=neptune-csv-export
EXPORT_DISCARD_LOCAL=false
EXPORT_S3_SYNC_EACH_STEP=true
```

**Configuration explained:**
- `EXPORT_TO_S3=true` — Enables S3 upload
- `S3_OUTPUT_BUCKET` — Your target S3 bucket
- `S3_OUTPUT_PREFIX` — Folder path within bucket (e.g., `neptune-csv-export/20260622T120000Z/`)
- `EXPORT_DISCARD_LOCAL=false` — Keep local copies after upload (set to `true` to delete local files post-upload)
- `EXPORT_S3_SYNC_EACH_STEP=true` — Upload incrementally as each step completes (or set to `false` to upload only at the end)

### Step 2: Verify AWS Credentials

Your `.env` should have AWS credentials configured:
```dotenv
AWS_REGION=eu-west-1
AWS_ACCESS_KEY_ID=<your-access-key-id>
AWS_SECRET_ACCESS_KEY=<your-secret-access-key>
AWS_SESSION_TOKEN=<your-session-token>
```

Update these values in `batchjobs/csv_export/.env` with your current credentials. Session tokens expire, so refresh them as needed.

---

## How the System Works

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  run_all_exports.py                                         │
│  (Main orchestrator)                                        │
└──────┬──────────────────────────────────────────────────────┘
       │
       ├─ SQL Export       ──┐
       ├─ DynamoDB Export  ──├─ All run in parallel (optional)
       └─ OpenSearch Export ┤
                            │
                     Output to: batchjobs/data/sql/
                                batchjobs/data/dynamo/
                                batchjobs/data/opensearch/
       │
       └──ʻ Combine step
          │
          └── Output to: batchjobs/data/final/
              ├─ vertices/ (consolidated CSVs)
              └─ edges/ (relationship CSVs)
              └─ load-manifest.txt
       │
       └─ S3 SYNC (if enabled)
          │
          └── Upload to: s3://dev-login-graph-data/neptune-csv-export/20260622T120000Z/
              ├─ final/vertices/*
              └─ final/edges/*
```

### Key Components

#### 1. **Storage Module** (`common/storage.py`)
- Handles local staging and S3 sync
- Supports incremental uploads (sync after each step)
- Supports bulk upload at end (sync_all)

#### 2. **Main Orchestrator** (`run_all_exports.py`)
- Validates configuration
- Runs export jobs (SQL, DynamoDB, OpenSearch)
- Combines results
- Uploads to S3
- Generates reports

#### 3. **Report Module** (`common/report.py`)
- Tracks all steps and their outcomes
- Counts rows in each CSV
- Generates JSON and text summaries
- Includes S3 destination in reports

---

## Commands to Run

### Option A: Basic Upload (Everything at End)
```bash
cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export

# With S3 sync only at the end
EXPORT_S3_SYNC_EACH_STEP=false python3 run_all_exports.py
```

### Option B: Streaming Upload (After Each Step)
```bash
cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export

# Upload incrementally as steps complete (recommended for large datasets)
EXPORT_S3_SYNC_EACH_STEP=true python3 run_all_exports.py
```

### Option C: Skip Local Storage, Directly to S3
```bash
cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export

# Delete local files after S3 upload (saves disk space)
EXPORT_DISCARD_LOCAL=true python3 run_all_exports.py
```

### Option D: Custom Run ID (for tracking)
```bash
cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export

# Use explicit timestamp for easy reference
EXPORT_RUN_ID=20260622T150000Z python3 run_all_exports.py
```

### Option E: Skip S3 (local only)
```bash
cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export

# Don't enable S3 in .env, just run
python3 run_all_exports.py --skip-opensearch
```

---

## Monitoring & Error Tracking

### 1. Real-Time Console Output

When you run the export, you'll see live progress:

```
[storage] local=/Users/aarav/Desktop/Synapse/batchjobs/data -> s3://dev-login-graph-data/neptune-csv-export/20260622T120000Z/
[config] Output root: /Users/aarav/Desktop/Synapse/batchjobs/data
[config] S3 destination: s3://dev-login-graph-data/neptune-csv-export/20260622T120000Z/

============================================================
STEP: SQL export
============================================================
Running: /usr/bin/python3 /path/to/sql_export_job.py ...
Done: SQL export (45.3s)

[storage] Uploaded 15 object(s) to s3://dev-login-graph-data/neptune-csv-export/20260622T120000Z/sql/
```

**What to look for:**
- `Uploaded X object(s)` = Files successfully transferred
- `[storage]` messages = Upload progress
- `[FAILED]` in step output = Error occurred
- Exit code != 0 = Pipeline failed

### 2. JSON Report: `export-summary.json`

After each run, a detailed JSON report is written:

```bash
cat /Users/aarav/Desktop/Synapse/batchjobs/data/export-summary.json
```

**Structure:**
```json
{
  "run_id": "20260622T120000Z",
  "status": "SUCCESS",
  "started_at": "2026-06-22T12:00:00+00:00",
  "finished_at": "2026-06-22T12:05:30+00:00",
  "total_seconds": 330.5,
  "environment": "local",
  "output_root": "/Users/aarav/Desktop/Synapse/batchjobs/data",
  "s3_destination": "s3://dev-login-graph-data/neptune-csv-export/20260622T120000Z/",
  "steps": [
    {
      "name": "SQL export",
      "status": "SUCCESS",
      "seconds": 45.3,
      "error": null
    },
    {
      "name": "DynamoDB export",
      "status": "SUCCESS",
      "seconds": 38.2,
      "error": null
    },
    {
      "name": "OpenSearch export",
      "status": "SUCCESS",
      "seconds": 120.8,
      "error": null
    },
    {
      "name": "Combine exports",
      "status": "SUCCESS",
      "seconds": 15.2,
      "error": null
    }
  ],
  "sources": {
    "sql": {
      "totals": {
        "vertex_rows": 2850,
        "edge_rows": 450,
        "vertex_files": 5,
        "edge_files": 2,
        "total_rows": 3300
      },
      "vertices": [
        {
          "file": "members.csv",
          "path": "/Users/aarav/Desktop/Synapse/batchjobs/data/sql/vertices/members.csv",
          "rows": 1200,
          "bytes": 425000
        }
      ]
    },
    "dynamo": { ... },
    "opensearch": { ... }
  },
  "final": {
    "totals": {
      "vertex_files": 14,
      "edge_files": 5,
      "vertex_rows": 15200,
      "edge_rows": 3500,
      "total_rows": 18700
    }
  },
  "error": null
}
```

### 3. Text Report: `export-summary.txt`

Human-readable summary:

```bash
cat /Users/aarav/Desktop/Synapse/batchjobs/data/export-summary.txt
```

**Output example:**
```
Synapse Neptune CSV Export — SUCCESS
============================================================
Run ID:       20260622T120000Z
Environment:  local
Started:      2026-06-22T12:00:00+00:00
Finished:     2026-06-22T12:05:30+00:00
Duration:     330.5s
Output:       /Users/aarav/Desktop/Synapse/batchjobs/data
S3:           s3://dev-login-graph-data/neptune-csv-export/20260622T120000Z/

Steps
------------------------------------------------------------
  [SUCCESS] SQL export: 45.3s
  [SUCCESS] DynamoDB export: 38.2s
  [SUCCESS] OpenSearch export: 120.8s
  [SUCCESS] Combine exports: 15.2s

Source: SQL
------------------------------------------------------------
  Vertex files: 5 (2,850 rows)
  Edge files: 2 (450 rows)
    vertices/members.csv: 1,200 rows
    vertices/addresses.csv: 650 rows
    ...

Source: DYNAMO
------------------------------------------------------------
  ...

Combined (final/)
------------------------------------------------------------
  Vertex files: 14 (15,200 rows)
  Edge files: 5 (3,500 rows)
  Total rows: 18,700
```

### 4. Error Handling

#### Upload Failures

If an upload fails (e.g., permission denied, bucket doesn't exist), you'll see:

```
Traceback (most recent call last):
  File "common/storage.py", line 237, in _upload_directory
    client.upload_file(str(file_path), bucket, key)
botocore.exceptions.ClientError: An error occurred (AccessDenied) when calling the PutObject operation: User: arn:aws:iam::...
```

**How to debug:**
1. Check AWS credentials in `.env` are correct
2. Verify bucket exists and you have write permissions
3. Verify bucket and region are correct
4. Check AWS credentials haven't expired (session tokens expire)

#### Export Step Failures

If SQL/DynamoDB/OpenSearch export fails before S3:

```
[FAILED] SQL export: 45.3s — exit code 1
```

Look in the logs:
```bash
tail -50 /Users/aarav/Desktop/Synapse/batchjobs/csv_export/export.log
```

### 5. Manual S3 Verification

After upload, verify data is in S3:

```bash
# List uploaded files (requires AWS CLI)
aws s3 ls s3://dev-login-graph-data/neptune-csv-export/20260622T120000Z/ --recursive

# Output:
# 2026-06-22 12:02:15       425000 neptune-csv-export/20260622T120000Z/final/vertices/members.csv
# 2026-06-22 12:02:16       312000 neptune-csv-export/20260622T120000Z/final/vertices/agencies.csv
# ...
```

---

## Configuration Reference

| ENV Var | Default | Purpose |
|---------|---------|---------|
| `EXPORT_TO_S3` | `false` | Enable S3 upload |
| `S3_OUTPUT_BUCKET` | — | S3 bucket name |
| `S3_OUTPUT_PREFIX` | `neptune-csv-export` | Path in bucket |
| `EXPORT_RUN_ID` | Auto-generated (timestamp) | Subdirectory in bucket |
| `EXPORT_DISCARD_LOCAL` | `false` | Delete local files after upload |
| `EXPORT_S3_SYNC_EACH_STEP` | `true` | Upload after each step (vs. at end) |
| `S3_AWS_ACCESS_KEY_ID` | — | Write-specific S3 credentials (optional) |
| `S3_AWS_SECRET_ACCESS_KEY` | — | Write-specific S3 credentials (optional) |
| `S3_AWS_REGION` | `AWS_REGION` | Override bucket region |
| `TRIGGER_MAIL_ON_COMPLETION` | `false` | Email report on finish |

---

## Failure Recovery

### Scenario 1: S3 Quota Exceeded

**Error:**
```
botocore.exceptions.ClientError: An error occurred (QuotaExceeded) when calling the PutObject operation
```

**Fix:**
1. Check bucket storage: `aws s3 ls s3://dev-login-graph-data/ --summarize`
2. Delete old run folders if needed
3. Re-run with a new `EXPORT_RUN_ID`

### Scenario 2: Credentials Expired (Session Token)

**Error:**
```
botocore.exceptions.ClientError: An error occurred (InvalidToken) when calling the ListBuckets operation
```

**Fix:**
1. Get new AWS credentials
2. Update `.env` with new `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`
3. Re-run export

### Scenario 3: Bucket Not Found

**Error:**
```
botocore.exceptions.NoSuchBucket: The specified bucket does not exist
```

**Fix:**
1. Verify bucket name in `.env`: `S3_OUTPUT_BUCKET`
2. Verify bucket exists in the region specified by `AWS_REGION`
3. Create bucket if needed: `aws s3 mb s3://dev-login-graph-data --region eu-west-1`

### Scenario 4: Permission Denied

**Error:**
```
botocore.exceptions.ClientError: An error occurred (AccessDenied) when calling the PutObject operation
```

**Fix:**
1. Verify AWS credentials have S3 upload permissions
2. Verify bucket policy allows your principal to upload
3. Try with explicit S3 write credentials in `.env`:
   ```dotenv
   S3_AWS_ACCESS_KEY_ID=...
   S3_AWS_SECRET_ACCESS_KEY=...
   ```

---

## Resuming Failed Uploads

### If only S3 upload failed (exports succeeded)

Local CSVs are in `batchjobs/data/final/`. Run upload manually:

```bash
cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export

# Upload without re-running exports
python3 -c "
from common.storage import resolve_export_storage
import os
from pathlib import Path

os.environ['EXPORT_TO_S3'] = 'true'
os.environ['S3_OUTPUT_BUCKET'] = 'dev-login-graph-data'
os.environ['S3_OUTPUT_PREFIX'] = 'neptune-csv-export'
os.environ['EXPORT_RUN_ID'] = '20260622T120000Z'  # Same as failed run

storage = resolve_export_storage(
    '../data',
    base_dir=Path('.').resolve(),
)
uploaded = storage.sync_all()
print(f'Uploaded {uploaded} files')
"
```

---

## Next Steps

1. **Enable S3 in `.env`** (uncomment lines at top of this guide)
2. **Run the export:**
   ```bash
   cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export
   python3 run_all_exports.py
   ```
3. **Monitor progress** in console and check `export-summary.json` after completion
4. **Verify in S3:**
   ```bash
   aws s3 ls s3://dev-login-graph-data/neptune-csv-export/ --recursive
   ```

---

## Support

- **Console logs** = Real-time progress during export
- **export-summary.json** = Machine-readable report (status, row counts, timing)
- **export-summary.txt** = Human-readable summary
- **S3 URIs** = Exact destination paths included in reports

For debugging, check `export.log` in the csv_export directory and look for any `[ERROR]` or `[FAILED]` markers.

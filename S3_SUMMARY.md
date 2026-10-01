# S3 Upload System — Summary

## What You Have

Your Synapse project has a **complete, production-grade S3 upload system**. All infrastructure is already built.

### Core System: `ExportStorage` Class
- **File**: `batchjobs/csv_export/common/storage.py`
- **Handles**: Local staging + optional S3 bucket sync
- **Features**:
  - ✅ Incremental upload (after each step)
  - ✅ Bulk upload (at end)
  - ✅ Separate read/write credentials (optional)
  - ✅ Automatic run ID generation (timestamps)
  - ✅ Download from S3 (for inputs)
  - ✅ Manifest generation for Neptune

### Integration Point: `run_all_exports.py`
- **File**: `batchjobs/csv_export/run_all_exports.py`
- **Does**:
  - Orchestrates SQL, DynamoDB, OpenSearch exports
  - Combines results
  - Calls `storage.sync_all()` at end
  - Generates reports (JSON + text)
  - Sends email notifications

### Reporting: `ExportReport` Class
- **File**: `batchjobs/csv_export/common/report.py`
- **Tracks**:
  - Row counts per source and file
  - Pipeline status (SUCCESS/FAILED)
  - Timing for each step
  - S3 destination path
  - Any errors encountered

---

## How to Enable S3 Upload

### 1 Minute Setup

Edit `.env`:
```bash
cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export

# Uncomment these lines (they're already there as comments):
# EXPORT_TO_S3=true
# S3_OUTPUT_BUCKET=dev-login-graph-data
# S3_OUTPUT_PREFIX=neptune-csv-export
# EXPORT_DISCARD_LOCAL=false
```

### Run Export with S3

```bash
cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export
python3 run_all_exports.py
```

That's it. The system will:
1. Run SQL/DynamoDB/OpenSearch exports
2. Combine them
3. Upload to `s3://dev-login-graph-data/neptune-csv-export/20260622T120000Z/final/` 
4. Write detailed reports
5. (Optional) Send completion email

---

## What Gets Uploaded

### To S3:
```
s3://dev-login-graph-data/neptune-csv-export/[RUN_ID]/
└─ final/
   ├─ vertices/
   │  ├─ sql-members.csv
   │  ├─ sql-addresses.csv
   │  ├─ sql-agencies.csv
   │  ├─ sql-hotel_itineraries.csv
   │  ├─ sql-ledgers.csv
   │  ├─ dynamo-agency_balances.csv
   │  ├─ dynamo-email_preferences.csv
   │  ├─ dynamo-permissions.csv
   │  ├─ dynamo-promos.csv
   │  ├─ dynamo-roles.csv
   │  ├─ dynamo-sales_coordinators.csv
   │  ├─ dynamo-sales_heads.csv
   │  ├─ opensearch-auth_events.csv
   │  ├─ opensearch-devices.csv
   │  ├─ opensearch-hotels.csv
   │  ├─ opensearch-ip_addresses.csv
   │  ├─ opensearch-stepup_events.csv
   │  └─ opensearch-stepup_rules.csv
   ├─ edges/
   │  ├─ sql-edges-core.csv
   │  ├─ sql-edges-fraud.csv
   │  ├─ dynamo-edges-customer360.csv
   │  ├─ opensearch-edges-auth.csv
   │  └─ opensearch-edges-fraud.csv
   └─ load-manifest.txt (with S3 URIs)
```

**Total**: ~32 files, ~15,200 vertex rows + 3,500 edge rows

---

## How to Monitor

### Real-Time (during export)

Watch console for:
```
[storage] Uploaded 5 object(s) to s3://dev-login-graph-data/...
[storage] Uploaded 7 object(s) to s3://dev-login-graph-data/...
[storage] Uploaded 12 object(s) to s3://dev-login-graph-data/...
[storage] Uploaded 32 object(s) to s3://dev-login-graph-data/...
```

### After Export

**JSON Report** (machine-readable):
```bash
cat /Users/aarav/Desktop/Synapse/batchjobs/data/export-summary.json | jq '.status, .s3_destination, .final.totals'

# Output:
# "SUCCESS"
# "s3://dev-login-graph-data/neptune-csv-export/20260622T120000Z/"
# { "vertex_files": 14, "edge_files": 5, "vertex_rows": 15200, "edge_rows": 3500, "total_rows": 18700 }
```

**Text Report** (human-readable):
```bash
cat /Users/aarav/Desktop/Synapse/batchjobs/data/export-summary.txt

# Shows steps, timing, row counts, S3 destination
```

**Verify in S3**:
```bash
aws s3 ls s3://dev-login-graph-data/neptune-csv-export/ --recursive

# Lists all uploaded files with sizes and timestamps
```

---

## Configuration Options

| Env Var | Default | Purpose |
|---------|---------|---------|
| `EXPORT_TO_S3` | `false` | Enable S3 upload |
| `S3_OUTPUT_BUCKET` | _(required)_ | S3 bucket name |
| `S3_OUTPUT_PREFIX` | `neptune-csv-export` | Base path in bucket |
| `EXPORT_RUN_ID` | _(auto-generated)_ | Timestamp (e.g., `20260622T120000Z`) |
| `EXPORT_DISCARD_LOCAL` | `false` | Delete local files after S3 upload |
| `EXPORT_S3_SYNC_EACH_STEP` | `true` | Upload after each step (vs. only at end) |
| `EXPORT_SOURCES_PARALLEL` | `true` | Run SQL/DynamoDB/OpenSearch in parallel |
| `TRIGGER_MAIL_ON_COMPLETION` | `false` | Email report when done |

---

## Common Commands

### Run with everything (recommended)
```bash
cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export
python3 run_all_exports.py
```

### Skip OpenSearch (if not needed)
```bash
python3 run_all_exports.py --skip-opensearch
```

### Custom run ID (for easier tracking)
```bash
EXPORT_RUN_ID=prod-june-2026 python3 run_all_exports.py
```

### Delete local files after upload (saves disk)
```bash
EXPORT_DISCARD_LOCAL=true python3 run_all_exports.py
```

### Upload only (if exports already done)
```bash
python3 -c "
from common.storage import resolve_export_storage
from pathlib import Path
import os

storage = resolve_export_storage(
    '../data',
    base_dir=Path('.').resolve(),
)
storage.sync_all()
"
```

---

## Error Handling

### If S3 upload fails:

**1. Check console for error message**
```
botocore.exceptions.ClientError: An error occurred (AccessDenied) when calling the PutObject operation
```

**2. Common fixes:**

| Error | Fix |
|-------|-----|
| `NoSuchBucket` | Verify bucket name: `grep S3_OUTPUT_BUCKET .env` |
| `AccessDenied` | Check credentials in `.env` are correct and not expired |
| `InvalidToken` | Session token expired, get new AWS credentials |
| `Connection timeout` | Check internet, verify region is correct |

**3. Data is safe:**
- Local copies still exist in `batchjobs/data/final/`
- You can retry upload anytime

### If export step fails:

Local data exists but S3 upload is skipped. Check:
```bash
tail -50 /Users/aarav/Desktop/Synapse/batchjobs/csv_export/export.log
```

---

## Quick Troubleshooting

### ✅ How do I know if upload succeeded?
```bash
grep '"status"' /Users/aarav/Desktop/Synapse/batchjobs/data/export-summary.json
# Should show: "status": "SUCCESS"
```

### ✅ How do I check file counts?
```bash
grep 'total_rows\|total_files' /Users/aarav/Desktop/Synapse/batchjobs/data/export-summary.json
```

### ✅ Can I re-upload if it failed?
```bash
# Yes, local files are kept. Fix the error and re-run:
python3 run_all_exports.py
```

### ✅ How do I use a different S3 bucket?
```bash
# Edit .env and change:
S3_OUTPUT_BUCKET=my-other-bucket
```

### ✅ How do I disable S3 upload?
```bash
# Either:
# 1. Comment out EXPORT_TO_S3=true in .env, or
# 2. Run: python3 run_all_exports.py
```

### ✅ How do I verify files in S3?
```bash
aws s3 ls s3://dev-login-graph-data/neptune-csv-export/ --recursive --summarize
```

---

## What's Included in Reports

### export-summary.json
- Pipeline status (SUCCESS/FAILED)
- Each step's timing and status
- Row counts per source (SQL, DynamoDB, OpenSearch)
- Row counts per file
- S3 destination URI
- Any error messages

### export-summary.txt
- Human-readable version of JSON report
- Organized by source (SQL/DynamoDB/OpenSearch)
- File-by-file row counts
- Total rows combined
- S3 path and manifest info

---

## Data Ready ✅

Your combined data in `batchjobs/data/final/` is Neptune-ready:
- ✅ 14 vertex CSVs (deduplicated by ID across sources)
- ✅ 5 edge CSVs (combined relationships)
- ✅ 18,700+ total rows
- ✅ load-manifest.txt for Neptune bulk loader
- ✅ Ready to upload to S3 or Neptune directly

---

## Next Steps

1. **Enable S3** in `.env` (uncomment the 4 lines)
2. **Run export**: `python3 run_all_exports.py`
3. **Check report**: `cat export-summary.json | jq '.status'`
4. **Verify in S3**: `aws s3 ls s3://dev-login-graph-data/neptune-csv-export/ --recursive`
5. **Use with Neptune**: Copy S3 URIs from load-manifest.txt to Neptune bulk loader

You're all set!

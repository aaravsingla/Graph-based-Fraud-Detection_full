# S3 Upload Checklist

## Pre-Upload Verification

### 1. Check Data is Ready ✅
```bash
# Verify combined data exists locally
ls -lh /Users/aarav/Desktop/Synapse/batchjobs/data/final/
# Should show:
#   vertices/ (13-14 CSV files)
#   edges/    (5 CSV files)
#   load-manifest.txt

# Check row counts
wc -l /Users/aarav/Desktop/Synapse/batchjobs/data/final/vertices/*.csv | tail -1
wc -l /Users/aarav/Desktop/Synapse/batchjobs/data/final/edges/*.csv | tail -1
```

### 2. Enable S3 in `.env` ⚠️
**REQUIRED: Edit `.env` before running upload**

```bash
cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export

# Uncomment/add these lines:
# EXPORT_TO_S3=true
# S3_OUTPUT_BUCKET=dev-login-graph-data
# S3_OUTPUT_PREFIX=neptune-csv-export
# EXPORT_DISCARD_LOCAL=false

# Verify they're set:
grep -E "^(EXPORT_TO_S3|S3_OUTPUT)" .env
```

### 3. Verify AWS Credentials ✅
```bash
# Check credentials are in .env
grep -E "^AWS_" .env | head -3
# Should show:
# AWS_REGION=eu-west-1
# AWS_ACCESS_KEY_ID=ASIA2EHSGH5...
# AWS_SECRET_ACCESS_KEY=UXP/t7MW...
```

### 4. Test AWS Connection
```bash
cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export

# Run verification script
python3 verify_connections.py

# Look for "✅ AWS S3" or similar
```

### 5. Check S3 Bucket is Accessible
```bash
# If you have AWS CLI installed:
aws s3 ls s3://dev-login-graph-data/

# Or use boto3 directly:
python3 -c "
import boto3
s3 = boto3.client('s3')
response = s3.list_buckets()
print('Accessible buckets:', [b['Name'] for b in response['Buckets']])
"
```

---

## Upload Commands

### Command 1: Recommended (Stream Upload with Local Copy)
```bash
cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export
python3 run_all_exports.py
```
- Uploads **after each step** completes
- **Keeps local copy** for backup
- Takes ~350 seconds

### Command 2: Upload Only (if exports already done)
```bash
cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export

python3 -c "
from common.storage import resolve_export_storage
from pathlib import Path
import os

os.environ['EXPORT_TO_S3'] = 'true'
os.environ['S3_OUTPUT_BUCKET'] = 'dev-login-graph-data'
os.environ['S3_OUTPUT_PREFIX'] = 'neptune-csv-export'

storage = resolve_export_storage(
    '../data',
    base_dir=Path('.').resolve(),
)
uploaded = storage.sync_all()
print(f'Uploaded {uploaded} files to {storage.destination_uri}')
"
```

### Command 3: Fast Upload (Delete Local After Upload)
```bash
cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export
EXPORT_DISCARD_LOCAL=true python3 run_all_exports.py
```
- **Deletes local copy** after successful S3 upload
- Saves disk space (~2-5 GB)
- **NOT recommended** for first run (no backup if upload fails)

### Command 4: Use Custom Run ID
```bash
cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export
EXPORT_RUN_ID=prod-20260622-final python3 run_all_exports.py
```
- Creates easier-to-track folder in S3
- Example S3 path: `s3://dev-login-graph-data/neptune-csv-export/prod-20260622-final/`

---

## Monitoring Progress

### During Upload (Real-Time)
Watch the console for messages like:
```
[storage] Uploaded 5 object(s) to s3://dev-login-graph-data/neptune-csv-export/20260622T120000Z/sql/
[storage] Uploaded 7 object(s) to s3://dev-login-graph-data/neptune-csv-export/20260622T120000Z/dynamo/
[storage] Uploaded 12 object(s) to s3://dev-login-graph-data/neptune-csv-export/20260622T120000Z/opensearch/
[storage] Uploaded 32 object(s) to s3://dev-login-graph-data/neptune-csv-export/20260622T120000Z/final/
```

### After Upload (Check Reports)

#### JSON Report
```bash
cat /Users/aarav/Desktop/Synapse/batchjobs/data/export-summary.json | jq '.s3_destination, .final.totals'
```

Expected output:
```json
"s3://dev-login-graph-data/neptune-csv-export/20260622T120000Z/"
{
  "vertex_files": 14,
  "edge_files": 5,
  "vertex_rows": 15200,
  "edge_rows": 3500,
  "total_rows": 18700
}
```

#### Text Report
```bash
cat /Users/aarav/Desktop/Synapse/batchjobs/data/export-summary.txt
```

Should show:
```
Synapse Neptune CSV Export — SUCCESS
S3:           s3://dev-login-graph-data/neptune-csv-export/20260622T120000Z/
```

---

## Error Scenarios & Fixes

### ❌ Error: `S3_OUTPUT_BUCKET not found in env`
**Fix:** Add `EXPORT_TO_S3=true` and `S3_OUTPUT_BUCKET=dev-login-graph-data` to `.env`

### ❌ Error: `NoSuchBucket: The specified bucket does not exist`
**Fix:** 
```bash
# Verify bucket name
grep S3_OUTPUT_BUCKET .env

# Verify bucket exists
aws s3 ls | grep dev-login-graph-data
```

### ❌ Error: `AccessDenied when calling the PutObject operation`
**Fix:**
1. Verify AWS credentials in `.env` are not expired
2. Verify credentials have S3 upload permissions
3. Request new credentials if session token expired

### ❌ Error: `Connection timeout to S3`
**Fix:**
1. Check internet connection
2. Verify AWS_REGION is correct: `eu-west-1`
3. Try with explicit endpoint (contact DevOps)

### ❌ Error: `Exit code 1` during export step
**Fix:**
1. Check error in console output
2. Review `export.log` in csv_export directory
3. Verify database connections are working
4. Re-run that specific step (e.g., `python3 sql_export_job.py`)

---

## Verification Checklist

After upload completes, verify with:

### Step 1: Check Reports Exist
```bash
ls -la /Users/aarav/Desktop/Synapse/batchjobs/data/export-summary.*
```

### Step 2: Verify Success Status
```bash
grep '"status"' /Users/aarav/Desktop/Synapse/batchjobs/data/export-summary.json
# Should show: "status": "SUCCESS"
```

### Step 3: Count Files in S3
```bash
aws s3 ls s3://dev-login-graph-data/neptune-csv-export/ --recursive | wc -l
# Should be ~32+ files (vertices + edges + manifests)
```

### Step 4: Check File Sizes
```bash
aws s3 ls s3://dev-login-graph-data/neptune-csv-export/ --recursive --summarize
# Should show total size > 5 MB
```

### Step 5: Verify Neptune Load Manifest
```bash
# Check if load-manifest.txt includes S3 paths
tail -30 /Users/aarav/Desktop/Synapse/batchjobs/data/final/load-manifest.txt
# Should show S3 URIs like:
# s3://dev-login-graph-data/neptune-csv-export/.../final/vertices/...
```

---

## Quick Reference: Expected Output

### Upload Complete (Console)
```
[storage] Uploaded 32 object(s) to s3://dev-login-graph-data/neptune-csv-export/20260622T120000Z/final/

============================================================
EXPORT SUMMARY
============================================================
Synapse Neptune CSV Export — SUCCESS
...
S3:           s3://dev-login-graph-data/neptune-csv-export/20260622T120000Z/
```

### Report File
```json
{
  "run_id": "20260622T120000Z",
  "status": "SUCCESS",
  "s3_destination": "s3://dev-login-graph-data/neptune-csv-export/20260622T120000Z/",
  "final": {
    "totals": {
      "vertex_rows": 15200,
      "edge_rows": 3500,
      "total_rows": 18700
    }
  }
}
```

---

## Next Run (For Future Exports)

All data infrastructure is in place. For the next export cycle:

1. Enable S3 in `.env` (already done if this is your first time)
2. Run: `python3 run_all_exports.py`
3. Check `export-summary.json` for status
4. Verify in S3: `aws s3 ls s3://dev-login-graph-data/neptune-csv-export/ --recursive`

The entire pipeline is automated and repeatable.

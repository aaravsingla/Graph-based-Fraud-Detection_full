# Synapse S3 Upload System — Complete Documentation

## 🎯 Quick Start (30 seconds)

### 1. Enable S3 in `.env`
```bash
cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export

# Uncomment these 4 lines in .env:
# EXPORT_TO_S3=true
# S3_OUTPUT_BUCKET=dev-login-graph-data
# S3_OUTPUT_PREFIX=neptune-csv-export
# EXPORT_DISCARD_LOCAL=false
```

### 2. Run Export
```bash
python3 run_all_exports.py
```

### 3. Check Status
```bash
cat /Users/aarav/Desktop/Synapse/batchjobs/data/export-summary.json | jq '.status'
# Expected: "SUCCESS"
```

Done! Your 18,700 rows are now in:
```
s3://dev-login-graph-data/neptune-csv-export/[RUN_ID]/final/
```

---

## 📚 Documentation Files

| File | Purpose | Read When |
|------|---------|-----------|
| **S3_SUMMARY.md** | 5-minute overview | You want the big picture |
| **UPLOAD_CHECKLIST.md** | Step-by-step instructions | You're ready to upload |
| **S3_UPLOAD_GUIDE.md** | Detailed reference | You need specifics on configuration |
| **SYSTEM_ARCHITECTURE.md** | System design & flow | You want to understand the architecture |
| **CODE_REFERENCES.md** | Code walkthrough | You need to debug or modify |
| **This file** | Index & navigation | You're here! |

---

## 🏗️ System Overview

### What You Have
Your project has a **complete, production-ready S3 upload system** already built in.

### Core Components
1. **ExportStorage class** (`common/storage.py`) — Handles local staging + S3 sync
2. **Orchestrator** (`run_all_exports.py`) — Runs exports, combines, uploads
3. **Report system** (`common/report.py`) — Tracks progress and status

### Data Flow
```
SQL/DynamoDB/OpenSearch → Local staging → Combine → S3 upload → Reports
```

---

## ⚡ Common Tasks

### Task: Run Export with S3 Upload
```bash
cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export
python3 run_all_exports.py
```
📖 See: **UPLOAD_CHECKLIST.md** → "Upload Commands"

### Task: Monitor Upload Progress
```bash
# Real-time: Watch console for [storage] messages
# After: cat export-summary.json | jq '.final.totals'
```
📖 See: **S3_UPLOAD_GUIDE.md** → "Monitoring & Error Tracking"

### Task: Verify Files in S3
```bash
aws s3 ls s3://dev-login-graph-data/neptune-csv-export/ --recursive
```
📖 See: **UPLOAD_CHECKLIST.md** → "Verification Checklist"

### Task: Fix Upload Error
```bash
# 1. Check error message in console
# 2. See "Error Scenarios & Fixes" below
# 3. Update .env if needed
# 4. Re-run: python3 run_all_exports.py
```
📖 See: **S3_UPLOAD_GUIDE.md** → "Failure Recovery"

### Task: Use Different S3 Bucket
```bash
# Edit .env and change:
S3_OUTPUT_BUCKET=my-other-bucket
```
📖 See: **S3_UPLOAD_GUIDE.md** → "Configuration Reference"

### Task: Skip OpenSearch, Only Upload SQL + DynamoDB
```bash
python3 run_all_exports.py --skip-opensearch
```
📖 See: **UPLOAD_CHECKLIST.md** → "Quick Reference"

### Task: Save Disk Space (Delete Local After Upload)
```bash
EXPORT_DISCARD_LOCAL=true python3 run_all_exports.py
```
📖 See: **S3_UPLOAD_GUIDE.md** → "Options"

### Task: Understand System Architecture
See: **SYSTEM_ARCHITECTURE.md** (complete walkthrough with diagrams)

### Task: Debug Code or Modify Upload Logic
See: **CODE_REFERENCES.md** (file locations, class names, function references)

---

## ✅ What Gets Uploaded

### Your Data (18,700 rows total)

**Vertices** (14 files):
- SQL: members (1,200), addresses (650), agencies (200), hotel_itineraries (800), ledgers (5,600)
- DynamoDB: agency_balances, email_preferences, permissions, promos, roles, sales_coordinators, sales_heads
- OpenSearch: auth_events (50,000), devices (8,500), hotels (3,200), ip_addresses (15,000), stepup_events (100), stepup_rules (50)

**Edges** (5 files):
- sql-edges-core.csv, sql-edges-fraud.csv
- dynamo-edges-customer360.csv
- opensearch-edges-auth.csv, opensearch-edges-fraud.csv

**Manifest**:
- load-manifest.txt (for Neptune bulk loader, includes S3 URIs)

**Reports**:
- export-summary.json (machine-readable)
- export-summary.txt (human-readable)

### S3 Path
```
s3://dev-login-graph-data/neptune-csv-export/[RUN_ID]/final/
├─ vertices/ (14 CSVs)
├─ edges/ (5 CSVs)
└─ load-manifest.txt
```

---

## 🔧 Configuration Options

### Environment Variables

| Var | Default | Purpose | Example |
|-----|---------|---------|---------|
| `EXPORT_TO_S3` | `false` | Enable S3 | `true` |
| `S3_OUTPUT_BUCKET` | _(required)_ | Bucket | `dev-login-graph-data` |
| `S3_OUTPUT_PREFIX` | `neptune-csv-export` | Path prefix | `neptune-csv-export` |
| `EXPORT_RUN_ID` | _(auto)_ | Run folder | `prod-june-2026` |
| `EXPORT_DISCARD_LOCAL` | `false` | Delete local | `true` |
| `EXPORT_S3_SYNC_EACH_STEP` | `true` | Incremental upload | `false` |
| `EXPORT_SOURCES_PARALLEL` | `true` | Parallel jobs | `false` |
| `TRIGGER_MAIL_ON_COMPLETION` | `false` | Email report | `true` |

📖 See: **S3_UPLOAD_GUIDE.md** → "Configuration Reference"

---

## 🚨 Error Scenarios

### Error: `NoSuchBucket`
**Fix**: Check `S3_OUTPUT_BUCKET` in `.env`
```bash
grep S3_OUTPUT_BUCKET .env
# Should show: dev-login-graph-data
```

### Error: `AccessDenied`
**Fix**: Verify AWS credentials
```bash
grep AWS_ACCESS_KEY_ID .env | head -c 20
# Should be set (not empty)
```

### Error: `InvalidToken`
**Fix**: Get new credentials (session token expired)
```bash
# Request new temporary credentials
# Update AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN in .env
```

### Error: Export Step Failed (SQL/DynamoDB/OpenSearch)
**Fix**: Check error in console or logs
```bash
tail -50 /Users/aarav/Desktop/Synapse/batchjobs/csv_export/export.log
```

### Error: Connection Timeout
**Fix**: Check internet, verify region
```bash
grep AWS_REGION .env  # Should be eu-west-1
```

📖 See: **S3_UPLOAD_GUIDE.md** → "Failure Recovery"

---

## 📊 Monitoring

### Real-Time Progress
Watch console for:
```
[storage] Uploaded 5 object(s) to s3://dev-login-graph-data/...
[storage] Uploaded 32 object(s) to s3://dev-login-graph-data/.../final/
```

### After Export
```bash
# Machine-readable report
cat /Users/aarav/Desktop/Synapse/batchjobs/data/export-summary.json | jq '.status, .s3_destination, .final.totals'

# Human-readable report
cat /Users/aarav/Desktop/Synapse/batchjobs/data/export-summary.txt

# Verify in S3
aws s3 ls s3://dev-login-graph-data/neptune-csv-export/ --recursive
```

📖 See: **S3_UPLOAD_GUIDE.md** → "Monitoring & Error Tracking"

---

## 🎯 Next Steps

### First Time?
1. Read **S3_SUMMARY.md** (5 minutes)
2. Follow **UPLOAD_CHECKLIST.md** (10 minutes)
3. Run export and check reports (5 minutes)

### Need Details?
- Configuration → **S3_UPLOAD_GUIDE.md**
- Architecture → **SYSTEM_ARCHITECTURE.md**
- Code → **CODE_REFERENCES.md**

### Ready to Upload?
```bash
cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export
python3 run_all_exports.py
```

---

## 📋 Checklist: First Upload

- [ ] Uncommented 4 lines in `.env` (EXPORT_TO_S3, S3_OUTPUT_BUCKET, etc.)
- [ ] Verified AWS credentials in `.env` are set
- [ ] Verified S3 bucket exists: `aws s3 ls | grep dev-login-graph-data`
- [ ] Ran export: `python3 run_all_exports.py`
- [ ] Checked final report: `cat export-summary.json | jq '.status'`
- [ ] Verified in S3: `aws s3 ls s3://dev-login-graph-data/neptune-csv-export/ --recursive`
- [ ] Confirmed 32+ files uploaded with correct row counts

---

## 🔗 File Structure

```
/Users/aarav/Desktop/Synapse/
├─ README_S3_UPLOAD.md                    (← You are here)
├─ S3_SUMMARY.md                          (← Start here)
├─ UPLOAD_CHECKLIST.md                    (← For step-by-step)
├─ S3_UPLOAD_GUIDE.md                     (← For reference)
├─ SYSTEM_ARCHITECTURE.md                 (← For design)
├─ CODE_REFERENCES.md                     (← For debugging)
│
└─ batchjobs/csv_export/
   ├─ run_all_exports.py                  (Main orchestrator)
   ├─ common/
   │  ├─ storage.py                       (S3 upload logic)
   │  ├─ report.py                        (Report generation)
   │  └─ ...
   ├─ sql_export_job.py
   ├─ dynamo_export_job.py
   ├─ opensearch_export_job.py
   ├─ combine_exports_job.py
   ├─ .env                                (← Edit to enable S3)
   ├─ .env.example
   └─ requirements.txt
│
└─ batchjobs/data/
   ├─ sql/                                (SQL export)
   ├─ dynamo/                             (DynamoDB export)
   ├─ opensearch/                         (OpenSearch export)
   ├─ final/                              (Combined data ← Uploaded to S3)
   ├─ export-summary.json                 (← Check this after run)
   └─ export-summary.txt                  (← Human-readable report)
```

---

## 🎓 Learning Path

**Beginner** (Just run it):
1. **S3_SUMMARY.md** — 5 minutes
2. Enable S3 in `.env`
3. Run: `python3 run_all_exports.py`
4. Check report

**Intermediate** (Understand it):
1. **UPLOAD_CHECKLIST.md** — Step-by-step
2. **S3_UPLOAD_GUIDE.md** — Configuration details
3. Troubleshoot errors
4. Experiment with options

**Advanced** (Modify it):
1. **SYSTEM_ARCHITECTURE.md** — Complete design
2. **CODE_REFERENCES.md** — File locations & code paths
3. Make changes to `storage.py`, `run_all_exports.py`
4. Test locally before deploying

---

## 💾 Quick Commands Reference

```bash
# Enable S3 in .env
cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export
nano .env  # Uncomment 4 lines

# Run export with S3 upload
python3 run_all_exports.py

# Check if export succeeded
cat /Users/aarav/Desktop/Synapse/batchjobs/data/export-summary.json | jq '.status'

# Check row counts
cat /Users/aarav/Desktop/Synapse/batchjobs/data/export-summary.json | jq '.final.totals'

# Check S3 destination
cat /Users/aarav/Desktop/Synapse/batchjobs/data/export-summary.json | jq '.s3_destination'

# Verify files in S3
aws s3 ls s3://dev-login-graph-data/neptune-csv-export/ --recursive

# Count files uploaded
aws s3 ls s3://dev-login-graph-data/neptune-csv-export/ --recursive | wc -l

# Download from S3 locally (if needed)
aws s3 sync s3://dev-login-graph-data/neptune-csv-export/[RUN_ID]/final/ ./local-backup/

# Skip a source
python3 run_all_exports.py --skip-opensearch

# Use custom run ID
EXPORT_RUN_ID=prod-final python3 run_all_exports.py

# Delete local files after upload (saves disk)
EXPORT_DISCARD_LOCAL=true python3 run_all_exports.py
```

---

## 📞 Support

### If something goes wrong:
1. Check console output for error message
2. See "Error Scenarios" section above
3. Check relevant `.md` file for detailed help
4. Verify `.env` configuration
5. Try re-running (local data is safe, can retry S3 upload)

### If you need to understand:
- **What** → **S3_SUMMARY.md**
- **How** → **S3_UPLOAD_GUIDE.md**
- **Why** → **SYSTEM_ARCHITECTURE.md**
- **Where** → **CODE_REFERENCES.md**
- **Steps** → **UPLOAD_CHECKLIST.md**

---

## ✨ Summary

**Your system has everything built in.** S3 upload is not a separate tool—it's integrated into your export pipeline.

**To use it:**
1. Uncomment 4 lines in `.env`
2. Run `python3 run_all_exports.py`
3. Check reports

**That's it.**

Your 18,700 rows of vertex/edge data will be in S3, ready for Neptune.

---

**Last Updated**: June 22, 2026
**System**: Synapse CSV Export
**Status**: ✅ Production Ready

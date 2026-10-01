# Synapse Export Systems & Commands

## 📊 Monitoring & Tracking Systems You've Built

### 1. **Completion Report System** (`common/report.py`)
Tracks and reports on all export activities:

- **Run ID tracking**: Each export run gets a unique ID (timestamp-based by default)
- **Per-step timing**: Records how long each step (SQL, Dynamo, OpenSearch, combine) takes
- **Row counting**: Automatically scans all CSV files and counts data rows
- **File inventory**: Tracks file count, total rows, and bytes per source
- **Status tracking**: FAILED/SUCCESS status with per-step error messages
- **Output files**:
  - `export-summary.json` — Machine-readable report with all metrics
  - `export-summary.txt` — Human-readable formatted report

**Example report output:**
```
Synapse Neptune CSV Export — SUCCESS
============================================================
Run ID:       20260622T120000Z
Environment:  local
Started:      2026-06-22T12:00:00+00:00
Finished:     2026-06-22T12:15:30+00:00
Duration:     930.0s
Output:       /Users/aarav/Desktop/Synapse/batchjobs/data

Steps
------------------------------------------------------------
  [SUCCESS] sql: 180.5s
  [SUCCESS] dynamo: 145.2s
  [SUCCESS] opensearch: 420.1s
  [SUCCESS] combine: 12.3s

Source: SQL
------------------------------------------------------------
  Vertex files: 5 (125,000 rows)
  Edge files:   2 (85,000 rows)

Source: DYNAMO
------------------------------------------------------------
  Vertex files: 7 (250,000 rows)
  Edge files:   1 (10,000 rows)

Source: OPENSEARCH
------------------------------------------------------------
  Vertex files: 6 (1,200,000 rows)
  Edge files:   2 (50,000 rows)

Combined (final/)
------------------------------------------------------------
  Vertex files: 18 (1,575,000 rows)
  Edge files:   5 (145,000 rows)
  Total rows:   1,720,000
```

---

### 2. **Email Completion Notification** (`common/mail.py`)
Sends automated email summaries when exports complete:

**Triggering:**
```env
TRIGGER_MAIL_ON_COMPLETION=true
SENDGRID_API_KEY=SG.xxx
MAIL_FROM=reports@yourdomain.com
MAIL_TO=you@yourdomain.com,team@yourdomain.com
MAIL_SUBJECT_PREFIX=Synapse CSV Export
```

**Email includes:**
- Status (SUCCESS/FAILED)
- Run ID and environment
- Duration
- Final totals (vertex/edge row counts)
- Full formatted report in both text and HTML

---

### 3. **Storage & S3 Sync System** (`common/storage.py`)
Tracks where data is written and optionally uploads to S3:

**Modes:**
- **Local only**: CSVs written to `batchjobs/data/` (default)
- **S3 sync**: Staged locally, then uploaded to S3 after each step
- **Direct S3**: Full S3 URI passed as `--output-root s3://bucket/prefix/`

**Configuration:**
```env
EXPORT_TO_S3=true
S3_OUTPUT_BUCKET=synapse-neptune-csv-export-prod
S3_OUTPUT_PREFIX=neptune-csv-export
EXPORT_STAGING_DIR=/tmp/synapse-export
EXPORT_DISCARD_LOCAL=false  # Keep local copy after S3 upload
EXPORT_S3_SYNC_EACH_STEP=true  # Upload after each job or only at end
```

**Features:**
- Automatic run ID-based S3 prefix organization
- Per-step incremental uploads or batch upload at end
- Manifest generation showing S3 URIs for Neptune bulk loader
- Optional local cleanup after S3 upload

---

### 4. **CSV Integrity System** (`common/csv_writer.py`)
Ensures CSV format is correct for Neptune bulk loader:

- **Type coercion**: Handles Neptune type suffixes (`:Double`, `:Long`, etc.)
- **Null safety**: Empty strings on numeric columns become "0" (Neptune parser requirement)
- **JSON encoding**: Dict/list values properly JSON-encoded
- **Boolean normalization**: True → "true", False → "false"
- **Header validation**: Per-column type detection for safe value handling

---

### 5. **Orchestration & Step Tracking** (`run_all_exports.py`)
Coordinates full pipeline with detailed step tracking:

- **Dependency management**: Validates which sources can run (SQL, Dynamo, OpenSearch)
- **Sequential/parallel modes**: `EXPORT_SOURCES_PARALLEL=true` for simultaneous exports
- **Error capture**: Records specific errors per step with full context
- **Environment setup**: Auto-creates Python venv, installs deps, loads `.env`
- **Selective execution**: Skip steps you've already completed with `--skip-sql`, etc.

---

## 🚀 Commands to Run Exports

### **Quick Start: Full Pipeline (Recommended)**

```bash
# From repo root
./batchjobs/csv_export/run_all.sh

# Or from csv_export directory
cd batchjobs/csv_export
./run_all.sh
```

**What it does:**
1. Creates Python venv
2. Installs dependencies
3. Loads `.env` configuration
4. Runs SQL export
5. Runs DynamoDB export
6. Runs OpenSearch export
7. Combines all outputs into `final/`
8. Writes `export-summary.json` + `export-summary.txt`
9. Sends email (if configured)

---

### **Individual Export Commands**

**First, activate venv:**
```bash
cd batchjobs/csv_export
source .venv/bin/activate  # or: . .venv/bin/activate
```

#### SQL Export Only
```bash
python sql_export_job.py --output-root ../data
```

**Requires in `.env`:**
- `SQL_HOST`, `SQL_PORT`, `SQL_DATABASE`
- `SQL_USERNAME`, `SQL_PASSWORD`

**Outputs:**
- `batchjobs/data/sql/vertices/` — agencies, members, addresses, ledgers, hotel_itineraries
- `batchjobs/data/sql/edges/` — edges-core.csv, edges-fraud.csv

---

#### DynamoDB Export Only
```bash
python dynamo_export_job.py --output-root ../data
```

**Requires in `.env`:**
- `AWS_REGION`
- `DYNAMO_CUSTOMER360_TABLE`, `DYNAMO_ROLE_PERMISSIONS_TABLE`
- `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` (if `IS_LOCAL=true`)

**Outputs:**
- `batchjobs/data/dynamo/vertices/` — agency_balances, sales_heads, email_preferences, roles, permissions, promos
- `batchjobs/data/dynamo/edges/` — edges-customer360.csv

---

#### Discover OpenSearch Indices
```bash
python opensearch_discover_indices.py
```

Run this **before** OpenSearch export to see what indices exist on your cluster and get suggested `.env` values.

---

#### OpenSearch Export Only
```bash
python opensearch_export_job.py --output-root ../data
```

**Requires in `.env`:**
- `OPENSEARCH_ENDPOINT` (hostname only, no `https://`)
- `OPENSEARCH_LOGIN_INDEX`, `OPENSEARCH_STEPUP_INDEX`, `OPENSEARCH_HOTEL_INDEX`
- Auth: `OPENSEARCH_AUTH_MODE=basic` with username/password **or** SigV4

**Outputs:**
- `batchjobs/data/opensearch/vertices/` — devices, ip_addresses, auth_events, stepup_events, stepup_rules, hotels
- `batchjobs/data/opensearch/edges/` — edges-fraud.csv, edges-auth.csv

---

#### Combine All Sources
```bash
python combine_exports_job.py \
  --input-roots ../data/sql ../data/dynamo ../data/opensearch \
  --output-root ../data/final
```

Or skip missing sources:
```bash
# Dynamo + OpenSearch only (SQL not available)
python combine_exports_job.py \
  --input-roots ../data/dynamo ../data/opensearch \
  --output-root ../data/final
```

**Outputs:**
- `batchjobs/data/final/vertices/` — merged CSV files from all sources
- `batchjobs/data/final/edges/` — merged edge files
- `load-manifest.txt` — file listing for Neptune bulk loader

---

### **Python Orchestrator (Full Pipeline)**

```bash
cd batchjobs/csv_export
python run_all_exports.py
```

From repo root:
```bash
python batchjobs/csv_export/run_all_exports.py
```

**Options:**
```bash
# Skip steps
python run_all_exports.py --skip-sql --skip-dynamo

# Only combine (all sources already exported)
python run_all_exports.py --skip-sql --skip-dynamo --skip-opensearch

# Use system Python instead of venv
python run_all_exports.py --no-venv --no-install-deps

# Force .env to override environment variables
python run_all_exports.py --env-override
```

---

## 📋 Typical Workflow

### **First Run (Complete Export)**
```bash
# 1. Setup
cd /Users/aarav/Desktop/Synapse
cp batchjobs/csv_export/.env.example batchjobs/csv_export/.env
# Edit .env with your connection details

# 2. Run full pipeline
./batchjobs/csv_export/run_all.sh

# 3. Check results
cat batchjobs/data/export-summary.txt
cat batchjobs/data/export-summary.json
```

---

### **Incremental Export (Some Sources Already Done)**
```bash
# Re-run only OpenSearch and combine
./batchjobs/csv_export/run_all.sh --skip-sql --skip-dynamo

# Or as Python
python batchjobs/csv_export/run_all_exports.py --skip-sql --skip-dynamo
```

---

### **Export to S3 (After Local Testing)**
```bash
# Add to .env
echo "EXPORT_TO_S3=true" >> batchjobs/csv_export/.env
echo "S3_OUTPUT_BUCKET=your-bucket-name" >> batchjobs/csv_export/.env
echo "S3_OUTPUT_PREFIX=neptune-export-$(date +%Y%m%d)" >> batchjobs/csv_export/.env

# Run
./batchjobs/csv_export/run_all.sh
```

---

## 🔍 Performance Tuning

Adjust these in `.env` based on your machine:

```env
# Laptop (8 cores)
EXPORT_WORKERS=8
DYNAMO_SCAN_SEGMENTS=8
OPENSEARCH_SCROLL_SLICES=4
EXPORT_SOURCES_PARALLEL=false

# Dev server (16+ cores)
EXPORT_WORKERS=16
DYNAMO_SCAN_SEGMENTS=16
OPENSEARCH_SCROLL_SLICES=8
EXPORT_SOURCES_PARALLEL=false

# Large batch host (parallel everything)
EXPORT_WORKERS=32
DYNAMO_SCAN_SEGMENTS=32
OPENSEARCH_SCROLL_SLICES=16
EXPORT_SOURCES_PARALLEL=true
OPENSEARCH_TIMEOUT=180
```

---

## 📊 Report Files Location

After each export run:

- **Summary (human-readable)**: `batchjobs/data/export-summary.txt`
- **Summary (JSON)**: `batchjobs/data/export-summary.json`
- **Source data**:
  - `batchjobs/data/sql/vertices/` + `edges/`
  - `batchjobs/data/dynamo/vertices/` + `edges/`
  - `batchjobs/data/opensearch/vertices/` + `edges/`
- **Combined output** (for Neptune):
  - `batchjobs/data/final/vertices/`
  - `batchjobs/data/final/edges/`
  - `batchjobs/data/final/load-manifest.txt`

---

## 🐛 Troubleshooting

| Issue | Fix |
|---|---|
| `No module named 'pyodbc'` | Run `./run_all.sh` to auto-create venv, or `pip install -r requirements.txt` |
| `Can't open lib 'ODBC Driver 18'` | Run `brew install msodbcsql18 unixodbc` |
| `index_not_found_exception` | Run `opensearch_discover_indices.py` to find correct index names |
| `certificate verify failed` | Set `OPENSEARCH_VERIFY_CERTS=false` for prep clusters |
| Export is slow | Increase `EXPORT_WORKERS`, `DYNAMO_SCAN_SEGMENTS`, `OPENSEARCH_SCROLL_SLICES` |
| Email not sending | Verify `TRIGGER_MAIL_ON_COMPLETION=true`, `SENDGRID_API_KEY`, `MAIL_TO`, `MAIL_FROM` |


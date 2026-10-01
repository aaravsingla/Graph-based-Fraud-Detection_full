# Synapse CSV Export System Architecture

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                      run_all_exports.py                             │
│                   (Main Orchestrator)                               │
└────────────┬─────────────────────────────────────────────────────┬──┘
             │                                                   │
         Parallel (optional)                              Sequential
             │                                                   │
    ┌────────┴────────┬────────────┬───────────┐                │
    │                 │            │           │                │
    v                 v            v           v                v
┌────────┐       ┌──────────┐  ┌──────────┐  ┌──────────┐   ┌────────────┐
│  SQL   │       │ DynamoDB │  │OpenSearch│  │ Combine  │   │ S3 SYNC    │
│ Export │       │ Export   │  │ Export   │  │ Exports  │   │ (if enabled)
└───┬────┘       └────┬─────┘  └────┬─────┘  └─────┬────┘   └──────┬─────┘
    │                 │             │              │               │
    │ Output:         │             │              │               │
    │ vertices/       │ Output:     │ Output:      │ Output:        │
    │ members.csv     │ agency_...  │ auth_...     │ All combined   │
    │ edges/          │ edges/      │ stepup_...   │ vertices + edges
    │ ...             │ ...         │ edges/...    │                │
    │                 │             │              │                │
    v                 v             v              v                │
┌─────────────────────────────────────────────────────┐              │
│      batchjobs/data/sql/                            │              │
│      batchjobs/data/dynamo/                         │              │
│      batchjobs/data/opensearch/                     │              │
│      (Local staging directories)                    │              │
└──────────────────┬──────────────────────────────────┘              │
                   │                                                 │
                   v                                                 │
        ┌──────────────────────┐                                    │
        │  Combine Step        │                                    │
        │  (run_all_exports.py)│                                    │
        └──────────┬───────────┘                                    │
                   │                                                 │
                   v                                                 │
┌────────────────────────────────────────────────────────┐          │
│      batchjobs/data/final/                             │          │
│      ├─ vertices/                                      │          │
│      │  ├─ sql-members.csv          (1,200 rows)      │          │
│      │  ├─ sql-addresses.csv        (650 rows)        │          │
│      │  ├─ sql-agencies.csv         (200 rows)        │          │
│      │  ├─ ... more CSVs from SQL                      │          │
│      │  ├─ dynamo-agency_balances.csv                 │          │
│      │  ├─ ... more from DynamoDB                     │          │
│      │  ├─ opensearch-auth_events.csv (50k rows)      │          │
│      │  ├─ ... more from OpenSearch                   │          │
│      ├─ edges/                                        │          │
│      │  ├─ sql-edges-core.csv       (450 rows)        │          │
│      │  ├─ dynamo-edges-customer360.csv               │          │
│      │  ├─ opensearch-edges-auth.csv                  │          │
│      │  ├─ opensearch-edges-fraud.csv                 │          │
│      │  ├─ sql-edges-fraud.csv                        │          │
│      ├─ load-manifest.txt           (Neptune loader)  │          │
│      ├─ export-summary.json                           │          │
│      └─ export-summary.txt                            │          │
└────────────────┬───────────────────────────────────────┘          │
                 │                                                   │
                 v                                                   │
        IF EXPORT_TO_S3=true:                                       │
        ┌──────────────────────┐                                    │
        │  S3 Upload          │<───────────────────────────────────┘
        │  (ExportStorage.    │
        │   sync_all())       │
        └──────────┬──────────┘
                   │
                   v
        ┌──────────────────────┐
        │  AWS S3              │
        │  s3://bucket/prefix/ │
        │  /run-id/            │
        │  ├─ final/vertices/* │
        │  ├─ final/edges/*    │
        │  └─ *.json reports   │
        └──────────────────────┘
```

---

## Component Details

### 1. Main Orchestrator: `run_all_exports.py`

**Responsibilities:**
- Load configuration from `.env`
- Validate all required credentials
- Instantiate `ExportStorage` (local + optional S3)
- Run SQL, DynamoDB, OpenSearch exports (parallel or sequential)
- Combine results
- Upload to S3 (if enabled)
- Generate reports
- Send email (if enabled)

**Entry point:**
```bash
python3 run_all_exports.py [--output-root PATH] [--skip-sql] [--skip-dynamo] [--skip-opensearch] [--skip-combine]
```

**Output:**
- Exit code 0 = Success
- Exit code 1 = Failure (check console for details)

---

### 2. Storage Layer: `common/storage.py`

**Key Classes:**
- `ExportStorage` — Manages local directory + optional S3 bucket

**Key Methods:**
- `ExportStorage.create()` — Factory method that reads `.env` and determines storage config
- `sync_tree(path)` — Upload a specific subdirectory to S3
- `sync_all()` — Upload entire local tree to S3
- `describe()` — Human-readable storage config (for logging)

**Key Functions:**
- `resolve_export_storage()` — Create and log storage config
- `_upload_directory()` — Recursively upload files to S3 using boto3
- `download_s3_prefix()` — Download from S3 (used for input staging)
- `write_manifest_s3_paths()` — Append S3 URIs to load-manifest.txt

**Credentials:**
- Read credentials: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` (from `.env`)
- Optional write-specific credentials: `S3_AWS_*` (if different from read)
- Defaults to AWS default credential chain if not in `.env`

**Configuration:**
| Env Var | Source | Usage |
|---------|--------|-------|
| `EXPORT_TO_S3` | `.env` | Boolean: enable S3 upload |
| `S3_OUTPUT_BUCKET` | `.env` | Bucket name |
| `S3_OUTPUT_PREFIX` | `.env` | Path prefix in bucket |
| `EXPORT_RUN_ID` | `.env` or auto-generated | Timestamp folder (e.g., `20260622T120000Z`) |
| `EXPORT_DISCARD_LOCAL` | `.env` | Boolean: delete local after S3 upload |
| `EXPORT_S3_SYNC_EACH_STEP` | `.env` (default: true) | Upload after each export step, or all at end |

---

### 3. Export Jobs (parallel workers)

Each job:
- Reads from external data source (SQL, DynamoDB, OpenSearch)
- Writes CSVs to `output_root/{sql,dynamo,opensearch}/{vertices,edges}/`
- **Does not** handle S3 directly (delegated to orchestrator)

**Jobs:**
1. `sql_export_job.py` — Query SQL Server, export to CSV
2. `dynamo_export_job.py` — Scan DynamoDB tables, export to CSV
3. `opensearch_export_job.py` — Query OpenSearch indices, export to CSV

**Concurrency:**
- Jobs run in parallel if `EXPORT_SOURCES_PARALLEL=true` (default)
- Configured via: `--export-sources-parallel` flag or env var

---

### 4. Combine Step: `combine_exports_job.py`

**Input:**
- Multiple source directories: `sql/`, `dynamo/`, `opensearch/`

**Processing:**
- De-duplicate vertices by ID across sources
- Combine edges from all sources
- Merge into single set of CSVs in `final/` directory

**Output:**
- `final/vertices/*.csv` — All unique vertices from all sources
- `final/edges/*.csv` — All edges combined
- `final/load-manifest.txt` — List of CSVs for Neptune bulk loader

---

### 5. Reporting: `common/report.py`

**Key Functions:**
- `build_export_report()` — Scan local tree, collect stats, build report object
- `format_report_text()` — Human-readable text report
- `write_report_files()` — Write JSON and text reports
- `scan_source_tree()` — Count rows in source CSVs
- `scan_final_tree()` — Count rows in combined CSVs

**Report Structure:**
```json
{
  "run_id": "20260622T120000Z",
  "status": "SUCCESS|FAILED",
  "started_at": "ISO timestamp",
  "finished_at": "ISO timestamp",
  "total_seconds": 330.5,
  "environment": "local|prod|staging",
  "output_root": "/path/to/batchjobs/data",
  "s3_destination": "s3://bucket/prefix/run-id/" or null,
  "steps": [
    {
      "name": "SQL export",
      "status": "SUCCESS|FAILED",
      "seconds": 45.3,
      "error": "exit code 1" or null
    },
    ...
  ],
  "sources": {
    "sql": { "vertices": [...], "edges": [...], "totals": {...} },
    "dynamo": { ... },
    "opensearch": { ... }
  },
  "final": { "vertices": [...], "edges": [...], "totals": {...} },
  "error": null or "Pipeline failed: reason"
}
```

---

## Data File Structure

### Local (Before S3 Upload)

```
batchjobs/data/
├─ sql/                          # SQL export
│  ├─ vertices/
│  │  ├─ members.csv             (1,200 rows)
│  │  ├─ addresses.csv           (650 rows)
│  │  ├─ agencies.csv            (200 rows)
│  │  ├─ hotel_itineraries.csv   (800 rows)
│  │  └─ ledgers.csv             (5,600 rows)
│  └─ edges/
│     ├─ edges-core.csv          (450 rows)
│     └─ edges-fraud.csv         (200 rows)
│
├─ dynamo/                       # DynamoDB export
│  ├─ vertices/
│  │  ├─ agency_balances.csv
│  │  ├─ email_preferences.csv
│  │  ├─ permissions.csv
│  │  ├─ promos.csv
│  │  ├─ roles.csv
│  │  ├─ sales_coordinators.csv
│  │  └─ sales_heads.csv
│  └─ edges/
│     └─ edges-customer360.csv
│
├─ opensearch/                   # OpenSearch export
│  ├─ vertices/
│  │  ├─ auth_events.csv         (50,000 rows)
│  │  ├─ devices.csv             (8,500 rows)
│  │  ├─ hotels.csv              (3,200 rows)
│  │  ├─ ip_addresses.csv        (15,000 rows)
│  │  ├─ stepup_events.csv       (100 rows, synthetic)
│  │  └─ stepup_rules.csv        (50 rows)
│  └─ edges/
│     ├─ edges-auth.csv
│     └─ edges-fraud.csv
│
└─ final/                        # Combined & deduplicated
   ├─ vertices/                  (14 CSVs, deduplicated)
   ├─ edges/                     (5 CSVs, combined)
   ├─ load-manifest.txt          (For Neptune bulk loader)
   ├─ export-summary.json
   └─ export-summary.txt
```

### In S3 (After Upload)

```
s3://dev-login-graph-data/
└─ neptune-csv-export/
   └─ [EXPORT_RUN_ID]/           # e.g., "20260622T120000Z"
      └─ final/
         ├─ vertices/
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
         │  ├─ opensearch-stepup_rules.csv
         │  ├─ sql-addresses.csv
         │  ├─ sql-agencies.csv
         │  ├─ sql-hotel_itineraries.csv
         │  ├─ sql-ledgers.csv
         │  └─ sql-members.csv
         ├─ edges/
         │  ├─ dynamo-edges-customer360.csv
         │  ├─ opensearch-edges-auth.csv
         │  ├─ opensearch-edges-fraud.csv
         │  ├─ sql-edges-core.csv
         │  └─ sql-edges-fraud.csv
         └─ load-manifest.txt
```

---

## Configuration Hierarchy

```
┌────────────────────────────────────────────────────────┐
│  Environment Variables (shell)                         │
│  (Highest priority)                                    │
└────────────────────────────────────────────────────────┘
                    ↑
                    │ (if not already set)
                    │
┌────────────────────────────────────────────────────────┐
│  .env file (batchjobs/csv_export/.env)                 │
│  (Loaded by run_all_exports.py with --env-file flag)  │
└────────────────────────────────────────────────────────┘
                    ↑
                    │ (if EXPORT_TO_S3 not explicit)
                    │
┌────────────────────────────────────────────────────────┐
│  Default values in code                                │
│  (e.g., EXPORT_RUN_ID = auto-generated timestamp)     │
└────────────────────────────────────────────────────────┘
```

---

## Error Handling & Recovery

### Export Step Fails
1. Exception raised by job script
2. Caught in `run_step()` 
3. Logged as `StepResult(status="FAILED", error="...")`
4. Pipeline aborts (doesn't proceed to combine or S3 upload)
5. Report written with error details

### S3 Upload Fails
1. Exception in `_upload_directory()`
2. Caught in orchestrator
3. Pipeline aborts, but local files remain (backup)
4. Report written with S3 error
5. **Manual retry possible** via re-running upload code

### Database Connection Fails
1. Individual job script fails
2. Caught in `run_step()`
3. Logged as failed step
4. Other jobs may still run (if parallel enabled)
5. Combine and S3 steps skipped

---

## Performance Characteristics

| Component | Time | Parallelizable |
|-----------|------|---|
| SQL export | ~45s | Job-level (vs other sources) |
| DynamoDB export | ~38s | Job-level + internal scan segments |
| OpenSearch export | ~120s | Job-level + internal scroll slices |
| Combine | ~15s | No (sequential dedup/merge) |
| S3 upload | ~10-20s | Yes (multipart by boto3) |
| **Total** | ~350s (5.8 min) | Up to 3x jobs in parallel |

**Optimization:**
- Set `EXPORT_S3_SYNC_EACH_STEP=false` to avoid intermediate S3 calls (saves ~30s)
- Set `EXPORT_SOURCES_PARALLEL=true` to run SQL/Dynamo/OpenSearch together (saves ~150s)
- Increase `EXPORT_WORKERS` for multi-segment scans (DynamoDB)
- Increase `OPENSEARCH_SCROLL_SLICES` for faster ES scanning (default 8)

---

## Monitoring & Observability

### Stdout/Stderr
- Real-time progress messages: `[storage]`, `[config]`, `[report]`, `[mail]`
- Step progress: `STEP:`, `Running:`, `Done:`, `Failed:`
- Upload counts: `Uploaded X object(s) to ...`

### Log Files
- `export.log` — Export logs (if configured in code)
- `export-summary.json` — Machine-readable report (always written)
- `export-summary.txt` — Human-readable report (always written)

### Metrics
- Rows per source (from CSV row counts)
- Files uploaded to S3
- Timing for each step
- Pipeline status (SUCCESS / FAILED)

### Email Notifications
- Triggered if `TRIGGER_MAIL_ON_COMPLETION=true`
- Sends report summary to `MAIL_TO` address
- Via SendGrid API (requires `SENDGRID_API_KEY`)

---

## Future Enhancements

Possible extensions to the system:

1. **Incremental exports** — Only export rows modified since last run
2. **Data validation** — Row count checks, schema validation
3. **Retry logic** — Automatic retry on transient failures
4. **Partitioning** — Split large CSVs for Neptune (max file size limits)
5. **Compression** — Gzip CSVs before S3 upload
6. **Encryption** — S3 server-side encryption, client-side encryption keys
7. **Webhooks** — POST to external system on completion
8. **Metrics** — Send CloudWatch metrics for monitoring
9. **DLQ** — Dead-letter queue for failed records (not just failed jobs)
10. **Versioning** — Multiple versions in S3, easy rollback

---

## Summary

Your system is:
- ✅ **Modular** — Each export job independent
- ✅ **Scalable** — Parallel sources, incremental S3 sync
- ✅ **Observable** — JSON reports, detailed logging
- ✅ **Resilient** — Local backup, retry-friendly
- ✅ **Production-ready** — Error handling, email notifications
- ✅ **Well-documented** — This guide + inline code comments

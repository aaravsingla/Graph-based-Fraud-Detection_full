# Code References — S3 Upload System

## File Locations

### Core Storage Module
- **Path**: `batchjobs/csv_export/common/storage.py`
- **Lines**: 1-260
- **Classes**: `ExportStorage`
- **Key Methods**:
  - `ExportStorage.create()` (lines 60-102) — Factory from env
  - `ExportStorage.sync_tree()` (lines 110-130) — Incremental upload
  - `ExportStorage.sync_all()` (lines 132-141) — Bulk upload
  - `_upload_directory()` (lines 231-246) — Recursive upload to S3
  - `_s3_write_client()` (lines 206-228) — Create boto3 S3 client

### Main Orchestrator
- **Path**: `batchjobs/csv_export/run_all_exports.py`
- **Lines**: 1-500+
- **Key Functions**:
  - `main()` (lines 240-340) — Entry point
  - `run_step()` (lines 195-211) — Execute single step
  - `resolve_export_storage()` (lines ~150-165) — Create storage from args
  - `_finalize_report()` (lines ~350-380) — Generate reports & email

### Report Module
- **Path**: `batchjobs/csv_export/common/report.py`
- **Lines**: 1-200+
- **Classes**: `ExportReport`, `StepResult`
- **Key Functions**:
  - `build_export_report()` (lines 115-142) — Create report object
  - `format_report_text()` (lines 145-190) — Human-readable format
  - `write_report_files()` (lines 193-202) — Write JSON + text
  - `scan_final_tree()` (lines 79-100) — Count rows in final CSVs

---

## Code Walkthrough: How S3 Upload Works

### Step 1: Configuration Loading (run_all_exports.py, ~line 280)

```python
# Load .env file
if load_dotenv(env_path, override=args.env_override):
    print(f"[config] Loaded environment from {env_path}")

# Create storage object (checks EXPORT_TO_S3, S3_OUTPUT_BUCKET, etc.)
storage = resolve_export_storage(
    args.output_root,
    base_dir=base,
    run_id=run_id,
)

# Log destination
if storage.use_s3:
    print("[config] S3 destination:", storage.destination_uri)
    # Output: "[config] S3 destination: s3://dev-login-graph-data/neptune-csv-export/20260622T120000Z/"
```

### Step 2: ExportStorage Initialization (storage.py, ~line 60)

```python
@classmethod
def create(
    cls,
    output_root: str | Path,
    *,
    base_dir: Path,
    run_id: str | None = None,
) -> ExportStorage:
    output_root_str = str(output_root).strip()
    keep_local = not _truthy(os.getenv("EXPORT_DISCARD_LOCAL"))

    # If output_root is S3 URI, parse it
    if output_root_str.startswith("s3://"):
        bucket, prefix = parse_s3_uri(output_root_str)
        local_root = Path(os.getenv("EXPORT_STAGING_DIR", ...))
        return cls(
            local_root=local_root.resolve(),
            s3_bucket=bucket,
            s3_prefix=prefix,
            keep_local=keep_local,
        )

    # Otherwise, check env vars
    if export_to_s3_enabled():  # EXPORT_TO_S3=true or S3_OUTPUT_BUCKET set
        bucket = os.environ["S3_OUTPUT_BUCKET"].strip()
        prefix = os.getenv("S3_OUTPUT_PREFIX", "neptune-csv-export").strip("/")
        if run_id:
            prefix = f"{prefix}/{run_id}"
        return cls(
            local_root=local_root,
            s3_bucket=bucket,
            s3_prefix=f"{prefix}/",
            keep_local=keep_local,
        )

    # No S3, local only
    return cls(local_root=local_root, keep_local=True)
```

### Step 3: Incremental Upload (storage.py, ~line 110)

During export, after each step completes:

```python
def sync_tree(self, relative_path: str | Path | None = None) -> int:
    # If S3 not enabled, skip
    if not self.use_s3:
        return 0
    
    # If sync disabled, skip
    if not _truthy(os.getenv("EXPORT_S3_SYNC_EACH_STEP", "true")):
        return 0

    # Upload this subdirectory
    source = self.local_root if relative_path is None else self.local_root / relative_path
    if not source.exists():
        return 0

    # Build S3 key prefix
    prefix = self.s3_prefix
    if relative_path is not None:
        rel = Path(relative_path).as_posix().strip("/")
        prefix = f"{prefix.rstrip('/')}/{rel}/" if rel else prefix

    # Recursive upload
    uploaded = _upload_directory(source, self.s3_bucket, prefix)
    print(f"[storage] Uploaded {uploaded} object(s) to {s3_uri(self.s3_bucket, prefix)}")

    return uploaded
```

### Step 4: Bulk Upload at End (storage.py, ~line 132)

In `run_all_exports.py` after combine step:

```python
if storage.use_s3:
    manifest = output_root / "final" / "load-manifest.txt"
    write_manifest_s3_paths(
        manifest,
        bucket=storage.s3_bucket,
        prefix=f"{storage.s3_prefix.rstrip('/')}/final",
    )
    storage.sync_all()  # Upload everything not yet uploaded
```

Where `sync_all()`:

```python
def sync_all(self) -> int:
    if not self.use_s3:
        return 0
    source = self.local_root
    if not source.exists():
        return 0
    uploaded = _upload_directory(source, self.s3_bucket, self.s3_prefix)
    print(f"[storage] Uploaded {uploaded} object(s) to {self.destination_uri}")
    if not self.keep_local:
        shutil.rmtree(self.local_root, ignore_errors=True)
    return uploaded
```

### Step 5: Recursive S3 Upload (storage.py, ~line 231)

```python
def _upload_directory(local_dir: Path, bucket: str, prefix: str) -> int:
    # Get boto3 S3 client with credentials from .env
    client = _s3_write_client()
    normalized_prefix = prefix.strip("/")
    uploaded = 0

    # Walk all files in local_dir
    for file_path in sorted(local_dir.rglob("*")):
        if not file_path.is_file():
            continue
        
        # Build S3 key
        rel = file_path.relative_to(local_dir).as_posix()
        key = f"{normalized_prefix}/{rel}" if normalized_prefix else rel
        
        # Upload
        client.upload_file(str(file_path), bucket, key)
        uploaded += 1
    
    return uploaded
```

### Step 6: Credentials (storage.py, ~line 206)

```python
def _s3_write_client():
    """
    Return a boto3 S3 client using S3_AWS_* credentials if set,
    otherwise fall back to the default credential chain.
    This allows read credentials (AWS_*) and write credentials (S3_AWS_*)
    to be configured independently.
    """
    import boto3

    # Try explicit write credentials first
    key = os.getenv("S3_AWS_ACCESS_KEY_ID", "").strip()
    secret = os.getenv("S3_AWS_SECRET_ACCESS_KEY", "").strip()
    token = os.getenv("S3_AWS_SESSION_TOKEN", "").strip()
    region = os.getenv("S3_AWS_REGION", os.getenv("AWS_REGION", "us-east-1")).strip()

    if key and secret:
        return boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            aws_session_token=token or None,
        )
    
    # Fall back to default chain (IAM role, ~/.aws/credentials, env vars, etc.)
    return boto3.client("s3", region_name=region)
```

### Step 7: Manifest Generation (storage.py, ~line 250)

After upload, append S3 URIs to load-manifest.txt:

```python
def write_manifest_s3_paths(manifest_path: Path, *, bucket: str, prefix: str) -> None:
    """Append S3 URIs below local paths in load-manifest.txt."""
    if not manifest_path.is_file():
        return
    lines = manifest_path.read_text(encoding="utf-8").splitlines()
    base = s3_uri(bucket, prefix).rstrip("/")
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write("\n# S3 destination (Neptune bulk loader prefix)\n")
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # For each local path, append S3 URI
            handle.write(f"{base}/{stripped}\n")
```

### Step 8: Report Generation (report.py, ~line 115)

```python
def build_export_report(
    *,
    run_id: str,
    output_root: Path,
    steps: list[StepResult],
    started_at: datetime,
    finished_at: datetime,
    s3_destination: str | None,  # <-- S3 path
    source_names: Iterable[str] = ("sql", "dynamo", "opensearch"),
    error: str | None = None,
) -> ExportReport:
    status = "FAILED" if error or any(s.status == "FAILED" for s in steps) else "SUCCESS"
    
    # Scan local tree for CSV files and count rows
    sources = {
        name: scan_source_tree(output_root, name)
        for name in source_names
        if (output_root / name).exists()
    }
    
    final_dir = output_root / "final"
    final = scan_final_tree(final_dir) if final_dir.exists() else {}

    return ExportReport(
        run_id=run_id,
        status=status,
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
        total_seconds=(finished_at - started_at).total_seconds(),
        environment=os.getenv("ENVIRONMENT", os.getenv("IS_LOCAL", "local")),
        output_root=str(output_root),
        s3_destination=s3_destination,  # <-- Included in report
        steps=steps,
        sources=sources,
        final=final,
        error=error,
    )
```

---

## Environment Variables Used

### Read from `.env` by `run_all_exports.py`

| Variable | Used in | Default | Purpose |
|----------|---------|---------|---------|
| `EXPORT_TO_S3` | `storage.py:export_to_s3_enabled()` | `false` | Enable S3 upload |
| `S3_OUTPUT_BUCKET` | `storage.py:create()` | _(required)_ | S3 bucket |
| `S3_OUTPUT_PREFIX` | `storage.py:create()` | `neptune-csv-export` | Key prefix |
| `EXPORT_RUN_ID` | `storage.py:default_run_id()` | Auto (timestamp) | Run identifier |
| `EXPORT_DISCARD_LOCAL` | `storage.py:create()` | `false` | Delete local after upload |
| `EXPORT_S3_SYNC_EACH_STEP` | `storage.py:sync_tree()` | `true` | Incremental upload |
| `S3_AWS_ACCESS_KEY_ID` | `storage.py:_s3_write_client()` | — | Write-only credentials |
| `S3_AWS_SECRET_ACCESS_KEY` | `storage.py:_s3_write_client()` | — | Write-only credentials |
| `S3_AWS_SESSION_TOKEN` | `storage.py:_s3_write_client()` | — | Write-only credentials |
| `S3_AWS_REGION` | `storage.py:_s3_write_client()` | `AWS_REGION` | S3 region |
| `AWS_REGION` | `storage.py:_s3_write_client()` | `us-east-1` | Default region |
| `AWS_ACCESS_KEY_ID` | `opensearch_export_job.py:_aws_session()` | — | Read credentials |
| `AWS_SECRET_ACCESS_KEY` | `opensearch_export_job.py:_aws_session()` | — | Read credentials |
| `AWS_SESSION_TOKEN` | `opensearch_export_job.py:_aws_session()` | — | Read credentials |
| `IS_LOCAL` | Various | `false` | Dev vs Prod mode |

---

## Call Flow: From Export to S3

```
run_all_exports.py:main()
├─ load_dotenv()
│  └─ Read .env file, set env vars
│
├─ resolve_export_storage()
│  └─ ExportStorage.create()
│     ├─ Check EXPORT_TO_S3
│     ├─ Parse S3_OUTPUT_BUCKET, S3_OUTPUT_PREFIX
│     └─ Create ExportStorage(s3_bucket=..., s3_prefix=...) or local-only
│
├─ run_step("SQL export", ...)
├─ run_step("DynamoDB export", ...)
├─ run_step("OpenSearch export", ...)
│
├─ run_step("Combine exports", ...)
│
├─ if storage.use_s3:
│  ├─ write_manifest_s3_paths()
│  │  └─ Append S3 URIs to load-manifest.txt
│  │
│  └─ storage.sync_all()
│     └─ _upload_directory(storage.local_root, storage.s3_bucket, storage.s3_prefix)
│        ├─ _s3_write_client()  # Get boto3 client with credentials
│        └─ For each file:
│           └─ client.upload_file(file_path, bucket, key)
│
├─ build_export_report(s3_destination=storage.destination_uri)
│  └─ scan_final_tree(), scan_source_tree() to count rows
│
├─ write_report_files(report, output_root)
│  └─ Write export-summary.json + export-summary.txt
│
└─ send_completion_email(report)  # If TRIGGER_MAIL_ON_COMPLETION
```

---

## Testing & Debugging

### Unit Test: Check If S3 Enabled
```python
from common.storage import export_to_s3_enabled
import os

os.environ['EXPORT_TO_S3'] = 'true'
assert export_to_s3_enabled()

os.environ['S3_OUTPUT_BUCKET'] = 'my-bucket'
os.environ.pop('EXPORT_TO_S3', None)
assert export_to_s3_enabled()
```

### Manual Test: Create Storage Object
```python
from common.storage import ExportStorage
from pathlib import Path
import os

os.environ['S3_OUTPUT_BUCKET'] = 'dev-login-graph-data'
os.environ['S3_OUTPUT_PREFIX'] = 'neptune-csv-export'
os.environ['EXPORT_RUN_ID'] = 'test-run-001'

storage = ExportStorage.create(
    '../data',
    base_dir=Path('.').resolve(),
)

print(storage.use_s3)              # True
print(storage.s3_bucket)           # 'dev-login-graph-data'
print(storage.destination_uri)     # 's3://dev-login-graph-data/neptune-csv-export/test-run-001/'
print(storage.describe())          # Full description
```

### Manual Test: Upload Directory
```python
from common.storage import _s3_write_client, _upload_directory
from pathlib import Path

client = _s3_write_client()

# Verify bucket is accessible
response = client.head_bucket(Bucket='dev-login-graph-data')
print(f"Bucket accessible: {response['ResponseMetadata']['HTTPStatusCode']} 200 OK")

# Try uploading a test file
test_file = Path('test.txt')
test_file.write_text('hello world')

uploaded = _upload_directory(test_file.parent, 'dev-login-graph-data', 'test-prefix')
print(f"Uploaded {uploaded} files")
```

---

## Error Handling Specifics

### Boto3 Exceptions (storage.py, _upload_directory)

```python
# No explicit try/except in _upload_directory
# Exceptions propagate to run_all_exports.py:main() finally block
# where they're caught and added to pipeline_error

try:
    storage.sync_all()
except Exception as exc:
    pipeline_error = str(exc)
    raise  # Re-raise, but first write report with error
finally:
    _finalize_report(
        ...
        error=pipeline_error,  # Included in report
    )
```

### Common boto3 exceptions:

```python
# No such bucket
botocore.exceptions.NoSuchBucket: The specified bucket does not exist
# → Check S3_OUTPUT_BUCKET

# Permission denied
botocore.exceptions.ClientError: An error occurred (AccessDenied) when calling the PutObject operation
# → Check AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY

# Credentials expired
botocore.exceptions.ClientError: An error occurred (InvalidToken) when calling the...
# → Get new credentials from AWS

# Network error
botocore.exceptions.EndpointConnectionError: Could not connect to the endpoint URL...
# → Check internet connection, region setting
```

---

## Performance Tuning

### In storage.py:

```python
# _upload_directory uses sequential upload (boto3 handles multipart)
# Optimization: Use s3_client.upload_file() which auto-enables multipart
# For 100+ MB files, boto3 automatically uses multipart upload

# Config (in botocore config):
# multipart_threshold = 8 MB
# multipart_chunksize = 8 MB
# max_concurrent_requests = 10
```

### In run_all_exports.py:

```python
# Parallel source exports (if EXPORT_SOURCES_PARALLEL=true)
if sources_parallel() and len(export_jobs) > 1:
    parallel_results = run_parallel(
        [(name, (lambda cmd=command: run_step(name, cmd))) for ...],
        max_workers=len(export_jobs),  # 3 parallel jobs (SQL, DynamoDB, OpenSearch)
    )
```

---

## Summary

- **Storage module** (`storage.py`) handles local-to-S3 sync
- **Orchestrator** (`run_all_exports.py`) calls `storage.sync_all()` at end
- **Credentials** from `.env` or IAM role
- **Reports** include S3 destination URI
- **Error handling** via try/finally with detailed logging
- **Incremental** or bulk upload configurable via env vars
- **Production-ready** with email notifications, detailed reporting

All the infrastructure is built. Just enable it in `.env` and run!

import boto3
import os
from pathlib import Path

# Load .env
env_path = Path(__file__).resolve().parent / ".env"
for line in env_path.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    os.environ.setdefault(k.strip(), v.strip())

# Use S3-specific write credentials
s3 = boto3.client(
    's3',
    region_name=os.environ.get('S3_AWS_REGION', 'eu-west-1'),
    aws_access_key_id=os.environ.get('S3_AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.environ.get('S3_AWS_SECRET_ACCESS_KEY'),
    aws_session_token=os.environ.get('S3_AWS_SESSION_TOKEN'),
)

resp = s3.list_objects_v2(
    Bucket='dev-login-graph-data',
    Prefix='neptune-csv-export/',
    Delimiter='/'
)
runs = sorted([p['Prefix'] for p in resp.get('CommonPrefixes', [])])
print(f"All runs in S3 ({len(runs)} total):")
for r in runs:
    print(f"  {r}")

# Check the 3 latest runs in detail
print("\nLatest 3 runs detail:")
for prefix in runs[-3:]:
    resp2 = s3.list_objects_v2(Bucket='dev-login-graph-data', Prefix=prefix)
    files = resp2.get('Contents', [])
    total_kb = sum(f['Size'] for f in files) // 1024
    print(f"\n  {prefix} — {len(files)} files, {total_kb} KB total")
    for f in sorted(files, key=lambda x: x['Key']):
        ts = f['LastModified'].strftime('%H:%M UTC')
        kb = f['Size'] // 1024
        name = '/'.join(f['Key'].split('/')[-2:])
        print(f"    [{ts}]  {name}  ({kb} KB)")

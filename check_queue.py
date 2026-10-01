#!/usr/bin/env python3
"""Check Neptune loader queue for active/queued jobs and cancel them."""
import os, json, requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

endpoint = os.environ['NEPTUNE_ENDPOINT']
region = os.environ.get('AWS_REGION', 'eu-west-1')
creds = Credentials(
    os.environ['AWS_ACCESS_KEY_ID'],
    os.environ['AWS_SECRET_ACCESS_KEY'],
    os.environ.get('AWS_SESSION_TOKEN')
)

def signed_request(method, url, body=None):
    payload = json.dumps(body).encode() if body else b''
    headers = {'Content-Type': 'application/json'} if body else {}
    req = AWSRequest(method=method, url=url, data=payload, headers=headers)
    SigV4Auth(creds, 'neptune-db', region).add_auth(req)
    resp = requests.request(method, url, data=payload, headers=dict(req.headers), timeout=30)
    return resp.json()

# Get all load IDs
list_url = f'{endpoint}/loader?limit=100&includeQueuedLoads=true'
result = signed_request('GET', list_url)
all_ids = result.get('payload', {}).get('loadIds', [])
print(f'Total load jobs in history: {len(all_ids)}')

# Check status of every job
statuses = {}
active = []
for lid in all_ids:
    url = f'{endpoint}/loader/{lid}?details=true'
    r = signed_request('GET', url)
    status = r.get('payload', {}).get('overallStatus', {}).get('status', 'UNKNOWN')
    statuses[status] = statuses.get(status, 0) + 1
    if status in ('IN_QUEUE', 'IN_PROGRESS', 'LOAD_IN_PROGRESS', 'LOAD_QUEUED',
                  'LOAD_NOT_STARTED', 'LOAD_COMMITTED_W_WRITE_CONFLICTS'):
        active.append((lid, status))

print('\n=== STATUS BREAKDOWN (all jobs) ===')
for s, count in sorted(statuses.items()):
    print(f'  {s}: {count}')

print()
if active:
    print(f'ACTIVE/QUEUED JOBS ({len(active)}) — cancelling now...')
    for lid, s in active:
        print(f'  Cancelling {lid}  [{s}]...', end=' ')
        cancel_url = f'{endpoint}/loader/{lid}'
        cr = signed_request('DELETE', cancel_url)
        print(cr.get('status', cr))
    print('\nDone. Queue cleared.')
else:
    print('No active or queued jobs. Queue is already clean — safe to start new loads.')

#!/usr/bin/env python3
"""Analyze device & login data distribution from exported OpenSearch CSVs."""
import csv
from collections import Counter, defaultdict
from pathlib import Path

OS_DIR = Path("batchjobs/data/opensearch")
SQL_DIR = Path("batchjobs/data/sql")

def read_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))

print("=" * 70)
print("DEVICE & LOGIN DATA ANALYSIS")
print("=" * 70)

# --- Auth events ---
auth = read_csv(OS_DIR / "vertices/auth_events.csv")
print(f"\n[AUTH EVENTS] total rows: {len(auth)}")
print("  eventType distribution:")
for et, c in Counter(a["eventType:String"] for a in auth).most_common():
    print(f"    {et or '(empty)'}: {c}")
print("  isSuccessfulLogin distribution:")
for v, c in Counter(a["isSuccessfulLogin:Bool"] for a in auth).most_common():
    print(f"    {v or '(empty)'}: {c}")

# memberId coverage
with_member = sum(1 for a in auth if a["memberId:String"].strip())
print(f"  auth events WITH memberId: {with_member} ({100*with_member//len(auth)}%)")
print(f"  auth events WITHOUT memberId: {len(auth)-with_member}")

# agency coverage
with_agency = sum(1 for a in auth if a["agencyId:String"].strip())
print(f"  auth events WITH agencyId: {with_agency}")
uniq_agencies_in_auth = set(a["agencyId:String"] for a in auth if a["agencyId:String"].strip())
print(f"  UNIQUE agencies appearing in auth events: {len(uniq_agencies_in_auth)}")

# device coverage in auth
with_device = sum(1 for a in auth if a["deviceId:String"].strip())
print(f"  auth events WITH deviceId: {with_device}")

# --- Devices ---
devices = read_csv(OS_DIR / "vertices/devices.csv")
print(f"\n[DEVICES] total unique devices: {len(devices)}")

# --- IPs ---
ips = read_csv(OS_DIR / "vertices/ip_addresses.csv")
print(f"[IP ADDRESSES] total unique IPs: {len(ips)}")

# --- LOGGED_IN / ACCESSED_FROM edges (edges-fraud) ---
fraud_edges = read_csv(OS_DIR / "edges/edges-fraud.csv")
edge_types = Counter(e.get("~label", "") for e in fraud_edges)
print(f"\n[FRAUD EDGES] total: {len(fraud_edges)}")
for et, c in edge_types.most_common():
    print(f"    {et}: {c}")

# logins per device
logged_in = [e for e in fraud_edges if e.get("~label") == "LOGGED_IN"]
dev_login_counts = Counter(e["~to"] for e in logged_in)
member_login_counts = Counter(e["~from"] for e in logged_in)
print(f"\n[LOGINS PER DEVICE]")
print(f"  total LOGGED_IN edges: {len(logged_in)}")
print(f"  distinct devices that have logins: {len(dev_login_counts)}")
if dev_login_counts:
    vals = sorted(dev_login_counts.values(), reverse=True)
    print(f"  max logins on one device: {vals[0]}")
    print(f"  avg logins per device (that has logins): {sum(vals)/len(vals):.2f}")
    print(f"  devices with >1 login (shared): {sum(1 for v in vals if v>1)}")
    print(f"  devices with exactly 1 login: {sum(1 for v in vals if v==1)}")
    print(f"  top 10 most-used devices: {vals[:10]}")

print(f"\n[MEMBERS WHO LOGGED IN]")
print(f"  distinct members with a login: {len(member_login_counts)}")

# device sharing across members (the fraud signal)
dev_members = defaultdict(set)
for e in logged_in:
    dev_members[e["~to"]].add(e["~from"])
shared_devices = {d: m for d, m in dev_members.items() if len(m) > 1}
print(f"\n[DEVICE SHARING — the core fraud signal]")
print(f"  devices used by >1 member: {len(shared_devices)}")
if shared_devices:
    top = sorted(shared_devices.items(), key=lambda x: -len(x[1]))[:10]
    for d, m in top:
        print(f"    {d}: {len(m)} distinct members")
else:
    print("    *** NO devices shared across members — zero fraud signal ***")

# --- Agency coverage: how many agencies have ANY login/device activity ---
print(f"\n[AGENCY COVERAGE]")
agencies = read_csv(SQL_DIR / "vertices/agencies-part-001.csv") + \
           read_csv(SQL_DIR / "vertices/agencies-part-002.csv")
total_agencies = len(agencies)
print(f"  total agencies in graph: {total_agencies}")
print(f"  agencies with auth/login activity: {len(uniq_agencies_in_auth)}")
pct = 100 * len(uniq_agencies_in_auth) / total_agencies if total_agencies else 0
print(f"  => {pct:.1f}% of agencies have any login data")
print(f"  => {total_agencies - len(uniq_agencies_in_auth)} agencies have ZERO login/device data")

# --- Members coverage ---
members = read_csv(SQL_DIR / "vertices/members-part-001.csv") + \
          read_csv(SQL_DIR / "vertices/members-part-002.csv")
total_members = len(members)
print(f"\n[MEMBER COVERAGE]")
print(f"  total members in graph: {total_members}")
print(f"  members with a login: {len(member_login_counts)}")
pct_m = 100 * len(member_login_counts) / total_members if total_members else 0
print(f"  => {pct_m:.1f}% of members have any login")

print("\n" + "=" * 70)

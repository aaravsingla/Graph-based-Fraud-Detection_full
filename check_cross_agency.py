#!/usr/bin/env python3
"""Check if shared devices span multiple agencies (the actual fraud signal)."""
import csv
from collections import defaultdict
from pathlib import Path

OS_DIR = Path("batchjobs/data/opensearch")

def read_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))

fraud_edges = read_csv(OS_DIR / "edges/edges-fraud.csv")
logged_in = [e for e in fraud_edges if e.get("~label") == "LOGGED_IN"]

# LOGGED_IN edge carries agencyId as a property — find its column
if logged_in:
    print("LOGGED_IN columns:", list(logged_in[0].keys()))

# Map device -> set of (member, agency)
dev_members = defaultdict(set)
dev_agencies = defaultdict(set)
for e in logged_in:
    dev = e["~to"]
    member = e["~from"]
    agency = e.get("agencyId:Long") or e.get("agencyId:String") or e.get("agencyId", "")
    dev_members[dev].add(member)
    if agency.strip():
        dev_agencies[dev].add(agency)

cross_agency = {d: a for d, a in dev_agencies.items() if len(a) > 1}
print(f"\nDevices used across >1 AGENCY (cross-agency fraud signal): {len(cross_agency)}")
for d, a in sorted(cross_agency.items(), key=lambda x: -len(x[1]))[:15]:
    print(f"  {d}: {len(a)} agencies -> {sorted(a)}")

# IP cross-agency
accessed = [e for e in fraud_edges if e.get("~label") == "ACCESSED_FROM"]
ip_agencies = defaultdict(set)
for e in accessed:
    ip = e["~to"]
    agency = e.get("agencyId:Long") or e.get("agencyId:String") or e.get("agencyId", "")
    if agency.strip():
        ip_agencies[ip].add(agency)
ip_cross = {i: a for i, a in ip_agencies.items() if len(a) > 1}
print(f"\nIPs used across >1 AGENCY: {len(ip_cross)}")
for i, a in sorted(ip_cross.items(), key=lambda x: -len(x[1]))[:15]:
    print(f"  {i}: {len(a)} agencies -> {sorted(a)}")

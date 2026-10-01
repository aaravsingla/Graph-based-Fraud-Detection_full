#!/usr/bin/env python3
"""
Append synthetic OpenSearch-compatible CSV rows for MVP graph loading.

Hybrid approach:
  - Never modifies or removes existing real rows.
  - Appends synthetic rows with SYN- prefixed IDs.
  - Primarily fills empty step-up data; optionally adds login/auth rows for
    SQL members who have no auth events yet.

Usage:
  cd batchjobs/csv_export
  python generate_synthetic_opensearch.py
  python generate_synthetic_opensearch.py --stepup-count 150 --fill-login-gaps 50
  python generate_synthetic_opensearch.py --dry-run

Then re-combine:
  python combine_exports_job.py \\
    --input-roots ../data/sql ../data/dynamo ../data/opensearch \\
    --output-root ../data/final
"""

from __future__ import annotations

import argparse
import csv
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DATA_ROOT = Path(__file__).resolve().parent.parent / "data"
OPENSEARCH_DIR = DATA_ROOT / "opensearch"
VERTICES = OPENSEARCH_DIR / "vertices"
EDGES = OPENSEARCH_DIR / "edges"

SQL_MEMBERS = DATA_ROOT / "sql" / "vertices" / "members.csv"

STEPUP_RULES = [
    "HighTransactionValue",
    "MultipleBookingsLastHour",
    "FailedLoginThreshold",
    "NewDeviceBooking",
    "HighRiskDestination",
]

STEPUP_VERTEX_HEADERS = [
    "~id",
    "~label",
    "TrackingId:String",
    "eventType:String",
    "MemberId:Long",
    "AgencyId:Long",
    "AgencyCountryCode:String",
    "ipAddress:String",
    "ipCountryCode:String",
    "deviceId:String",
    "cognitoDeviceId:String",
    "username:String",
    "isPrimaryAgent:Bool",
    "isStepUpInitiated:Bool",
    "result:String",
    "reason:String",
    "TransactionValue:Double",
    "TransactionCurrency:String",
    "paymentId:String",
    "BookingCreatedOn:Datetime",
    "BookingCheckInOn:Datetime",
    "BookingAmountLastHours:Double",
    "TotalBookingsLastHours:Int",
    "TotalBookingsLastDaysExcludingHours:Int",
    "FailedLoginCount:Int",
    "StepUpHistoryExists:Bool",
    "LastStepUpCompletedTime:Datetime",
    "HotelStarRatings:String",
    "IsRefundableBooking:Bool",
    "DestinationCountryCode:String",
    "UserCreatedOn:Date",
    "timestamp:Datetime",
    "processedAt:Datetime",
]

STEPUP_RULE_HEADERS = [
    "~id",
    "~label",
    "ruleName:String",
    "description:String",
    "threshold:Int",
    "windowSeconds:Int",
]

AUTH_EVENT_HEADERS = [
    "~id",
    "~label",
    "authSessionId:String",
    "eventType:String",
    "challengeName:String",
    "email:String",
    "memberId:Long",
    "agencyId:Long",
    "ipAddress:String",
    "ipCountryCode:String",
    "deviceId:String",
    "tbohDeviceId:String",
    "isSuccessfulLogin:Bool",
    "initAuthTime:Long",
    "respondToAuthTime:Long",
    "medium:String",
    "ipThreatIntel:String",
    "SuspiciousMail:Bool",
    "UserCreatedOn:Date",
    "timestamp:Datetime",
    "processedAt:Datetime",
]

DEVICE_HEADERS = [
    "~id",
    "~label",
    "deviceId:String",
    "tbohDeviceId:String",
    "deviceFingerprint:String",
    "deviceType:String",
    "os:String",
    "browser:String",
    "firstSeen:Date",
    "lastSeen:Date",
    "useCount:Long",
]

IP_HEADERS = [
    "~id",
    "~label",
    "ipAddress:String",
    "ipCountryCode:String",
    "isp:String",
    "ipThreatIntel:String",
    "firstSeen:Date",
    "lastSeen:Date",
    "loginCount:Long",
]

FRAUD_EDGE_HEADERS = [
    "~id",
    "~from",
    "~to",
    "~label",
    "authSessionId:String",
    "ipAddress:String",
    "ipCountryCode:String",
    "ipThreatIntel:String",
    "timestamp:Datetime",
    "isSuccessfulLogin:Bool",
    "challengeName:String",
    "medium:String",
    "agencyId:Long",
    "deviceId:String",
    "tbohDeviceId:String",
    "bookedAt:Datetime",
]

AUTH_EDGE_HEADERS = [
    "~id",
    "~from",
    "~to",
    "~label",
    "timestamp:Datetime",
    "isSuccessfulLogin:Bool",
    "challengeName:String",
    "medium:String",
    "agencyId:Long",
    "ipAddress:String",
    "deviceId:String",
    "isStepUpInitiated:Bool",
    "TransactionValue:Double",
    "TrackingId:String",
    "reason:String",
]


def member_id(value: int | str) -> str:
    return f"member:{value}"


def auth_event_id(auth_session_id: str) -> str:
    return f"authevent:{auth_session_id}"


def stepup_event_id(tracking_id: str) -> str:
    return f"stepupevent:{tracking_id}"


def stepup_rule_id(rule_name: str) -> str:
    return f"stepuprule:{rule_name}"


def read_csv_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    if not path.exists():
        return [], []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        rows = list(reader)
    if not rows:
        return [], []
    return rows[0], rows[1:]


def append_rows(path: Path, header: list[str], new_rows: list[list[Any]], *, id_column: int = 0) -> int:
    existing_header, existing_rows = read_csv_rows(path)
    if existing_header and existing_header != header:
        raise ValueError(f"Header mismatch for {path}")

    seen_ids = {row[id_column] for row in existing_rows if len(row) > id_column}
    appended: list[list[Any]] = []
    for row in new_rows:
        row_id = str(row[id_column])
        if row_id in seen_ids:
            continue
        seen_ids.add(row_id)
        appended.append(row)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in existing_rows:
            writer.writerow(row)
        for row in appended:
            writer.writerow(["" if value is None else value for value in row])
    return len(appended)


def load_auth_seeds() -> list[dict[str, str]]:
    header, rows = read_csv_rows(VERTICES / "auth_events.csv")
    if not header:
        return []
    indexes = {name: idx for idx, name in enumerate(header)}
    seeds: list[dict[str, str]] = []
    for row in rows:
        if len(row) < len(header):
            continue
        member = row[indexes["memberId:Long"]]
        if not member:
            continue
        seeds.append(
            {
                "member_id": member,
                "agency_id": row[indexes["agencyId:Long"]],
                "email": row[indexes.get("email:String", header.index("email:String"))],
                "ip": row[indexes["ipAddress:String"]],
                "ip_country": row[indexes["ipCountryCode:String"]],
                "device_id": row[indexes["deviceId:String"]],
                "timestamp": row[indexes["timestamp:Datetime"]],
            }
        )
    return seeds


def load_members_without_auth(limit: int) -> list[dict[str, str]]:
    auth_header, auth_rows = read_csv_rows(VERTICES / "auth_events.csv")
    members_with_auth: set[str] = set()
    if auth_header:
        mid_idx = auth_header.index("memberId:Long")
        members_with_auth = {row[mid_idx] for row in auth_rows if len(row) > mid_idx and row[mid_idx]}

    sql_header, sql_rows = read_csv_rows(SQL_MEMBERS)
    if not sql_header:
        return []

    member_idx = sql_header.index("memberId:Long")
    email_idx = sql_header.index("email:String")
    agency_idx = sql_header.index("agencyId:Long")
    created_idx = sql_header.index("createdAt:Date")

    candidates: list[dict[str, str]] = []
    for row in sql_rows:
        if len(row) <= max(member_idx, email_idx, agency_idx, created_idx):
            continue
        member = row[member_idx]
        if member in members_with_auth:
            continue
        candidates.append(
            {
                "member_id": member,
                "agency_id": row[agency_idx],
                "email": row[email_idx],
                "created_at": row[created_idx],
            }
        )
    random.shuffle(candidates)
    return candidates[:limit]


def iso_now(days_ago: int = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def next_edge_id(prefix: str, existing_rows: list[list[str]], start: int) -> int:
    max_seen = start
    for row in existing_rows:
        if not row:
            continue
        edge_id = row[0]
        if edge_id.startswith(prefix):
            try:
                max_seen = max(max_seen, int(edge_id.split("-")[-1]))
            except ValueError:
                continue
    return max_seen + 1


def generate_stepup_bundle(
    count: int,
    *,
    dry_run: bool,
) -> dict[str, int]:
    seeds = load_auth_seeds()
    if not seeds:
        raise SystemExit(
            "No auth_events seeds found. Export login-prep first or use --fill-login-gaps."
        )

    _, existing_stepup = read_csv_rows(VERTICES / "stepup_events.csv")
    _, existing_rules = read_csv_rows(VERTICES / "stepup_rules.csv")
    _, existing_auth_edges = read_csv_rows(EDGES / "edges-auth.csv")

    auth_edge_seq = next_edge_id("OPS-A-", existing_auth_edges, 1_000_000)

    stepup_events: list[list[Any]] = []
    stepup_rules: dict[str, list[Any]] = {}
    auth_edges: list[list[Any]] = []

    for _ in range(count):
        seed = random.choice(seeds)
        tracking_id = f"SYN-{uuid.uuid4()}"
        reason = random.choice(STEPUP_RULES)
        ts = seed["timestamp"] or iso_now(random.randint(1, 30))
        device_id = seed["device_id"] or str(uuid.uuid4())
        ip = seed["ip"] or f"203.0.113.{random.randint(1, 254)}"
        ip_country = seed["ip_country"] or random.choice(["IN", "AE", "US", "GB"])
        member = seed["member_id"]
        agency = seed["agency_id"]
        txn_value = round(random.uniform(500, 5000), 2)

        stepup_events.append(
            [
                stepup_event_id(tracking_id),
                "StepUpEvent",
                tracking_id,
                "STEP_UP",
                member,
                agency,
                ip_country,
                ip,
                ip_country,
                device_id,
                device_id,
                seed["email"],
                random.choice(["True", "False"]),
                "True",
                random.choice(["ALLOW", "CHALLENGE", "DENY"]),
                reason,
                txn_value,
                "USD",
                f"SYN-PAY-{uuid.uuid4().hex[:12]}",
                ts,
                iso_now(0),
                txn_value,
                random.randint(1, 5),
                random.randint(1, 10),
                random.randint(0, 3),
                random.choice(["True", "False"]),
                ts,
                random.choice(["3", "4", "5"]),
                random.choice(["True", "False"]),
                ip_country,
                iso_now(90)[:10],
                ts,
                ts,
            ]
        )

        if reason not in stepup_rules:
            stepup_rules[reason] = [
                stepup_rule_id(reason),
                "StepUpRule",
                reason,
                f"Synthetic MVP rule: {reason}",
                random.randint(1, 5),
                random.choice([3600, 7200, 86400]),
            ]

        mid_vid = member_id(member)
        auth_edges.extend(
            [
                [
                    f"OPS-A-{auth_edge_seq:08d}",
                    mid_vid,
                    stepup_event_id(tracking_id),
                    "HAD_STEP_UP",
                    ts,
                    "",
                    "",
                    "",
                    agency,
                    "",
                    "",
                    "True",
                    txn_value,
                    tracking_id,
                    "",
                ],
                [
                    f"OPS-A-{auth_edge_seq + 1:08d}",
                    stepup_event_id(tracking_id),
                    device_id,
                    "STEP_UP_USED_DEVICE",
                    ts,
                    "",
                    "",
                    "",
                    agency,
                    ip,
                    device_id,
                    "",
                    "",
                    "",
                    "",
                ],
                [
                    f"OPS-A-{auth_edge_seq + 2:08d}",
                    stepup_event_id(tracking_id),
                    ip,
                    "STEP_UP_FROM_IP",
                    ts,
                    "",
                    "",
                    "",
                    agency,
                    ip,
                    device_id,
                    "",
                    "",
                    "",
                    "",
                ],
                [
                    f"OPS-A-{auth_edge_seq + 3:08d}",
                    stepup_event_id(tracking_id),
                    stepup_rule_id(reason),
                    "TRIGGERED_RULE",
                    ts,
                    "",
                    "",
                    "",
                    agency,
                    "",
                    "",
                    "",
                    "",
                    "",
                    reason,
                ],
            ]
        )
        auth_edge_seq += 4

    if dry_run:
        return {
            "stepup_events": len(stepup_events),
            "stepup_rules": len(stepup_rules),
            "auth_edges": len(auth_edges),
        }

    added = {
        "stepup_events": append_rows(VERTICES / "stepup_events.csv", STEPUP_VERTEX_HEADERS, stepup_events),
        "stepup_rules": append_rows(VERTICES / "stepup_rules.csv", STEPUP_RULE_HEADERS, list(stepup_rules.values())),
        "auth_edges": append_rows(EDGES / "edges-auth.csv", AUTH_EDGE_HEADERS, auth_edges),
    }
    return added


def generate_login_gap_bundle(
    count: int,
    *,
    dry_run: bool,
) -> dict[str, int]:
    members = load_members_without_auth(count)
    if not members:
        print("[login-gap] No SQL members without auth events — skipping.")
        return {"auth_events": 0, "devices": 0, "ip_addresses": 0, "fraud_edges": 0, "auth_edges": 0}

    _, existing_fraud_edges = read_csv_rows(EDGES / "edges-fraud.csv")
    _, existing_auth_edges = read_csv_rows(EDGES / "edges-auth.csv")
    fraud_seq = next_edge_id("OPS-F-", existing_fraud_edges, 1_000_000)
    auth_seq = next_edge_id("OPS-A-", existing_auth_edges, 2_000_000)

    auth_events: list[list[Any]] = []
    devices: list[list[Any]] = []
    ips: list[list[Any]] = []
    fraud_edges: list[list[Any]] = []
    auth_edges: list[list[Any]] = []

    for member in members:
        auth_session = f"SYN-{uuid.uuid4()}"
        device_id = str(uuid.uuid4())
        ip = f"203.0.113.{random.randint(1, 254)}"
        ts = iso_now(random.randint(1, 60))
        agency = member["agency_id"]
        mid = member["member_id"]
        mid_vid = member_id(mid)

        auth_events.append(
            [
                auth_event_id(auth_session),
                "AuthEvent",
                auth_session,
                "LOGIN",
                "PASSWORD",
                member["email"],
                mid,
                agency,
                ip,
                "IN",
                device_id,
                device_id,
                "True",
                "",
                "",
                "web",
                "",
                "",
                member["created_at"],
                ts,
                ts,
            ]
        )
        devices.append(
            [
                device_id,
                "Device",
                device_id,
                device_id,
                uuid.uuid4().hex,
                "desktop",
                "macOS",
                "Chrome",
                ts,
                ts,
                1,
            ]
        )
        ips.append(
            [
                ip,
                "IpAddress",
                ip,
                "IN",
                "Synthetic ISP",
                "",
                ts,
                ts,
                1,
            ]
        )
        fraud_edges.extend(
            [
                [
                    f"OPS-F-{fraud_seq:08d}",
                    mid_vid,
                    device_id,
                    "LOGGED_IN",
                    auth_session,
                    ip,
                    "IN",
                    "",
                    ts,
                    "True",
                    "PASSWORD",
                    "web",
                    agency,
                    device_id,
                    device_id,
                    "",
                ],
                [
                    f"OPS-F-{fraud_seq + 1:08d}",
                    mid_vid,
                    ip,
                    "ACCESSED_FROM",
                    auth_session,
                    ip,
                    "IN",
                    "",
                    ts,
                    "True",
                    "",
                    "",
                    agency,
                    device_id,
                    device_id,
                    "",
                ],
            ]
        )
        auth_edges.extend(
            [
                [
                    f"OPS-A-{auth_seq:08d}",
                    mid_vid,
                    auth_event_id(auth_session),
                    "HAD_AUTH_EVENT",
                    ts,
                    "True",
                    "PASSWORD",
                    "web",
                    agency,
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                ],
                [
                    f"OPS-A-{auth_seq + 1:08d}",
                    auth_event_id(auth_session),
                    device_id,
                    "AUTH_USED_DEVICE",
                    ts,
                    "",
                    "",
                    "",
                    agency,
                    ip,
                    device_id,
                    "",
                    "",
                    "",
                    "",
                ],
                [
                    f"OPS-A-{auth_seq + 2:08d}",
                    auth_event_id(auth_session),
                    ip,
                    "AUTH_FROM_IP",
                    ts,
                    "",
                    "",
                    "",
                    agency,
                    ip,
                    device_id,
                    "",
                    "",
                    "",
                    "",
                ],
            ]
        )
        fraud_seq += 2
        auth_seq += 3

    if dry_run:
        return {
            "auth_events": len(auth_events),
            "devices": len(devices),
            "ip_addresses": len(ips),
            "fraud_edges": len(fraud_edges),
            "auth_edges": len(auth_edges),
        }

    return {
        "auth_events": append_rows(VERTICES / "auth_events.csv", AUTH_EVENT_HEADERS, auth_events),
        "devices": append_rows(VERTICES / "devices.csv", DEVICE_HEADERS, devices),
        "ip_addresses": append_rows(VERTICES / "ip_addresses.csv", IP_HEADERS, ips),
        "fraud_edges": append_rows(EDGES / "edges-fraud.csv", FRAUD_EDGE_HEADERS, fraud_edges),
        "auth_edges": append_rows(EDGES / "edges-auth.csv", AUTH_EDGE_HEADERS, auth_edges),
    }


def print_inventory() -> None:
    print("\n=== Current OpenSearch export inventory ===")
    files = [
        VERTICES / "hotels.csv",
        VERTICES / "auth_events.csv",
        VERTICES / "devices.csv",
        VERTICES / "ip_addresses.csv",
        VERTICES / "stepup_events.csv",
        VERTICES / "stepup_rules.csv",
        EDGES / "edges-auth.csv",
        EDGES / "edges-fraud.csv",
    ]
    for path in files:
        _, rows = read_csv_rows(path)
        status = "EMPTY" if not rows else f"{len(rows):,} rows"
        print(f"  {path.relative_to(DATA_ROOT)}: {status}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Append synthetic OpenSearch CSV rows for MVP.")
    parser.add_argument("--stepup-count", type=int, default=100, help="Synthetic step-up events to add.")
    parser.add_argument(
        "--fill-login-gaps",
        type=int,
        default=0,
        help="Add synthetic login/auth rows for this many SQL members without auth events.",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible synthetic data.")
    parser.add_argument("--dry-run", action="store_true", help="Print counts only; do not write files.")
    args = parser.parse_args()

    random.seed(args.seed)
    print_inventory()

    stepup_added = generate_stepup_bundle(args.stepup_count, dry_run=args.dry_run)
    print(f"\n[stepup] {'Would add' if args.dry_run else 'Added'}: {stepup_added}")

    if args.fill_login_gaps > 0:
        login_added = generate_login_gap_bundle(args.fill_login_gaps, dry_run=args.dry_run)
        print(f"[login-gap] {'Would add' if args.dry_run else 'Added'}: {login_added}")

    if args.dry_run:
        print("\nDry run complete. Re-run without --dry-run to write files.")
    else:
        print("\nNext: re-combine into final/")
        print(
            "  python combine_exports_job.py "
            "--input-roots ../data/sql ../data/dynamo ../data/opensearch "
            "--output-root ../data/final"
        )


if __name__ == "__main__":
    main()

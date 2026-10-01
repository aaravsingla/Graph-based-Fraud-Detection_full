from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from botocore.config import Config

from common.concurrency import (
    boto3_client_settings,
    dynamo_scan_segments,
    map_parallel,
    run_parallel,
    worker_count,
)
from common.storage import default_run_id, resolve_export_storage
from common.csv_writer import write_csv
from common.headers import EDGE_HEADERS
from common.ids import (
    agency_balance_id,
    agency_id,
    email_pref_id,
    member_id,
    permission_id,
    promo_id,
    role_id,
    sales_coordinator_id,
    sales_head_id,
)


def _epoch_to_iso(val: Any) -> Any:
    """Convert a Unix epoch integer (seconds or milliseconds) to an ISO 8601 string.

    DynamoDB sometimes stores timestamps as epoch integers. Neptune expects
    ISO strings for :String typed date columns. Non-numeric values are
    returned as-is.
    """
    if val is None:
        return None
    try:
        epoch = int(val)
    except (TypeError, ValueError):
        return val  # already a string, pass through
    # Heuristic: values > 1e10 are milliseconds, otherwise seconds
    if epoch > 10_000_000_000:
        epoch = epoch // 1000
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _import_boto3():
    try:
        import boto3  # type: ignore
    except ImportError as exc:
        raise RuntimeError("boto3 is required for Dynamo export. Install with: pip install boto3") from exc
    return boto3


def _is_local() -> bool:
    return os.getenv("IS_LOCAL", "false").strip().lower() == "true"


def _aws_session():
    boto3 = _import_boto3()
    region = os.getenv("AWS_REGION", "ap-south-1")
    access_key = os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    session_token = os.getenv("AWS_SESSION_TOKEN")
    profile = os.getenv("AWS_PROFILE")

    if _is_local():
        if access_key and secret_key:
            return boto3.Session(
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                aws_session_token=session_token,
                region_name=region,
            )
        if profile:
            return boto3.Session(profile_name=profile, region_name=region)
        raise RuntimeError(
            "IS_LOCAL=true requires credentials. Set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY "
            "(and AWS_SESSION_TOKEN if temporary) or AWS_PROFILE."
        )

    return boto3.Session(region_name=region)


def _scan_segment(table, segment: int, total_segments: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    response = table.scan(Segment=segment, TotalSegments=total_segments)
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = table.scan(
            ExclusiveStartKey=response["LastEvaluatedKey"],
            Segment=segment,
            TotalSegments=total_segments,
        )
        items.extend(response.get("Items", []))
    print(f"[dynamo] segment {segment + 1}/{total_segments}: {len(items)} items")
    return items


def _parallel_scan(table, label: str) -> list[dict[str, Any]]:
    segments = dynamo_scan_segments()
    print(f"[dynamo] parallel scan {label} segments={segments}")
    parts = map_parallel(
        lambda segment: _scan_segment(table, segment, segments),
        range(segments),
        max_workers=segments,
    )
    merged = [item for part in parts for item in part]
    print(f"[dynamo] {label} total items={len(merged)}")
    return merged


def _process_customer360(c360_items: list[dict[str, Any]]) -> dict[str, Any]:
    agency_balances = []
    sales_heads = []
    sales_coordinators = []
    email_prefs = []
    promos: dict[str, list[Any]] = {}
    customer360_edges = []
    edge_seq = 1
    sales_head_member_links: set[str] = set()
    coordinator_member_links: set[str] = set()

    for item in c360_items:
        pk = item.get("pk", "")
        sk = item.get("sk", "")
        if not pk or not sk:
            continue

        if pk.startswith("Agency#") and sk == "Static#AgencyBalance":
            aid = pk.split("#", 1)[1]
            agency_balances.append(
                [
                    agency_balance_id(aid),
                    "AgencyBalance",
                    aid,
                    item.get("AvailableBalance"),
                    item.get("CreditLimit"),
                    item.get("CashBalance"),
                    item.get("LccBalance"),
                    item.get("LocalCreditLimit"),
                    item.get("CurrencyCode"),
                    item.get("IsCreditLimitReleased"),
                    item.get("UpdatedAt"),
                ]
            )
            customer360_edges.append(
                [
                    f"DYN-E-{edge_seq:08d}",
                    agency_id(aid),
                    agency_balance_id(aid),
                    "HAS_BALANCE",
                    item.get("UpdatedAt"),
                    None,
                    None,
                    None,
                    None,
                    None,
                ]
            )
            edge_seq += 1

        elif pk.startswith("Agency#") and sk == "Static#SalesHead":
            aid = pk.split("#", 1)[1]
            sh_id = item.get("SalesHeadMemberId")
            if sh_id is None:
                continue
            sales_heads.append(
                [
                    sales_head_id(sh_id),
                    "SalesHead",
                    sh_id,
                    item.get("SalesHeadEmail"),
                    item.get("SalesHeadCountry"),
                    item.get("SalesHeadContactNumber"),
                ]
            )
            customer360_edges.append(
                [
                    f"DYN-E-{edge_seq:08d}",
                    agency_id(aid),
                    sales_head_id(sh_id),
                    "MANAGED_BY_SALESHEAD",
                    None,
                    item.get("AssignedAt"),
                    None,
                    None,
                    None,
                    None,
                ]
            )
            edge_seq += 1
            sh_key = str(sh_id)
            if sh_key not in sales_head_member_links:
                customer360_edges.append(
                    [
                        f"DYN-E-{edge_seq:08d}",
                        sales_head_id(sh_id),
                        member_id(sh_id),
                        "SALESHEAD_IS_MEMBER",
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                    ]
                )
                edge_seq += 1
                sales_head_member_links.add(sh_key)

        elif pk.startswith("Agency#") and sk == "Static#SalesCoordinator":
            aid = pk.split("#", 1)[1]
            sc_id = item.get("SalesCoordinatorId")
            if sc_id is None:
                continue
            sales_coordinators.append(
                [sales_coordinator_id(sc_id), "SalesCoordinator", sc_id, item.get("SalesCoordinatorEmail")]
            )
            customer360_edges.append(
                [
                    f"DYN-E-{edge_seq:08d}",
                    agency_id(aid),
                    sales_coordinator_id(sc_id),
                    "MANAGED_BY_COORDINATOR",
                    None,
                    item.get("AssignedAt"),
                    None,
                    None,
                    None,
                    None,
                ]
            )
            edge_seq += 1
            sc_key = str(sc_id)
            if sc_key not in coordinator_member_links:
                customer360_edges.append(
                    [
                        f"DYN-E-{edge_seq:08d}",
                        sales_coordinator_id(sc_id),
                        member_id(sc_id),
                        "COORDINATOR_IS_MEMBER",
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                    ]
                )
                edge_seq += 1
                coordinator_member_links.add(sc_key)

        elif pk.startswith("Member#") and "#All" in sk and sk.startswith("EmailPreference#"):
            mid = pk.split("#", 1)[1]
            parts = sk.split("#")
            if len(parts) < 4:
                continue
            product_type = parts[1]
            email_type = parts[2]
            pref_vid = email_pref_id(mid, product_type, email_type)
            email_prefs.append(
                [
                    pref_vid,
                    "EmailPreference",
                    mid,
                    item.get("AgencyId"),
                    product_type,
                    email_type,
                    item.get("SendToBooker"),
                    item.get("SendToAdmin"),
                    item.get("SendToFinance"),
                    item.get("IsActive"),
                    item.get("IsDefaultEntry"),
                    _epoch_to_iso(item.get("CreatedDate")),
                ]
            )
            customer360_edges.append(
                [
                    f"DYN-E-{edge_seq:08d}",
                    member_id(mid),
                    pref_vid,
                    "HAS_EMAIL_PREF",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ]
            )
            edge_seq += 1

        elif pk.startswith("Member#") and sk.startswith("ConsumedSalesPromo#"):
            mid = pk.split("#", 1)[1]
            promo_code = sk.split("#", 1)[1]
            p_vid = promo_id(promo_code)
            promos[p_vid] = [
                p_vid,
                "Promo",
                promo_code,
                item.get("PromoCode", promo_code),
                item.get("PromoType"),
                item.get("DiscountValue"),
                item.get("DiscountType"),
                item.get("ValidFrom"),
                item.get("ValidTo"),
                item.get("MaxUsage"),
            ]
            customer360_edges.append(
                [
                    f"DYN-E-{edge_seq:08d}",
                    member_id(mid),
                    p_vid,
                    "CONSUMED_PROMO",
                    None,
                    None,
                    None,
                    item.get("ConsumedAt"),
                    item.get("BookingAmount"),
                    item.get("AgencyId"),
                ]
            )
            edge_seq += 1

    return {
        "agency_balances": agency_balances,
        "sales_heads": sales_heads,
        "sales_coordinators": sales_coordinators,
        "email_prefs": email_prefs,
        "promos": promos,
        "customer360_edges": customer360_edges,
    }


def _process_role_permissions(rp_items: list[dict[str, Any]]) -> dict[str, Any]:
    roles: dict[str, list[Any]] = {}
    permissions: dict[str, list[Any]] = {}
    customer360_edges = []
    edge_seq = 1

    for item in rp_items:
        pk = item.get("pk", "")
        sk = item.get("sk", "")
        if not pk.startswith("Role#") or not sk.startswith("PID#"):
            continue
        rid = pk.split("#", 1)[1]
        _, product_type, permission = sk.split("#", 2)
        r_vid = role_id(rid)
        p_vid = permission_id(permission, product_type)
        roles[r_vid] = [r_vid, "Role", rid, item.get("RoleName"), item.get("RoleCategory"), None]
        permissions[p_vid] = [
            p_vid,
            "Permission",
            permission,
            product_type,
            item.get("TaskName"),
            item.get("Description"),
        ]
        customer360_edges.append(
            [
                f"DYN-E-{edge_seq:08d}",
                r_vid,
                p_vid,
                "ROLE_PERMITS",
                None,
                None,
                product_type,
                None,
                None,
                None,
            ]
        )
        edge_seq += 1

    return {"roles": roles, "permissions": permissions, "customer360_edges": customer360_edges}


def export_dynamo(output_root: Path) -> None:
    region = os.getenv("AWS_REGION", "ap-south-1")
    customer360_table_name = os.getenv("DYNAMO_CUSTOMER360_TABLE")
    role_permissions_table_name = os.getenv("DYNAMO_ROLE_PERMISSIONS_TABLE")
    if not customer360_table_name:
        raise RuntimeError("Set DYNAMO_CUSTOMER360_TABLE")
    
    # RolePermissions360 is optional — if not set, skip it
    if not role_permissions_table_name:
        print("[warning] DYNAMO_ROLE_PERMISSIONS_TABLE not set — skipping Role/Permission vertices")

    session = _aws_session()
    is_local = _is_local()
    auth_mode = (
        "env/profile credentials"
        if is_local
        else "IAM role / default provider chain"
    )
    print("[startup] Dynamo export")
    print(f"[startup] IS_LOCAL={is_local}")
    print(f"[startup] Auth mode={auth_mode}")
    print(f"[startup] AWS_REGION={region}")
    print(f"[startup] DYNAMO_CUSTOMER360_TABLE={customer360_table_name}")
    print(f"[startup] DYNAMO_ROLE_PERMISSIONS_TABLE={role_permissions_table_name or '(optional, not set)'}")
    print(f"[startup] Output root={output_root}")
    print(f"[startup] EXPORT_WORKERS={worker_count()}")
    print(f"[startup] DYNAMO_SCAN_SEGMENTS={dynamo_scan_segments()}")

    config = Config(**boto3_client_settings())
    ddb = session.resource("dynamodb", region_name=region, config=config)
    customer360 = ddb.Table(customer360_table_name)
    role_permissions = ddb.Table(role_permissions_table_name) if role_permissions_table_name else None

    vertices_dir = output_root / "dynamo" / "vertices"
    edges_dir = output_root / "dynamo" / "edges"

    # Clean stale files from previous runs to prevent old pre-chunking files
    # being picked up alongside newly written chunks by the combine step.
    import shutil
    for d in (vertices_dir, edges_dir):
        if d.exists():
            shutil.rmtree(d)
            print(f"[startup] Cleaned stale output dir: {d}")

    scans = run_parallel(
        [
            ("customer360", lambda: _parallel_scan(customer360, customer360_table_name)),
            ("role_permissions", lambda: _parallel_scan(role_permissions, role_permissions_table_name) if role_permissions else []),
        ],
        max_workers=2,
    )

    c360_data = _process_customer360(scans["customer360"])
    rp_data = _process_role_permissions(scans["role_permissions"])
    customer360_edges = c360_data["customer360_edges"] + rp_data["customer360_edges"]
    # Both _process_customer360 and _process_role_permissions mint DYN-E- IDs
    # from their own counters starting at 1, so the two ID ranges collide.
    # Edge IDs are never referenced as endpoints (only vertex IDs are), so it is
    # safe to reassign a single globally-unique sequence across the merged list.
    for i, row in enumerate(customer360_edges, start=1):
        row[0] = f"DYN-E-{i:08d}"

    run_parallel(
        [
            (
                "agency_balances.csv",
                lambda: write_csv(
                    vertices_dir / "agency_balances.csv",
                    [
                        "~id",
                        "~label",
                        "agencyId:Long",
                        "availableBalance:String",
                        "creditLimit:String",
                        "cashBalance:String",
                        "lccBalance:String",
                        "localCreditLimit:String",
                        "currencyCode:String",
                        "isCreditLimitReleased:Bool",
                        "updatedAt:String",
                    ],
                    c360_data["agency_balances"],
                ),
            ),
            (
                "sales_heads.csv",
                lambda: write_csv(
                    vertices_dir / "sales_heads.csv",
                    [
                        "~id",
                        "~label",
                        "salesHeadMemberId:Long",
                        "salesHeadEmail:String",
                        "salesHeadCountry:String",
                        "salesHeadContactNumber:String",
                    ],
                    c360_data["sales_heads"],
                ),
            ),
            (
                "sales_coordinators.csv",
                lambda: write_csv(
                    vertices_dir / "sales_coordinators.csv",
                    ["~id", "~label", "salesCoordinatorId:Long", "salesCoordinatorEmail:String"],
                    c360_data["sales_coordinators"],
                ),
            ),
            (
                "email_preferences.csv",
                lambda: write_csv(
                    vertices_dir / "email_preferences.csv",
                    [
                        "~id",
                        "~label",
                        "memberId:Long",
                        "agencyId:Long",
                        "productType:String",
                        "emailType:String",
                        "sendToBooker:Bool",
                        "sendToAdmin:Bool",
                        "sendToFinance:Bool",
                        "isActive:Bool",
                        "isDefaultEntry:Bool",
                        "createdAt:String",
                    ],
                    c360_data["email_prefs"],
                ),
            ),
            (
                "roles.csv",
                lambda: write_csv(
                    vertices_dir / "roles.csv",
                    ["~id", "~label", "roleId:Int", "roleName:String", "roleCategory:String", "description:String"],
                    rp_data["roles"].values(),
                ),
            ),
            (
                "permissions.csv",
                lambda: write_csv(
                    vertices_dir / "permissions.csv",
                    [
                        "~id",
                        "~label",
                        "permissionId:String",
                        "productType:String",
                        "taskName:String",
                        "description:String",
                    ],
                    rp_data["permissions"].values(),
                ),
            ),
            (
                "promos.csv",
                lambda: write_csv(
                    vertices_dir / "promos.csv",
                    [
                        "~id",
                        "~label",
                        "promoId:String",
                        "promoCode:String",
                        "promoType:String",
                        "discountValue:Double",
                        "discountType:String",
                        "validFrom:Date",
                        "validTo:Date",
                        "maxUsage:Int",
                    ],
                    c360_data["promos"].values(),
                ),
            ),
            (
                "edges-customer360.csv",
                lambda: write_csv(
                    edges_dir / "edges-customer360.csv",
                    EDGE_HEADERS["customer360"],
                    customer360_edges,
                ),
            ),
        ],
        max_workers=min(8, worker_count()),
    )


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Export DynamoDB data into Neptune CSV files.")
    parser.add_argument("--output-root", default="../data", help="Base output directory.")
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    storage = resolve_export_storage(
        args.output_root,
        base_dir=base,
        run_id=default_run_id(),
    )
    export_dynamo(storage.local_root)
    storage.sync_tree("dynamo")


if __name__ == "__main__":
    main()

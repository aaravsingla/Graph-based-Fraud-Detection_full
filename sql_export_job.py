from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from common.concurrency import map_parallel, run_parallel, worker_count
from common.storage import default_run_id, resolve_export_storage
from common.csv_writer import write_csv
from common.edges import booking_edge_row
from common.headers import EDGE_HEADERS, VERTEX_HEADERS
from common.ids import (
    address_id,
    agency_id,
    hotel_id,
    itinerary_id,
    ledger_id,
    member_id,
)

SQL_QUERIES: dict[str, str] = {
    "agencies": """
        SELECT
          a.agencyId,
          a.agencyName,
          CAST(a.agencyTypeId AS nvarchar(50)) AS agencyType,
          a.GSAType AS gsaType,
          a.travelGroup,
          a.AffiliateAdminId AS affiliateAdminId,
          ISNULL(CAST(cam_sso.IsSSO AS int), 0) AS isSSO,
          pa.PriorityCategory,
          pa.Award,
          a.creditLimit,
          a.currency,
          a.createdOn,
          apd.firstSignin AS agencyFirstSignIn,
          a.HeadOfficeAgencyId,
          a.addressId
        FROM agency a WITH (NOLOCK)
        LEFT JOIN AgencyProfileDetails apd WITH (NOLOCK) ON apd.agencyId = a.agencyId
        LEFT JOIN PriorityAgents pa WITH (NOLOCK) ON pa.AgencyId = a.agencyId
        OUTER APPLY (
          SELECT TOP 1 cam.IsSSO
          FROM ConsortiaAgencyMapping cam WITH (NOLOCK)
          WHERE cam.ConsortiaAgencyId = CAST(a.agencyId AS varchar(50))
        ) cam_sso
    """,
    "members": """
        SELECT
          m.memberId, m.email, m.status, m.agencyId, m.roleId, m.isPrimaryAgent,
          m.prefLang AS prefLanguage, m.mobileNumber, m.createdOn,
          rl.role AS roleName, rc.categoryName AS roleCategory,
          m.addressId
        FROM member m WITH (NOLOCK)
        LEFT JOIN [Role] rl WITH (NOLOCK) ON rl.roleId = m.roleId
        LEFT JOIN RoleCategory rc WITH (NOLOCK) ON rc.roleId = m.roleId
    """,
    "addresses": """
        SELECT addressId, line1 AS addressLine, cityOther AS city, countryCode,
               pin AS postalCode, mobile, phone
        FROM address WITH (NOLOCK)
    """,
    "ledgers": """
        SELECT
          l.ledgerId,
          l.agencyId,
          l.currentBalance AS balance,
          a.currency AS currencyCode,
          l.lastModifiedOn AS updatedAt
        FROM Ledger l WITH (NOLOCK)
        LEFT JOIN agency a WITH (NOLOCK) ON a.agencyId = l.agencyId
    """,
    "itineraries": """
        SELECT
          hi.hotelId AS itineraryId,
          hi.bookingMemberId AS memberId,
          hi.agencyId,
          hi.hotelCode,
          hi.AgentPrice AS totalAmount,
          hi.status,
          hi.checkInDate AS checkIn,
          hi.checkOutDate AS checkOut,
          hi.createdOn AS bookedAt,
          hi.deviceId
        FROM HotelItinerary hi WITH (NOLOCK)
        WHERE hi.createdOn >= DATEADD(month, -3, GETDATE())
    """,
}


def _import_pyodbc():
    try:
        import pyodbc  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "pyodbc is required for SQL export. Install with: pip install pyodbc"
        ) from exc
    return pyodbc


def _connect():
    pyodbc = _import_pyodbc()
    conn_str = os.getenv("SQL_CONN_STR")
    dsn = os.getenv("SQL_DSN")
    if conn_str:
        return pyodbc.connect(conn_str)
    if dsn:
        return pyodbc.connect(f"DSN={dsn}")

    host = os.getenv("SQL_HOST")
    database = os.getenv("SQL_DATABASE")
    username = os.getenv("SQL_USERNAME")
    password = os.getenv("SQL_PASSWORD")
    if host and database and username and password:
        port = os.getenv("SQL_PORT", "1433")
        driver = os.getenv("SQL_DRIVER", "ODBC Driver 18 for SQL Server")
        encrypt = os.getenv("SQL_ENCRYPT", "yes")
        trust_server_cert = os.getenv("SQL_TRUST_SERVER_CERTIFICATE", "yes")
        built_conn_str = (
            f"DRIVER={{{driver}}};"
            f"SERVER={host},{port};"
            f"DATABASE={database};"
            f"UID={username};"
            f"PWD={password};"
            f"Encrypt={encrypt};"
            f"TrustServerCertificate={trust_server_cert};"
            f"Connection Timeout=30;"
        )
        timeout = int(os.getenv("SQL_QUERY_TIMEOUT", "600"))
        return pyodbc.connect(built_conn_str, timeout=timeout)
        return pyodbc.connect(built_conn_str)

    raise RuntimeError(
        "Set one of: "
        "SQL_CONN_STR, SQL_DSN, or SQL_HOST/SQL_PORT/SQL_DATABASE/SQL_USERNAME/SQL_PASSWORD"
    )


def _sql_auth_summary() -> str:
    if os.getenv("SQL_CONN_STR"):
        return "SQL_CONN_STR"
    if os.getenv("SQL_DSN"):
        return "SQL_DSN"
    if (
        os.getenv("SQL_HOST")
        and os.getenv("SQL_DATABASE")
        and os.getenv("SQL_USERNAME")
        and os.getenv("SQL_PASSWORD")
    ):
        return "SQL_HOST/SQL_PORT/SQL_DATABASE/SQL_USERNAME/SQL_PASSWORD"
    return "unknown"


def _rows(cursor, query: str) -> list[dict[str, Any]]:
    cursor.execute(query)
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _fetch_named_query(name_query: tuple[str, str]) -> tuple[str, list[dict[str, Any]]]:
    name, query = name_query
    conn = _connect()
    try:
        cursor = conn.cursor()
        rows = _rows(cursor, query)
        print(f"[sql] fetched {name}: {len(rows)} rows")
        return name, rows
    finally:
        conn.close()


def _fetch_all_queries_parallel() -> dict[str, list[dict[str, Any]]]:
    workers = min(worker_count(), len(SQL_QUERIES))
    print(f"[sql] parallel table fetch workers={workers}")
    pairs = map_parallel(_fetch_named_query, SQL_QUERIES.items(), max_workers=workers)
    return dict(pairs)


def export_sql(output_root: Path) -> None:
    vertices_dir = output_root / "sql" / "vertices"
    edges_dir = output_root / "sql" / "edges"
    print("[startup] SQL export")
    print(f"[startup] Connection mode={_sql_auth_summary()}")
    print(f"[startup] SQL_HOST={os.getenv('SQL_HOST')}")
    print(f"[startup] SQL_DATABASE={os.getenv('SQL_DATABASE')}")
    print(f"[startup] Output root={output_root}")
    print(f"[startup] EXPORT_WORKERS={worker_count()}")

    # Clean stale files from previous runs so old pre-chunking files
    # don't get picked up alongside newly written chunks by the combine step.
    import shutil
    for d in (vertices_dir, edges_dir):
        if d.exists():
            shutil.rmtree(d)
            print(f"[startup] Cleaned stale output dir: {d}")

    data = _fetch_all_queries_parallel()
    agencies = data["agencies"]
    members = data["members"]
    addresses = data["addresses"]
    ledgers = data["ledgers"]
    itineraries = data["itineraries"]

    write_tasks = [
        (
            "agencies.csv",
            lambda: write_csv(
                vertices_dir / "agencies.csv",
                VERTEX_HEADERS["agencies"],
                [
                    [
                        agency_id(r["agencyId"]),
                        "Agency",
                        r["agencyId"],
                        r.get("agencyName"),
                        r.get("agencyType"),
                        r.get("gsaType"),
                        None,
                        r.get("travelGroup"),
                        r.get("affiliateAdminId"),
                        bool(r.get("isSSO")) if r.get("isSSO") is not None else None,
                        r.get("PriorityCategory"),
                        r.get("Award"),
                        r.get("creditLimit"),
                        r.get("currency"),
                        r.get("createdOn"),
                        r.get("agencyFirstSignIn"),
                    ]
                    for r in agencies
                ],
            ),
        ),
        (
            "members.csv",
            lambda: write_csv(
                vertices_dir / "members.csv",
                VERTEX_HEADERS["members"],
                [
                    [
                        member_id(r["memberId"]),
                        "Member",
                        r["memberId"],
                        r.get("email"),
                        "Active" if bool(r.get("status")) else "Inactive",
                        r.get("agencyId"),
                        r.get("roleId"),
                        r.get("roleName"),
                        bool(r.get("isPrimaryAgent")) if r.get("isPrimaryAgent") is not None else None,
                        r.get("prefLanguage"),
                        r.get("mobileNumber"),
                        r.get("createdOn"),
                    ]
                    for r in members
                ],
            ),
        ),
        (
            "addresses.csv",
            lambda: write_csv(
                vertices_dir / "addresses.csv",
                [
                    "~id",
                    "~label",
                    "addressId:Long",
                    "addressLine:String",
                    "city:String",
                    "countryCode:String",
                    "postalCode:String",
                    "mobile:String",
                    "phone:String",
                ],
                [
                    [
                        address_id(r["addressId"]),
                        "Address",
                        r["addressId"],
                        r.get("addressLine"),
                        r.get("city"),
                        r.get("countryCode"),
                        r.get("postalCode"),
                        r.get("mobile"),
                        r.get("phone"),
                    ]
                    for r in addresses
                ],
            ),
        ),
        (
            "ledgers.csv",
            lambda: write_csv(
                vertices_dir / "ledgers.csv",
                [
                    "~id",
                    "~label",
                    "ledgerId:Long",
                    "balance:Double",
                    "currencyCode:String",
                    "updatedAt:String",  # String — SQL returns "YYYY-MM-DD HH:MM:SS.ffffff", not strict ISO
                ],
                [
                    [
                        ledger_id(r["ledgerId"]),
                        "Ledger",
                        r["ledgerId"],
                        r.get("balance"),
                        r.get("currencyCode"),
                        r.get("updatedAt"),
                    ]
                    for r in ledgers
                ],
            ),
        ),
        (
            "hotel_itineraries.csv",
            lambda: write_csv(
                vertices_dir / "hotel_itineraries.csv",
                [
                    "~id",
                    "~label",
                    "itineraryId:Long",
                    "hotelCode:String",
                    "totalAmount:Double",
                    "currency:String",
                    "status:String",
                    "checkIn:String",   # SQL returns datetime strings, not bare YYYY-MM-DD
                    "checkOut:String",  # SQL returns datetime strings, not bare YYYY-MM-DD
                    "bookedAt:String",  # String — Neptune Datetime requires strict ISO, SQL values vary
                ],
                [
                    [
                        itinerary_id(r["itineraryId"]),
                        "HotelItinerary",
                        r["itineraryId"],
                        r.get("hotelCode"),
                        r.get("totalAmount"),
                        "AED",
                        r.get("status"),
                        r.get("checkIn"),
                        r.get("checkOut"),
                        r.get("bookedAt"),
                    ]
                    for r in itineraries
                ],
            ),
        ),
    ]

    run_parallel(write_tasks, max_workers=min(5, worker_count()))

    edge_rows = []
    fraud_rows = []
    seq = 1
    fseq = 1

    for r in members:
        member_agency_id = r.get("agencyId")
        # Only emit agency-linked edges when the member actually has an agency.
        # Members with a NULL agencyId previously produced edges to the
        # non-existent vertex "agency:None", which Neptune rejects.
        if member_agency_id is not None:
            edge_rows.append(
                [
                    f"SQL-E-{seq:08d}",
                    member_id(r["memberId"]),
                    agency_id(member_agency_id),
                    "BELONGS_TO",
                    r.get("createdOn"),
                    r.get("roleId"),
                    r.get("roleName"),
                    r.get("roleCategory"),
                    bool(r.get("isPrimaryAgent")) if r.get("isPrimaryAgent") is not None else None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ]
            )
            seq += 1
            if r.get("isPrimaryAgent"):
                edge_rows.append(
                    [
                        f"SQL-E-{seq:08d}",
                        member_id(r["memberId"]),
                        agency_id(member_agency_id),
                        "IS_PRIMARY_AGENT_FOR",
                        None,  # joinedAt
                        None,  # roleId
                        None,  # roleName
                        None,  # roleCategory
                        None,  # isPrimaryAgent
                        None,  # relationshipType
                        None,  # riskWeight
                        None,  # propagationRule
                        None,  # createdAt
                        None,  # updatedAt
                        None,  # previousBalance
                        None,  # newBalance
                        r.get("createdOn"),  # registeredAt
                        None,  # assignedAt
                    ]
                )
                seq += 1
        # REGISTERED_AT is independent of agency — keep it OUTSIDE the guard
        if r.get("addressId"):
            edge_rows.append(
                [
                    f"SQL-E-{seq:08d}",
                    member_id(r["memberId"]),
                    address_id(r["addressId"]),
                    "REGISTERED_AT",
                    None,  # joinedAt
                    None,  # roleId
                    None,  # roleName
                    None,  # roleCategory
                    None,  # isPrimaryAgent
                    None,  # relationshipType
                    None,  # riskWeight
                    None,  # propagationRule
                    None,  # createdAt
                    None,  # updatedAt
                    None,  # previousBalance
                    None,  # newBalance
                    r.get("createdOn"),  # registeredAt
                    None,  # assignedAt
                ]
            )
            seq += 1

    exported_agency_ids = {str(r["agencyId"]) for r in agencies}

    for r in agencies:
        head_office = r.get("HeadOfficeAgencyId")
        # Only emit the head-office RELATED_TO edge when the parent agency is
        # actually in the exported set. Otherwise it points at a vertex that is
        # never written (e.g. agency:108000), which Neptune rejects.
        if head_office is not None and str(head_office) in exported_agency_ids:
            edge_rows.append(
                [
                    f"SQL-E-{seq:08d}",
                    agency_id(r["agencyId"]),
                    agency_id(r["HeadOfficeAgencyId"]),
                    "RELATED_TO",
                    None,
                    None,
                    None,
                    None,
                    None,
                    "HEAD_OFFICE",
                    0.9,
                    "BIDIRECTIONAL",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ]
            )
            seq += 1
        if r.get("addressId"):
            edge_rows.append(
                [
                    f"SQL-E-{seq:08d}",
                    agency_id(r["agencyId"]),
                    address_id(r["addressId"]),
                    "HEADQUARTERED_AT",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    r.get("createdOn"),
                    None,
                ]
            )
            seq += 1

    for r in ledgers:
        ledger_agency_id = r.get("agencyId")
        # ledgers LEFT JOINs agency, so agencyId can be NULL. Guard against
        # emitting an OWNS_LEDGER edge to the non-existent vertex "agency:None".
        if ledger_agency_id is None:
            continue
        edge_rows.append(
            [
                f"SQL-E-{seq:08d}",
                agency_id(ledger_agency_id),
                ledger_id(r["ledgerId"]),
                "OWNS_LEDGER",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            ]
        )
        seq += 1

    for r in itineraries:
        hid = r["itineraryId"]
        mid = r.get("memberId")
        aid = r.get("agencyId")
        booked_at = r.get("bookedAt")
        hotel_code = r.get("hotelCode")
        device = r.get("deviceId")
        itin_vid = itinerary_id(hid)

        if mid:
            fraud_rows.append(
                booking_edge_row(
                    f"SQL-F-{fseq:08d}",
                    member_id(mid),
                    itin_vid,
                    "CREATED_BOOKING",
                    agency_id=aid,
                    booked_at=booked_at,
                )
            )
            fseq += 1
        if aid:
            fraud_rows.append(
                booking_edge_row(
                    f"SQL-F-{fseq:08d}",
                    itin_vid,
                    agency_id(aid),
                    "BOOKING_FOR",
                    booked_at=booked_at,
                )
            )
            fseq += 1
        if device:
            fraud_rows.append(
                booking_edge_row(
                    f"SQL-F-{fseq:08d}",
                    itin_vid,
                    f"device:{device}",
                    "BOOKED_WITH_DEVICE",
                    booked_at=booked_at,
                )
            )
            fseq += 1
        if hotel_code:
            fraud_rows.append(
                booking_edge_row(
                    f"SQL-F-{fseq:08d}",
                    itin_vid,
                    hotel_id(str(hotel_code)),
                    "BOOKED_AT_HOTEL",
                    booked_at=booked_at,
                )
            )
            fseq += 1

    run_parallel(
        [
            (
                "edges-core.csv",
                lambda: write_csv(edges_dir / "edges-core.csv", EDGE_HEADERS["core"], edge_rows),
            ),
            (
                "edges-fraud.csv",
                lambda: write_csv(edges_dir / "edges-fraud.csv", EDGE_HEADERS["booking"], fraud_rows),
            ),
        ],
        max_workers=2,
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
    parser = argparse.ArgumentParser(description="Export SQL data into Neptune CSV files.")
    parser.add_argument(
        "--output-root",
        default="../data",
        help="Base folder where source output folders are created.",
    )
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    storage = resolve_export_storage(
        args.output_root,
        base_dir=base,
        run_id=default_run_id(),
    )
    export_sql(storage.local_root)
    storage.sync_tree("sql")


if __name__ == "__main__":
    main()

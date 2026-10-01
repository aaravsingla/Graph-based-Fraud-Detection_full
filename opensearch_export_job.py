from __future__ import annotations

import argparse
import csv
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from common.concurrency import (
    map_parallel,
    opensearch_scroll_slices,
    run_parallel,
    worker_count,
)
from common.storage import default_run_id, resolve_export_storage
from common.coerce import coerce_long, first_present
from common.csv_writer import ensure_dir, write_csv
from common.headers import EDGE_HEADERS, VERTEX_HEADERS
from common.hotel_fields import (
    city_code_from_doc,
    country_code_from_doc,
    geo_lat_lon_from_doc,
    hotel_code_from_doc,
    star_rating_from_doc,
)
from common.ids import auth_event_id, hotel_id, member_id_optional, stepup_event_id, stepup_rule_id

# ---------------------------------------------------------------------------
# Synthetic step-up configuration
# ---------------------------------------------------------------------------

# Default number of synthetic step-up events to generate when real OpenSearch
# step-up index is skipped (MVP mode).  Override via env var SYNTHETIC_STEPUP_COUNT.
_DEFAULT_SYNTHETIC_STEPUP_COUNT = 100

_SYNTHETIC_STEPUP_RULES = [
    "HighTransactionValue",
    "MultipleBookingsLastHour",
    "FailedLoginThreshold",
    "NewDeviceBooking",
    "HighRiskDestination",
]

# Default cap on hotel vertices for MVP demonstrations.
# Set to None (or HOTEL_LIMIT=0 in env) to export all hotels.
_DEFAULT_HOTEL_LIMIT = 500


def _import_opensearch():
    try:
        from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "opensearch-py is required. Install with: pip install opensearch-py"
        ) from exc
    return OpenSearch, RequestsHttpConnection, AWSV4SignerAuth


def _is_local() -> bool:
    return os.getenv("IS_LOCAL", "false").strip().lower() == "true"


def _import_boto3():
    try:
        import boto3  # type: ignore
    except ImportError as exc:
        raise RuntimeError("boto3 is required for OpenSearch SigV4 auth.") from exc
    return boto3


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


def _normalize_endpoint(endpoint: str) -> str:
    value = endpoint.strip()
    for prefix in ("https://", "http://"):
        if value.lower().startswith(prefix):
            value = value[len(prefix) :]
    return value.strip().strip("/")


def _client():
    OpenSearch, RequestsHttpConnection, AWSV4SignerAuth = _import_opensearch()
    endpoint = _normalize_endpoint(os.getenv("OPENSEARCH_ENDPOINT", ""))
    if not endpoint:
        raise RuntimeError("Set OPENSEARCH_ENDPOINT")
    port = int(os.getenv("OPENSEARCH_PORT", "443"))
    use_ssl = os.getenv("OPENSEARCH_USE_SSL", "true").lower() == "true"
    verify_certs = os.getenv("OPENSEARCH_VERIFY_CERTS", "true").lower() == "true"

    username = os.getenv("OPENSEARCH_USERNAME")
    password = os.getenv("OPENSEARCH_PASSWORD")
    auth_mode = os.getenv("OPENSEARCH_AUTH_MODE", "auto").lower()
    is_local = _is_local()

    if auth_mode not in {"auto", "basic", "sigv4"}:
        raise RuntimeError("OPENSEARCH_AUTH_MODE must be one of: auto, basic, sigv4")

    use_basic = auth_mode == "basic" or (
        auth_mode == "auto" and is_local and username and password
    )

    pool_size = max(10, worker_count() + 4)
    timeout = int(os.getenv("OPENSEARCH_TIMEOUT", "120"))
    client_kwargs: dict[str, Any] = {
        "hosts": [{"host": endpoint, "port": port}],
        "use_ssl": use_ssl,
        "verify_certs": verify_certs,
        "connection_class": RequestsHttpConnection,
        "pool_maxsize": pool_size,
        "timeout": timeout,
    }

    if use_basic:
        if not (username and password):
            raise RuntimeError("Basic auth requires OPENSEARCH_USERNAME and OPENSEARCH_PASSWORD")
        client = OpenSearch(http_auth=(username, password), **client_kwargs)
        setattr(client, "_synapse_auth_mode", "basic")
        return client

    aws_region = os.getenv("OPENSEARCH_REGION", os.getenv("AWS_REGION", "ap-south-1"))
    aws_service = os.getenv("OPENSEARCH_AWS_SERVICE", "es")
    session = _aws_session()
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError(
            "SigV4 auth selected but AWS credentials not found. "
            "Set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_SESSION_TOKEN or AWS_PROFILE."
        )
    auth = AWSV4SignerAuth(credentials, aws_region, aws_service)
    client = OpenSearch(http_auth=auth, **client_kwargs)
    setattr(client, "_synapse_auth_mode", "sigv4")
    return client


def _scroll(client, index: str, query: dict[str, Any], scroll_ttl: str = "2m", *, size: int = 1000) -> Iterable[dict[str, Any]]:
    resp = client.search(index=index, body=query, scroll=scroll_ttl, size=size)
    scroll_id = resp.get("_scroll_id")
    hits = resp.get("hits", {}).get("hits", [])
    while hits:
        for hit in hits:
            yield hit.get("_source", {})
        resp = client.scroll(scroll_id=scroll_id, scroll=scroll_ttl)
        scroll_id = resp.get("_scroll_id")
        hits = resp.get("hits", {}).get("hits", [])
    if scroll_id:
        client.clear_scroll(scroll_id=scroll_id)


def _hotel_limit() -> int | None:
    """Return the max number of hotel vertices to export, or None for unlimited.

    Reads HOTEL_LIMIT from the environment.  A value of 0 means unlimited.
    When not set the default is _DEFAULT_HOTEL_LIMIT (500) — enough for MVP.
    """
    raw = os.getenv("HOTEL_LIMIT", "").strip()
    if not raw:
        return _DEFAULT_HOTEL_LIMIT
    try:
        val = int(raw)
        return None if val <= 0 else val
    except ValueError:
        return _DEFAULT_HOTEL_LIMIT


def _hotel_row(doc: dict[str, Any]) -> list[Any] | None:
    code = hotel_code_from_doc(doc)
    if not code:
        return None
    lat, lon = geo_lat_lon_from_doc(doc)
    return [
        hotel_id(code),
        "Hotel",
        code,
        lat,
        lon,
        country_code_from_doc(doc),
        city_code_from_doc(doc),
        star_rating_from_doc(doc),
    ]


def _export_hotel_slice(args: tuple[int, int, str, Path]) -> int:
    slice_id, max_slices, index, path = args
    client = _client()
    ensure_dir(path.parent)
    query: dict[str, Any] = {
        "slice": {"id": slice_id, "max": max_slices},
        "query": {"match_all": {}},
    }
    count = 0
    last_progress = 0
    limit = _hotel_limit()
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(VERTEX_HEADERS["hotels"])
        for doc in _scroll(client, index, query, size=2000):
            # Per-slice limit: each slice gets an equal share so that after
            # merging we land close to the overall HOTEL_LIMIT.
            if limit is not None and count >= (limit // max_slices + 1):
                break
            row = _hotel_row(doc)
            if row:
                writer.writerow(row)
                count += 1
                # Progress indicator every 10000 records
                if count - last_progress >= 10000:
                    print(f"[hotels] slice {slice_id + 1}/{max_slices}: {count:,} rows exported...")
                    last_progress = count
    print(f"[hotels] ✓ slice {slice_id + 1}/{max_slices}: {count:,} rows complete")
    return count


def _merge_hotel_slices(slice_paths: list[Path], final_path: Path) -> int:
    ensure_dir(final_path.parent)
    seen: set[str] = set()
    count = 0
    limit = _hotel_limit()
    with final_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(VERTEX_HEADERS["hotels"])
        for path in slice_paths:
            if not path.exists():
                continue
            if limit is not None and count >= limit:
                break
            with path.open(encoding="utf-8") as slice_file:
                reader = csv.reader(slice_file)
                next(reader, None)
                for row in reader:
                    if limit is not None and count >= limit:
                        break
                    if len(row) < 3:
                        continue
                    code = row[2]
                    if code in seen:
                        continue
                    seen.add(code)
                    writer.writerow(row)
                    count += 1
    if limit is not None:
        print(f"[hotels] merged {count} unique hotel vertices (capped at {limit} for MVP)")
    else:
        print(f"[hotels] merged {count} unique hotel vertices")
    return count


def _export_hotels_parallel(index: str, final_path: Path, temp_dir: Path) -> int:
    slices = opensearch_scroll_slices()
    ensure_dir(temp_dir)
    slice_paths = [temp_dir / f"hotels_slice_{i}.csv" for i in range(slices)]
    print(f"[hotels] parallel sliced scroll slices={slices}")
    map_parallel(
        _export_hotel_slice,
        [(i, slices, index, slice_paths[i]) for i in range(slices)],
        max_workers=slices,
    )
    return _merge_hotel_slices(slice_paths, final_path)


def _safe_epoch(val: Any) -> Any:
    """Return None for negative epoch integers (e.g. .NET DateTime.MinValue = -62135596800000).

    These values are invalid timestamps that leak from the source system and
    cause Neptune parsing errors. Positive integers and ISO strings are passed through.
    """
    if val is None:
        return None
    try:
        if int(val) < 0:
            return None
    except (TypeError, ValueError):
        pass
    return val


def _sanitize_ip(ip_raw: Any) -> str | None:
    """Extract the first usable IP from a potentially multi-IP string.

    OpenSearch often stores IPs like "115.114.12.58, 163.116.199.114" (client + proxy).
    Neptune vertex IDs cannot contain commas, so we take only the first IP.
    """
    if not ip_raw:
        return None
    ip_str = str(ip_raw).strip()
    if not ip_str:
        return None
    # Take only the first IP if comma-separated
    first_ip = ip_str.split(",")[0].strip()
    return first_ip if first_ip else None


def _extract_threat_summary(threat_intel: Any) -> str | None:
    """Extract a compact threat summary from ipThreatIntel.

    The raw field is often a huge JSON blob with geolocation, ASN info, etc.
    Neptune doesn't need all that. We extract only the threat-relevant fields:
    is_vpn, is_proxy, is_tor, threat_score, trust_score.
    """
    if threat_intel is None:
        return None
    if isinstance(threat_intel, str):
        if not threat_intel.strip() or threat_intel.strip() == "null":
            return None
        # Try to parse JSON
        try:
            import json
            data = json.loads(threat_intel.replace('""', '"'))
        except (json.JSONDecodeError, ValueError):
            # If it's a simple string like "none" or "low", return as-is
            if len(threat_intel) < 50:
                return threat_intel
            return None
    elif isinstance(threat_intel, dict):
        data = threat_intel
    else:
        return str(threat_intel) if len(str(threat_intel)) < 50 else None

    # Extract only the threat section
    threat = data.get("threat", {})
    if not threat:
        return None
    scores = threat.get("scores", {})
    parts = []
    if threat.get("is_vpn"):
        parts.append("vpn")
    if threat.get("is_proxy"):
        parts.append("proxy")
    if threat.get("is_tor"):
        parts.append("tor")
    if threat.get("is_datacenter"):
        parts.append("datacenter")
    threat_score = scores.get("threat_score", 0)
    trust_score = scores.get("trust_score", 100)
    if parts:
        return f"{'|'.join(parts)};threat={threat_score};trust={trust_score}"
    if threat_score > 0:
        return f"threat={threat_score};trust={trust_score}"
    return None


def _stepup_hotel_star_ratings(doc: dict[str, Any]) -> str | None:
    val = doc.get("HotelStarRatings")
    if val is None:
        return None
    if isinstance(val, list):
        return "|".join(str(v) for v in val)
    return str(val)


STEPUP_VERTEX_HEADERS = [
    "~id",
    "~label",
    "TrackingId:String",
    "eventType:String",
    "MemberId:String",
    "AgencyId:String",
    "AgencyCountryCode:String",
    "ipAddress:String",
    "ipCountryCode:String",
    "deviceId:String",
    "cognitoDeviceId:String",
    "username:String",
    "isPrimaryAgent:String",     # can be empty — use String not Bool
    "isStepUpInitiated:String",  # can be empty — use String not Bool
    "result:String",
    "reason:String",
    "TransactionValue:String",
    "TransactionCurrency:String",
    "paymentId:String",
    "BookingCreatedOn:String",
    "BookingCheckInOn:String",
    "BookingAmountLastHours:String",
    "TotalBookingsLastHours:String",
    "TotalBookingsLastDaysExcludingHours:String",
    "FailedLoginCount:String",
    "StepUpHistoryExists:String",  # can be empty — use String not Bool
    "LastStepUpCompletedTime:String",
    "HotelStarRatings:String",
    "IsRefundableBooking:String",  # can be empty — use String not Bool
    "DestinationCountryCode:String",
    "UserCreatedOn:String",
    "timestamp:String",
    "processedAt:String",
]


def _export_login_bundle(login_index: str, login_query: dict[str, Any]) -> dict[str, Any]:
    client = _client()
    devices: dict[str, list[Any]] = {}
    ips: dict[str, list[Any]] = {}
    auth_events: list[list[Any]] = []
    edges_fraud: list[list[Any]] = []
    edges_auth: list[list[Any]] = []
    fseq = 1
    aseq = 1
    doc_count = 0
    last_progress = 0

    print(f"[export] scrolling login index: {login_index}")
    print("[login] Starting export... (progress every 5,000 records)")
    for doc in _scroll(client, login_index, login_query):
        doc_count += 1
        # Progress indicator every 5000 records
        if doc_count - last_progress >= 5000:
            print(f"[login] Processed {doc_count:,} documents, {len(auth_events):,} events, {len(devices):,} devices, {len(ips):,} IPs...")
            last_progress = doc_count
        
        auth_session = str(first_present(doc, "authSessionId") or "")
        if not auth_session:
            continue
        mid_raw = first_present(doc, "memberId", "MemberId")
        mid = coerce_long(mid_raw)
        mid_vid = member_id_optional(mid_raw)
        aid = coerce_long(first_present(doc, "AgencyId", "agencyId"))
        ip_raw = first_present(doc, "ipAddress")
        # Sanitize IP: take only first IP if multiple are present (comma-separated)
        ip = _sanitize_ip(ip_raw)
        device = first_present(doc, "deviceId")
        tboh_device = first_present(doc, "tbohDeviceId")
        # Sanitize ipThreatIntel: extract only the threat score, drop huge JSON
        ip_threat = _extract_threat_summary(doc.get("ipThreatIntel"))
        if device:
            device_vid = f"device:{device}"
            devices[device] = [
                device_vid,
                "Device",
                device,
                tboh_device,
                doc.get("deviceFingerprint"),
                None,
                None,
                None,
                doc.get("timestamp"),
                doc.get("timestamp"),
                None,
            ]
        else:
            device_vid = None
        if ip:
            ip_vid = f"ip:{ip}"
            ips[ip] = [
                ip_vid,
                "IpAddress",
                ip,
                doc.get("ipCountryCode"),
                doc.get("isp"),
                ip_threat,
                doc.get("timestamp"),
                doc.get("timestamp"),
                None,
            ]
        else:
            ip_vid = None

        auth_events.append(
            [
                auth_event_id(auth_session),
                "AuthEvent",
                auth_session,
                doc.get("eventType"),
                doc.get("challengeName"),
                doc.get("email"),
                mid,
                aid,
                ip,
                doc.get("ipCountryCode"),
                device,
                tboh_device,
                doc.get("isSuccessfulLogin"),
                _safe_epoch(doc.get("initAuthTime")),
                _safe_epoch(doc.get("respondToAuthTime")),
                doc.get("medium"),
                ip_threat,
                doc.get("SuspiciousMail"),
                doc.get("UserCreatedOn"),
                doc.get("timestamp"),
                doc.get("processedAt"),
            ]
        )

        if mid_vid and device_vid:
            edges_fraud.append(
                [
                    f"OPS-F-{fseq:08d}",
                    mid_vid,
                    device_vid,
                    "LOGGED_IN",
                    auth_session,
                    ip,
                    doc.get("ipCountryCode"),
                    ip_threat,
                    doc.get("timestamp"),
                    doc.get("isSuccessfulLogin"),
                    doc.get("challengeName"),
                    doc.get("medium"),
                    aid,
                    device,
                    tboh_device,
                    None,
                ]
            )
            fseq += 1
        if mid_vid and ip_vid:
            edges_fraud.append(
                [
                    f"OPS-F-{fseq:08d}",
                    mid_vid,
                    ip_vid,
                    "ACCESSED_FROM",
                    auth_session,
                    ip,
                    doc.get("ipCountryCode"),
                    None,
                    doc.get("timestamp"),
                    doc.get("isSuccessfulLogin"),
                    None,
                    None,
                    aid,
                    device,
                    tboh_device,
                    None,
                ]
            )
            fseq += 1
        if mid_vid:
            edges_auth.append(
                [
                    f"OPS-A-{aseq:08d}",
                    mid_vid,
                    auth_event_id(auth_session),
                    "HAD_AUTH_EVENT",
                    doc.get("timestamp"),
                    doc.get("isSuccessfulLogin"),
                    doc.get("challengeName"),
                    doc.get("medium"),
                    aid,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ]
            )
            aseq += 1
        if device_vid:
            edges_auth.append(
                [
                    f"OPS-A-{aseq:08d}",
                    auth_event_id(auth_session),
                    device_vid,
                    "AUTH_USED_DEVICE",
                    doc.get("timestamp"),
                    None,
                    None,
                    None,
                    aid,
                    ip,
                    device,
                    None,
                    None,
                    None,
                    None,
                ]
            )
            aseq += 1
        if ip_vid:
            edges_auth.append(
                [
                    f"OPS-A-{aseq:08d}",
                    auth_event_id(auth_session),
                    ip_vid,
                    "AUTH_FROM_IP",
                    doc.get("timestamp"),
                    None,
                    None,
                    None,
                    aid,
                    ip,
                    device,
                    None,
                    None,
                    None,
                    None,
                ]
            )
            aseq += 1

    print(f"[login] ✓ Complete! Total: {doc_count:,} docs, {len(auth_events):,} events, {len(devices):,} devices, {len(ips):,} IPs, {len(edges_fraud):,} fraud edges, {len(edges_auth):,} auth edges")
    
    return {
        "devices": devices,
        "ips": ips,
        "auth_events": auth_events,
        "edges_fraud": edges_fraud,
        "edges_auth": edges_auth,
    }


def _export_stepup_bundle(stepup_index: str, stepup_query: dict[str, Any]) -> dict[str, Any]:
    client = _client()
    stepup_events: list[list[Any]] = []
    stepup_rules: dict[str, list[Any]] = {}
    edges_auth: list[list[Any]] = []
    aseq = 1
    doc_count = 0
    last_progress = 0

    print(f"[export] scrolling step-up index: {stepup_index}")
    print("[stepup] Starting export... (progress every 5,000 records)")
    for doc in _scroll(client, stepup_index, stepup_query):
        doc_count += 1
        # Progress indicator every 5000 records
        if doc_count - last_progress >= 5000:
            print(f"[stepup] Processed {doc_count:,} documents, {len(stepup_events):,} events, {len(stepup_rules):,} rules...")
            last_progress = doc_count
        
        tracking_id = str(first_present(doc, "TrackingId") or "")
        if not tracking_id:
            continue
        mid_raw = first_present(doc, "MemberId", "memberId")
        mid = coerce_long(mid_raw)
        mid_vid = member_id_optional(mid_raw)
        aid = coerce_long(first_present(doc, "AgencyId", "agencyId"))
        ip_raw = first_present(doc, "ipAddress")
        ip = _sanitize_ip(ip_raw)
        ip_vid = f"ip:{ip}" if ip else None
        device = first_present(doc, "deviceId")
        device_vid = f"device:{device}" if device else None
        reason = doc.get("reason")
        stepup_events.append(
            [
                stepup_event_id(tracking_id),
                "StepUpEvent",
                tracking_id,
                doc.get("eventType"),
                mid,
                aid,
                doc.get("AgencyCountryCode"),
                ip,
                doc.get("ipCountryCode"),
                device,
                doc.get("cognitoDeviceId"),
                doc.get("username"),
                doc.get("isPrimaryAgent"),
                doc.get("isStepUpInitiated"),
                doc.get("result"),
                reason,
                doc.get("TransactionValue"),
                doc.get("TransactionCurrency"),
                doc.get("paymentId"),
                doc.get("BookingCreatedOn"),
                doc.get("BookingCheckInOn"),
                doc.get("BookingAmountLastHours"),
                coerce_long(doc.get("TotalBookingsLastHours")),
                coerce_long(doc.get("TotalBookingsLastDaysExcludingHours")),
                coerce_long(doc.get("FailedLoginCount")),
                doc.get("StepUpHistoryExists"),
                doc.get("LastStepUpCompletedTime"),
                _stepup_hotel_star_ratings(doc),
                doc.get("IsRefundableBooking"),
                doc.get("DestinationCountryCode"),
                doc.get("UserCreatedOn"),
                doc.get("timestamp"),
                doc.get("processedAt"),
            ]
        )
        if reason:
            stepup_rules[reason] = [stepup_rule_id(reason), "StepUpRule", reason, None, None, None]
        if mid_vid:
            edges_auth.append(
                [
                    f"OPS-A-{aseq:08d}",
                    mid_vid,
                    stepup_event_id(tracking_id),
                    "HAD_STEP_UP",
                    doc.get("timestamp"),
                    None,
                    None,
                    None,
                    aid,
                    None,
                    None,
                    doc.get("isStepUpInitiated"),
                    doc.get("TransactionValue"),
                    tracking_id,
                    None,
                ]
            )
            aseq += 1
        if device_vid:
            edges_auth.append(
                [
                    f"OPS-A-{aseq:08d}",
                    stepup_event_id(tracking_id),
                    device_vid,
                    "STEP_UP_USED_DEVICE",
                    doc.get("timestamp"),
                    None,
                    None,
                    None,
                    aid,
                    ip,
                    device,
                    None,
                    None,
                    None,
                    None,
                ]
            )
            aseq += 1
        if ip_vid:
            edges_auth.append(
                [
                    f"OPS-A-{aseq:08d}",
                    stepup_event_id(tracking_id),
                    ip_vid,
                    "STEP_UP_FROM_IP",
                    doc.get("timestamp"),
                    None,
                    None,
                    None,
                    aid,
                    ip,
                    device,
                    None,
                    None,
                    None,
                    None,
                ]
            )
            aseq += 1
        if reason:
            edges_auth.append(
                [
                    f"OPS-A-{aseq:08d}",
                    stepup_event_id(tracking_id),
                    stepup_rule_id(reason),
                    "TRIGGERED_RULE",
                    doc.get("timestamp"),
                    None,
                    None,
                    None,
                    aid,
                    None,
                    None,
                    None,
                    None,
                    None,
                    reason,
                ]
            )
            aseq += 1

    print(f"[stepup] ✓ Complete! Total: {doc_count:,} docs, {len(stepup_events):,} events, {len(stepup_rules):,} rules, {len(edges_auth):,} auth edges")
    
    return {
        "stepup_events": stepup_events,
        "stepup_rules": stepup_rules,
        "edges_auth": edges_auth,
    }


def _iso_now(days_ago: int = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_synthetic_stepup_bundle(auth_events: list[list[Any]], count: int) -> dict[str, Any]:
    """Generate synthetic step-up events seeded from real auth event data.

    Uses member/agency/device/ip data already captured from the login export
    so all edges remain consistent with the rest of the graph.

    IDs are prefixed with SYN- to make synthetic rows easy to identify.
    """
    if not auth_events:
        print("[stepup] WARNING: no auth events to seed from — synthetic step-up will be empty")
        return {"stepup_events": [], "stepup_rules": {}, "edges_auth": []}

    random.seed(int(os.getenv("SYNTHETIC_STEPUP_SEED", "42")))

    # Build seed index from auth events (header order matches _export_login_bundle rows)
    # Row layout: [id, label, authSessionId, eventType, challengeName, email,
    #              memberId, agencyId, ipAddress, ipCountryCode, deviceId,
    #              tbohDeviceId, isSuccessfulLogin, initAuthTime, respondToAuthTime,
    #              medium, ipThreatIntel, SuspiciousMail, UserCreatedOn, timestamp, processedAt]
    IDX_MID, IDX_AID, IDX_IP, IDX_IP_CC, IDX_DEV, IDX_TS = 6, 7, 8, 9, 10, 19
    seeds = [
        row for row in auth_events
        if len(row) > IDX_TS and row[IDX_MID]
    ]
    if not seeds:
        print("[stepup] WARNING: no usable auth event seeds — synthetic step-up will be empty")
        return {"stepup_events": [], "stepup_rules": {}, "edges_auth": []}

    stepup_events: list[list[Any]] = []
    stepup_rules: dict[str, list[Any]] = {}
    edges_auth: list[list[Any]] = []
    aseq = 1

    print(f"[stepup] Generating {count} synthetic step-up events seeded from {len(seeds)} auth events")

    for _ in range(count):
        seed = random.choice(seeds)
        tracking_id = f"SYN-{uuid.uuid4()}"
        reason = random.choice(_SYNTHETIC_STEPUP_RULES)
        ts = seed[IDX_TS] or _iso_now(random.randint(1, 30))
        device_id = seed[IDX_DEV] or str(uuid.uuid4())
        ip = seed[IDX_IP] or f"203.0.113.{random.randint(1, 254)}"
        ip_country = seed[IDX_IP_CC] or random.choice(["IN", "AE", "US", "GB"])
        member_raw = seed[IDX_MID]
        agency_raw = seed[IDX_AID]
        txn_value = round(random.uniform(500, 5000), 2)
        mid_vid = member_id_optional(member_raw)

        stepup_events.append([
            stepup_event_id(tracking_id),
            "StepUpEvent",
            tracking_id,
            "STEP_UP",
            member_raw,
            agency_raw,
            ip_country,
            ip,
            ip_country,
            device_id,
            device_id,
            None,                                        # username
            random.choice([True, False]),                # isPrimaryAgent
            True,                                        # isStepUpInitiated
            random.choice(["ALLOW", "CHALLENGE", "DENY"]),  # result
            reason,
            txn_value,
            "USD",
            f"SYN-PAY-{uuid.uuid4().hex[:12]}",         # paymentId
            ts,                                          # BookingCreatedOn
            _iso_now(0),                                 # BookingCheckInOn
            txn_value,                                   # BookingAmountLastHours
            random.randint(1, 5),                        # TotalBookingsLastHours
            random.randint(1, 10),                       # TotalBookingsLastDaysExcludingHours
            random.randint(0, 3),                        # FailedLoginCount
            random.choice([True, False]),                # StepUpHistoryExists
            ts,                                          # LastStepUpCompletedTime
            random.choice(["3", "4", "5"]),              # HotelStarRatings
            random.choice([True, False]),                # IsRefundableBooking
            ip_country,                                  # DestinationCountryCode
            _iso_now(90)[:10],                           # UserCreatedOn
            ts,                                          # timestamp
            ts,                                          # processedAt
        ])

        if reason not in stepup_rules:
            stepup_rules[reason] = [
                stepup_rule_id(reason),
                "StepUpRule",
                reason,
                f"Synthetic MVP rule: {reason}",
                random.randint(1, 5),
                random.choice([3600, 7200, 86400]),
            ]

        if mid_vid:
            edges_auth.append([
                f"OPS-A-{aseq:08d}",
                mid_vid,
                stepup_event_id(tracking_id),
                "HAD_STEP_UP",
                ts,
                None, None, None,
                agency_raw,
                None, None,
                True,
                txn_value,
                tracking_id,
                None,
            ])
            aseq += 1

        device_vid = f"device:{device_id}" if device_id else None
        ip_vid = f"ip:{ip}" if ip else None

        if device_vid:
            edges_auth.append([
                f"OPS-A-{aseq:08d}",
                stepup_event_id(tracking_id),
                device_vid,
                "STEP_UP_USED_DEVICE",
                ts,
                None, None, None,
                agency_raw,
                ip, device_id,
                None, None, None, None,
            ])
            aseq += 1

        if ip_vid:
            edges_auth.append([
                f"OPS-A-{aseq:08d}",
                stepup_event_id(tracking_id),
                ip_vid,
                "STEP_UP_FROM_IP",
                ts,
                None, None, None,
                agency_raw,
                ip, device_id,
                None, None, None, None,
            ])
            aseq += 1

        edges_auth.append([
            f"OPS-A-{aseq:08d}",
            stepup_event_id(tracking_id),
            stepup_rule_id(reason),
            "TRIGGERED_RULE",
            ts,
            None, None, None,
            agency_raw,
            None, None,
            None, None, None,
            reason,
        ])
        aseq += 1

    print(f"[stepup] ✓ Generated {len(stepup_events)} synthetic step-up events, "
          f"{len(stepup_rules)} rules, {len(edges_auth)} auth edges")
    return {
        "stepup_events": stepup_events,
        "stepup_rules": stepup_rules,
        "edges_auth": edges_auth,
    }


def export_opensearch(output_root: Path) -> None:
    is_local = _is_local()
    login_index = os.getenv("OPENSEARCH_LOGIN_INDEX", "login-history-*")
    hotel_index = os.getenv("OPENSEARCH_HOTEL_INDEX", "hotel-static-data")
    # Step-up is handled synthetically — real index is intentionally skipped.
    synthetic_stepup_count = int(os.getenv("SYNTHETIC_STEPUP_COUNT", str(_DEFAULT_SYNTHETIC_STEPUP_COUNT)))
    hotel_cap = _hotel_limit()
    login_query: dict[str, Any] = {"query": {"match_all": {}}}

    print("[startup] OpenSearch export")
    print(f"[startup] IS_LOCAL={is_local}")
    print(f"[startup] OPENSEARCH_ENDPOINT={os.getenv('OPENSEARCH_ENDPOINT')}")
    print(f"[startup] OPENSEARCH_AUTH_MODE={os.getenv('OPENSEARCH_AUTH_MODE', 'auto')}")
    print(f"[startup] OPENSEARCH_LOGIN_INDEX={login_index}  (full export)")
    print(f"[startup] OPENSEARCH_HOTEL_INDEX={hotel_index}  (limit={hotel_cap if hotel_cap else 'unlimited'})")
    print(f"[startup] Step-up mode: SYNTHETIC ({synthetic_stepup_count} events) — real index skipped")
    print(f"[startup] Output root={output_root}")
    print(f"[startup] EXPORT_WORKERS={worker_count()}")
    print(f"[startup] OPENSEARCH_SCROLL_SLICES={opensearch_scroll_slices()}")

    vertices_dir = output_root / "opensearch" / "vertices"
    edges_dir = output_root / "opensearch" / "edges"
    temp_dir = output_root / "opensearch" / "_tmp"

    # Clean stale files from previous runs to prevent old pre-chunking files
    # being picked up alongside newly written chunks by the combine step.
    import shutil
    for d in (vertices_dir, edges_dir, temp_dir):
        if d.exists():
            shutil.rmtree(d)
            print(f"[startup] Cleaned stale output dir: {d}")
    hotels_path = vertices_dir / "hotels.csv"

    # Login history (full) and hotels (capped) run in parallel against OpenSearch.
    # Step-up is generated synthetically after login so it can be seeded from
    # real member/device/IP data captured during the login export.
    exports = run_parallel(
        [
            ("login", lambda: _export_login_bundle(login_index, login_query)),
            (
                "hotels",
                lambda: _export_hotels_parallel(hotel_index, hotels_path, temp_dir),
            ),
        ],
        max_workers=2,
    )

    login = exports["login"]

    # Build synthetic step-up bundle seeded from real login auth events
    stepup = _build_synthetic_stepup_bundle(login["auth_events"], synthetic_stepup_count)

    edges_auth = login["edges_auth"] + stepup["edges_auth"]
    # login and stepup both mint OPS-A- IDs from independent aseq=1 counters,
    # so the two ID ranges collide. Edge IDs are never used as endpoints
    # (only vertex IDs are), so renumbering the merged list is safe.
    for i, row in enumerate(edges_auth, start=1):
        row[0] = f"OPS-A-{i:08d}"

    run_parallel(
        [
            (
                "devices.csv",
                lambda: write_csv(
                    vertices_dir / "devices.csv",
                    [
                        "~id",
                        "~label",
                        "deviceId:String",
                        "tbohDeviceId:String",
                        "deviceFingerprint:String",
                        "deviceType:String",
                        "os:String",
                        "browser:String",
                        "firstSeen:String",
                        "lastSeen:String",
                        "useCount:String",
                    ],
                    login["devices"].values(),
                ),
            ),
            (
                "ip_addresses.csv",
                lambda: write_csv(
                    vertices_dir / "ip_addresses.csv",
                    [
                        "~id",
                        "~label",
                        "ipAddress:String",
                        "ipCountryCode:String",
                        "isp:String",
                        "ipThreatIntel:String",
                        "firstSeen:String",
                        "lastSeen:String",
                        "loginCount:String",
                    ],
                    login["ips"].values(),
                ),
            ),
            (
                "auth_events.csv",
                lambda: write_csv(
                    vertices_dir / "auth_events.csv",
                    [
                        "~id",
                        "~label",
                        "authSessionId:String",
                        "eventType:String",
                        "challengeName:String",
                        "email:String",
                        "memberId:String",
                        "agencyId:String",
                        "ipAddress:String",
                        "ipCountryCode:String",
                        "deviceId:String",
                        "tbohDeviceId:String",
                        "isSuccessfulLogin:Bool",
                        "initAuthTime:String",
                        "respondToAuthTime:String",
                        "medium:String",
                        "ipThreatIntel:String",
                        "SuspiciousMail:String",
                        "UserCreatedOn:String",
                        "timestamp:String",
                        "processedAt:String",
                    ],
                    login["auth_events"],
                ),
            ),
            (
                "stepup_events.csv",
                lambda: write_csv(
                    vertices_dir / "stepup_events.csv",
                    STEPUP_VERTEX_HEADERS,
                    stepup["stepup_events"],
                ),
            ),
            (
                "stepup_rules.csv",
                lambda: write_csv(
                    vertices_dir / "stepup_rules.csv",
                    [
                        "~id",
                        "~label",
                        "ruleName:String",
                        "description:String",
                        "threshold:String",
                        "windowSeconds:String",
                    ],
                    stepup["stepup_rules"].values(),
                ),
            ),
            (
                "edges-fraud.csv",
                lambda: write_csv(
                    edges_dir / "edges-fraud.csv",
                    EDGE_HEADERS["fraud"],
                    login["edges_fraud"],
                ),
            ),
            (
                "edges-auth.csv",
                lambda: write_csv(
                    edges_dir / "edges-auth.csv",
                    EDGE_HEADERS["auth"],
                    edges_auth,
                ),
            ),
        ],
        max_workers=min(7, worker_count()),
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
    parser = argparse.ArgumentParser(description="Export OpenSearch data into Neptune CSV files.")
    parser.add_argument("--output-root", default="../data", help="Base output directory.")
    parser.add_argument(
        "--hotel-limit",
        type=int,
        default=None,
        help=(
            "Max hotel vertices to export for MVP (default: 500). "
            "Pass 0 to export all hotels."
        ),
    )
    parser.add_argument(
        "--synthetic-stepup-count",
        type=int,
        default=None,
        help=f"Number of synthetic step-up events to generate (default: {_DEFAULT_SYNTHETIC_STEPUP_COUNT}).",
    )
    args = parser.parse_args()

    # CLI args take precedence over env vars
    if args.hotel_limit is not None:
        os.environ["HOTEL_LIMIT"] = str(args.hotel_limit)
    if args.synthetic_stepup_count is not None:
        os.environ["SYNTHETIC_STEPUP_COUNT"] = str(args.synthetic_stepup_count)

    base = Path(__file__).resolve().parent
    storage = resolve_export_storage(
        args.output_root,
        base_dir=base,
        run_id=default_run_id(),
    )
    export_opensearch(storage.local_root)
    storage.sync_tree("opensearch")


if __name__ == "__main__":
    main()

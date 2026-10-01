#!/usr/bin/env python3
"""
Probe OpenSearch indices before running opensearch_export_job.py.

Lists candidate indices, counts documents, samples fields, and suggests .env values.

Usage:
  cd batchjobs/csv_export
  source .venv/bin/activate
  python opensearch_discover_indices.py

Loads .env from this directory (does not override existing shell env unless --env-override).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from common.coerce import first_present

# Reuse the same client/auth logic as the export job.
from opensearch_export_job import _client, _normalize_endpoint

KEYWORDS = ("login", "step", "hotel", "history", "static")

DEFAULT_CANDIDATES = [
    "login",
    "login-prep",
    "login-dev",
    "login-history-*",
    "step-up-dev",
    "stepup-events-*",
    "step-up-*",
    "hotels",
    "hotels2",
    "hotel-static-data",
    "hotel-static-data-*",
]

LOGIN_FIELDS = (
    "authSessionId",
    "memberId",
    "MemberId",
    "ipAddress",
    "deviceId",
    "timestamp",
)
STEPUP_FIELDS = ("TrackingId", "MemberId", "AgencyId", "reason", "timestamp")
HOTEL_FIELDS = (
    "HotelCode",
    "hotelCode",
    "hotelcode",
    "GeoCodes",
    "CountryCode",
    "countryCode",
    "StarRating",
    "starRating",
    "CityCodes",
    "cityCode",
)


def load_dotenv(env_path: Path, *, override: bool = False) -> bool:
    if not env_path.is_file():
        return False
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
        if not override and key in os.environ:
            continue
        os.environ[key] = value
    return True


def _count(client: Any, index: str) -> int | None:
    try:
        return int(client.count(index=index)["count"])
    except Exception:
        return None


def _count_error(client: Any, index: str) -> str:
    try:
        client.count(index=index)
        return ""
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def _sample(client: Any, index: str) -> dict[str, Any] | None:
    try:
        resp = client.search(index=index, body={"size": 1, "query": {"match_all": {}}})
        hits = resp.get("hits", {}).get("hits", [])
        if not hits:
            return None
        return hits[0].get("_source", {})
    except Exception:
        return None


def _present_fields(doc: dict[str, Any], expected: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for name in expected:
        if first_present(doc, name) is not None:
            found.append(name)
    return found


def _classify_index(doc: dict[str, Any] | None) -> str:
    if not doc:
        return "unknown"
    login_hits = _present_fields(doc, LOGIN_FIELDS)
    stepup_hits = _present_fields(doc, STEPUP_FIELDS)
    hotel_hits = _present_fields(doc, HOTEL_FIELDS)
    has_auth_session = first_present(doc, "authSessionId") is not None
    has_hotel_code = first_present(doc, "HotelCode", "hotelCode", "hotelcode") is not None
    if has_auth_session or len(login_hits) >= 3:
        return "login"
    if first_present(doc, "TrackingId") is not None or len(stepup_hits) >= 3:
        return "stepup"
    if has_hotel_code or len(hotel_hits) >= 2:
        return "hotel"
    return "unknown"


def _unique_ordered(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def discover() -> int:
    endpoint = os.getenv("OPENSEARCH_ENDPOINT", "")
    if not endpoint:
        print("ERROR: Set OPENSEARCH_ENDPOINT in .env or the environment.", file=sys.stderr)
        return 1

    print(f"[config] OPENSEARCH_ENDPOINT={_normalize_endpoint(endpoint)}")
    print(f"[config] OPENSEARCH_AUTH_MODE={os.getenv('OPENSEARCH_AUTH_MODE', 'auto')}")
    print(f"[config] OPENSEARCH_VERIFY_CERTS={os.getenv('OPENSEARCH_VERIFY_CERTS', 'true')}")

    try:
        client = _client()
    except Exception as exc:
        print(f"ERROR: Could not create OpenSearch client: {exc}", file=sys.stderr)
        return 1

    auth_mode = getattr(client, "_synapse_auth_mode", "unknown")
    print(f"[connected] auth={auth_mode}")

    try:
        all_indices = sorted(x["index"] for x in client.cat.indices(format="json"))
    except Exception as exc:
        print(f"ERROR: Could not list indices: {exc}", file=sys.stderr)
        return 1

    print("\n=== Candidate indices (name matches login/step/hotel/history/static) ===")
    candidates_on_cluster = [name for name in all_indices if any(k in name.lower() for k in KEYWORDS)]
    if candidates_on_cluster:
        for name in candidates_on_cluster:
            print(f"  {name}")
    else:
        print("  (none — check endpoint / permissions)")

    env_candidates = [
        os.getenv("OPENSEARCH_LOGIN_INDEX", ""),
        os.getenv("OPENSEARCH_STEPUP_INDEX", ""),
        os.getenv("OPENSEARCH_HOTEL_INDEX", ""),
    ]
    probe_list = _unique_ordered(
        [c for c in DEFAULT_CANDIDATES + env_candidates if c.strip()]
    )

    print("\n=== Count probe ===")
    ok_indices: list[tuple[str, int, str]] = []
    for name in probe_list:
        count = _count(client, name)
        if count is None:
            err = _count_error(client, name)
            print(f"  {name}: FAIL — {err}")
        else:
            print(f"  {name}: {count:,} docs")
            if count > 0:
                doc = _sample(client, name)
                kind = _classify_index(doc)
                ok_indices.append((name, count, kind))

    if not ok_indices:
        print("\nNo indices with documents found. Fix endpoint, auth, or index names.")
        return 1

    print("\n=== Sample documents (indices with count > 0) ===")
    by_kind: dict[str, list[tuple[str, int]]] = {"login": [], "stepup": [], "hotel": [], "unknown": []}
    for name, count, kind in ok_indices:
        doc = _sample(client, name)
        keys = sorted(doc.keys())[:25] if doc else []
        print(f"\n--- {name} ({count:,} docs) — detected: {kind} ---")
        if doc:
            print(f"  sample fields ({len(doc)} total): {keys}{' ...' if len(doc) > 25 else ''}")
            if kind == "login":
                print(f"  login markers: {_present_fields(doc, LOGIN_FIELDS)}")
            elif kind == "stepup":
                print(f"  stepup markers: {_present_fields(doc, STEPUP_FIELDS)}")
            elif kind == "hotel":
                print(f"  hotel markers: {_present_fields(doc, HOTEL_FIELDS)}")
                code = first_present(doc, "hotelCode", "HotelCode", "hotelcode")
                print(f"  hotelCode sample: {code!r}")
        else:
            print("  (could not fetch sample)")
        by_kind.setdefault(kind, []).append((name, count))

    def _best(kind: str) -> str | None:
        items = by_kind.get(kind, [])
        if not items:
            return None
        return max(items, key=lambda x: x[1])[0]

    login_pick = _best("login")
    stepup_pick = _best("stepup")
    hotel_pick = _best("hotel")

    print("\n=== Suggested .env (highest doc count per detected type) ===")
    if login_pick:
        print(f"OPENSEARCH_LOGIN_INDEX={login_pick}")
    else:
        print("# OPENSEARCH_LOGIN_INDEX=<no login-like index detected>")
    if stepup_pick:
        print(f"OPENSEARCH_STEPUP_INDEX={stepup_pick}")
    else:
        print("# OPENSEARCH_STEPUP_INDEX=<no step-up-like index detected>")
    if hotel_pick:
        print(f"OPENSEARCH_HOTEL_INDEX={hotel_pick}")
    else:
        print("# OPENSEARCH_HOTEL_INDEX=<no hotel-like index detected>")

    if by_kind.get("unknown"):
        print("\n=== Indices with docs but unknown shape (review manually) ===")
        for name, count in by_kind["unknown"]:
            print(f"  {name}: {count:,}")

    missing = [k for k, pick in (("login", login_pick), ("stepup", stepup_pick), ("hotel", hotel_pick)) if not pick]
    if missing:
        print(f"\nWARNING: Could not auto-detect: {', '.join(missing)}")
        print("Pick names from the candidate list above and set them in .env before export.")
        return 1

    print("\nOK: All three index types detected. Update .env, then run:")
    print("  python opensearch_export_job.py --output-root ../data")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover OpenSearch index names for the Neptune CSV export.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Env file to load from csv_export directory (default: .env).",
    )
    parser.add_argument(
        "--env-override",
        action="store_true",
        help="Let .env override variables already set in the shell.",
    )
    args = parser.parse_args()

    env_path = Path(args.env_file)
    if not env_path.is_absolute():
        env_path = Path(__file__).resolve().parent / env_path

    if load_dotenv(env_path, override=args.env_override):
        print(f"[config] Loaded {env_path}")
    else:
        print(f"[config] No env file at {env_path} (using shell environment)")

    raise SystemExit(discover())


if __name__ == "__main__":
    main()

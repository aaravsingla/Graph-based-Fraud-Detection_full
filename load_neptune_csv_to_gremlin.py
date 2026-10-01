#!/usr/bin/env python3
"""
Load Neptune-format CSV exports into a local Gremlin Server (TinkerGraph) or Neptune.

Usage:
    cd batchjobs/csv_export
    source .venv/bin/activate
    pip install gremlinpython

    # Dev subset — SQL core only
    python load_neptune_csv_to_gremlin.py \\
        --data-dir ../data/final \\
        --limit 1000 \\
        --files vertices/sql-members.csv,vertices/sql-agencies.csv,edges/sql-edges-core.csv

    # Dry-run (print sample Gremlin, no server needed)
    python load_neptune_csv_to_gremlin.py --data-dir ../data/final --limit 2 --dry-run \\
        --files vertices/sql-members.csv

    # Full manifest order (vertices then edges)
    python load_neptune_csv_to_gremlin.py --data-dir ../data/final --limit 100

Load order: vertices before edges. Default file order follows load-manifest.txt.
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
import time

logging.getLogger("gremlinpython").setLevel(logging.WARNING)
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

try:
    from gremlin_python.driver import client as gremlin_client
except ImportError:
    gremlin_client = None  # type: ignore[assignment]


GREMLIN_TYPE_MAP: dict[str, str] = {
    "String": "String",
    "Long": "Long",
    "Int": "Int",
    "Bool": "Bool",
    "Boolean": "Bool",
    "Double": "Double",
    "Float": "Float",
    "Date": "String",
    "Datetime": "String",
    "DateTime": "String",
}

SYSTEM_VERTEX_COLS = {"~id", "~label"}
SYSTEM_EDGE_COLS = {"~id", "~from", "~to", "~label"}


@dataclass
class ColumnSpec:
    header: str
    name: str
    gremlin_type: str


@dataclass
class CsvMeta:
    kind: Literal["vertex", "edge"]
    id_col: str = "~id"
    label_col: str = "~label"
    from_col: str = "~from"
    to_col: str = "~to"
    properties: list[ColumnSpec] = field(default_factory=list)


@dataclass
class LoadStats:
    loaded: int = 0
    skipped: int = 0
    errors: int = 0


def escape_groovy_string(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )


def parse_neptune_header(fieldnames: list[str]) -> CsvMeta:
    is_edge = any(name.lower() in ("~from", "~to") for name in fieldnames)
    meta = CsvMeta(kind="edge" if is_edge else "vertex")

    for col in fieldnames:
        col_lower = col.lower()
        if col_lower == "~id":
            meta.id_col = col
        elif col_lower == "~label":
            meta.label_col = col
        elif col_lower == "~from":
            meta.from_col = col
        elif col_lower == "~to":
            meta.to_col = col
        elif col_lower in SYSTEM_VERTEX_COLS or col_lower in SYSTEM_EDGE_COLS:
            continue
        else:
            match = re.match(r"^(.+):(\w+)$", col)
            if match:
                prop_name, prop_type = match.groups()
                gremlin_type = GREMLIN_TYPE_MAP.get(prop_type, "String")
            else:
                prop_name, gremlin_type = col, "String"
            meta.properties.append(
                ColumnSpec(header=col, name=prop_name, gremlin_type=gremlin_type)
            )

    return meta


def format_gremlin_value(raw: str, gremlin_type: str) -> str | None:
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None

    if gremlin_type == "String":
        return f"'{escape_groovy_string(value)}'"
    if gremlin_type == "Long":
        try:
            return f"{int(float(value))}L"
        except ValueError:
            return None
    if gremlin_type == "Int":
        try:
            return str(int(float(value)))
        except ValueError:
            return None
    if gremlin_type == "Double":
        try:
            return f"{float(value)}d"
        except ValueError:
            return None
    if gremlin_type == "Bool":
        return "true" if value.lower() in ("true", "1", "yes") else "false"
    return f"'{escape_groovy_string(value)}'"


def build_property_chain(row: dict[str, str], meta: CsvMeta) -> str:
    parts: list[str] = []
    for spec in meta.properties:
        raw = row.get(spec.header, "")
        formatted = format_gremlin_value(raw, spec.gremlin_type)
        if formatted is None:
            continue
        parts.append(f".property('{escape_groovy_string(spec.name)}', {formatted})")
    return "".join(parts)


def build_vertex_query(row: dict[str, str], meta: CsvMeta) -> str | None:
    vertex_id = (row.get(meta.id_col) or "").strip()
    label = (row.get(meta.label_col) or "").strip()
    if not vertex_id or not label:
        return None
    props = build_property_chain(row, meta)
    return (
        f"g.addV('{escape_groovy_string(label)}')"
        f".property(T.id, '{escape_groovy_string(vertex_id)}')"
        f"{props}"
    )


def build_edge_query(row: dict[str, str], meta: CsvMeta) -> str | None:
    edge_id = (row.get(meta.id_col) or "").strip()
    label = (row.get(meta.label_col) or "").strip()
    from_id = (row.get(meta.from_col) or "").strip()
    to_id = (row.get(meta.to_col) or "").strip()
    if not edge_id or not label or not from_id or not to_id:
        return None
    props = build_property_chain(row, meta)
    return (
        f"g.V('{escape_groovy_string(from_id)}')"
        f".addE('{escape_groovy_string(label)}')"
        f".to(__.V('{escape_groovy_string(to_id)}'))"
        f".property(T.id, '{escape_groovy_string(edge_id)}')"
        f"{props}"
    )


def read_manifest_files(data_dir: Path) -> list[Path]:
    manifest = data_dir / "load-manifest.txt"
    if not manifest.is_file():
        return []
    files: list[Path] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        files.append(data_dir / line)
    return files


def resolve_files(
    data_dir: Path,
    files_arg: str | None,
    vertices_only: bool,
    edges_only: bool,
) -> list[Path]:
    if files_arg:
        paths = [data_dir / part.strip() for part in files_arg.split(",") if part.strip()]
    else:
        paths = read_manifest_files(data_dir)
        if not paths:
            paths = sorted((data_dir / "vertices").glob("*.csv"))
            paths.extend(sorted((data_dir / "edges").glob("*.csv")))

    if vertices_only:
        paths = [p for p in paths if "/vertices/" in p.as_posix() or p.parent.name == "vertices"]
    if edges_only:
        paths = [p for p in paths if "/edges/" in p.as_posix() or p.parent.name == "edges"]

    missing = [p for p in paths if not p.is_file()]
    if missing:
        for path in missing:
            print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(1)
    return paths


def iter_rows(path: Path, limit: int | None) -> tuple[CsvMeta, list[dict[str, str]]]:
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        meta = parse_neptune_header(fieldnames)
        for index, row in enumerate(reader):
            if limit is not None and index >= limit:
                break
            rows.append(row)
    return meta, rows


def submit_batch(
    gremlin: Any,
    queries: list[str],
    *,
    dry_run: bool,
    sample: int = 3,
) -> tuple[int, int]:
    if not queries:
        return 0, 0
    if dry_run:
        for query in queries[:sample]:
            print(f"    {query}")
        if len(queries) > sample:
            print(f"    ... ({len(queries) - sample} more)")
        return len(queries), 0

    loaded = 0
    errors = 0
    for query in queries:
        try:
            gremlin.submit(query).all().result()
            loaded += 1
        except Exception as row_error:
            errors += 1
            message = str(row_error).split("\n")[0]
            print(f"    ERROR: {message}")
            print(f"    query: {query[:200]}...")
    return loaded, errors


def load_file(
    gremlin: Any,
    path: Path,
    *,
    batch_size: int,
    limit: int | None,
    dry_run: bool,
) -> LoadStats:
    stats = LoadStats()
    meta, rows = iter_rows(path, limit)
    print(f"  {len(rows):,} rows ({meta.kind}), batch size {batch_size}")

    batch: list[str] = []
    for row in rows:
        if meta.kind == "vertex":
            query = build_vertex_query(row, meta)
        else:
            query = build_edge_query(row, meta)
        if query is None:
            stats.skipped += 1
            continue
        batch.append(query)
        if len(batch) >= batch_size:
            loaded, errors = submit_batch(gremlin, batch, dry_run=dry_run)
            stats.loaded += loaded
            stats.errors += errors
            batch = []

    if batch:
        loaded, errors = submit_batch(gremlin, batch, dry_run=dry_run)
        stats.loaded += loaded
        stats.errors += errors

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load Neptune CSV exports into Gremlin Server / Neptune"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "final",
        help="Root directory containing vertices/, edges/, load-manifest.txt",
    )
    parser.add_argument(
        "--files",
        type=str,
        default=None,
        help="Comma-separated paths relative to --data-dir (overrides manifest)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max rows per file (dev subset)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Gremlin statements per submission (default: 50)",
    )
    parser.add_argument(
        "--gremlin-url",
        type=str,
        default="ws://localhost:8182/gremlin",
        help="Gremlin WebSocket URL (default: ws://localhost:8182/gremlin)",
    )
    parser.add_argument(
        "--vertices-only",
        action="store_true",
        help="Load only vertex files",
    )
    parser.add_argument(
        "--edges-only",
        action="store_true",
        help="Load only edge files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print sample queries without connecting to Gremlin",
    )
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    if not data_dir.is_dir():
        print(f"ERROR: data directory not found: {data_dir}", file=sys.stderr)
        sys.exit(1)

    files = resolve_files(data_dir, args.files, args.vertices_only, args.edges_only)
    vertex_files = [p for p in files if p.parent.name == "vertices"]
    edge_files = [p for p in files if p.parent.name == "edges"]
    ordered = vertex_files + edge_files

    print(f"Data dir: {data_dir}")
    print(f"Files: {len(ordered)} ({len(vertex_files)} vertices, {len(edge_files)} edges)")
    if args.limit:
        print(f"Limit: {args.limit} rows per file")

    gremlin: Any = None
    if not args.dry_run:
        if gremlin_client is None:
            print("ERROR: gremlinpython not installed. Run: pip install gremlinpython", file=sys.stderr)
            sys.exit(1)
        print(f"Connecting to {args.gremlin_url} ...")
        try:
            gremlin = gremlin_client.Client(args.gremlin_url, "g")
            count = gremlin.submit("g.V().count()").all().result()
            print(f"Connected. Current vertex count: {count[0] if count else 0}")
        except Exception as exc:
            print(f"ERROR: Could not connect to Gremlin server: {exc}", file=sys.stderr)
            sys.exit(1)

    totals = LoadStats()
    started = time.monotonic()

    for path in ordered:
        print(f"\nProcessing {path.relative_to(data_dir)}")
        file_stats = load_file(
            gremlin,
            path,
            batch_size=args.batch_size,
            limit=args.limit,
            dry_run=args.dry_run,
        )
        totals.loaded += file_stats.loaded
        totals.skipped += file_stats.skipped
        totals.errors += file_stats.errors
        print(
            f"  Done: loaded={file_stats.loaded:,} skipped={file_stats.skipped:,} "
            f"errors={file_stats.errors:,}"
        )

    if gremlin is not None:
        try:
            v_count = gremlin.submit("g.V().count()").all().result()
            e_count = gremlin.submit("g.E().count()").all().result()
            print(f"\nGraph now: {v_count[0] if v_count else 0} vertices, {e_count[0] if e_count else 0} edges")
        finally:
            gremlin.close()

    elapsed = time.monotonic() - started
    print(
        f"\nSummary: loaded={totals.loaded:,} skipped={totals.skipped:,} "
        f"errors={totals.errors:,} elapsed={elapsed:.1f}s"
    )
    if totals.errors:
        sys.exit(1)


if __name__ == "__main__":
    main()

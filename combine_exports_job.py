from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path

from common.csv_writer import ensure_dir
from common.storage import (
    ExportStorage,
    _s3_write_client,
    _upload_directory,
    default_run_id,
    materialize_input_root,
    resolve_export_storage,
    write_manifest_s3_paths,
)


def _upload_manifest_above(manifest_local: Path, storage: ExportStorage, rel_str: str) -> None:
    """Upload load-manifest.txt to the parent of the neptune-load data prefix.

    This keeps it out of the directory Neptune scans for CSV files, while
    still making it available for use with a manifest-based loader call.

    S3 layout after upload:
      s3://<bucket>/<s3_prefix>/load-manifest.txt        ← manifest here
      s3://<bucket>/<s3_prefix>/<rel_str>/vertices/*.csv ← data here
      s3://<bucket>/<s3_prefix>/<rel_str>/edges/*.csv
    """
    if not storage.use_s3 or not manifest_local.is_file():
        return
    client = _s3_write_client()
    # Place manifest at the storage root, not inside rel_str
    key = f"{storage.s3_prefix.strip('/')}/load-manifest.txt"
    client.upload_file(str(manifest_local), storage.s3_bucket, key)
    from common.storage import s3_uri
    print(f"[combine] Manifest uploaded to s3://{storage.s3_bucket}/{key}")


def _drop_dangling_edges(out_vertices: Path, out_edges: Path) -> None:
    """Drop edges whose ~from or ~to endpoint has no corresponding vertex.

    The combine step is the first and only point where all sources sit
    together, so cross-source referential integrity is enforced here. Edge
    ~id values are never referenced as endpoints (only vertex IDs are), so
    dropping an orphaned edge cannot break any other reference. Every edge
    removed is one Neptune would have rejected at load time anyway.
    """
    valid_vertex_ids: set[str] = set()
    for vfile in out_vertices.glob("*.csv"):
        with vfile.open(encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            next(reader, None)  # skip header
            for row in reader:
                if row:
                    valid_vertex_ids.add(row[0])  # ~id is column 0

    for efile in sorted(out_edges.glob("*.csv")):
        kept: list[list[str]] = []
        dropped = 0
        with efile.open(encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            for row in reader:
                if len(row) >= 3 and row[1] in valid_vertex_ids and row[2] in valid_vertex_ids:
                    kept.append(row)
                else:
                    dropped += 1
        if dropped:
            with efile.open("w", encoding="utf-8", newline="\n") as f:
                writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
                if header is not None:
                    writer.writerow(header)
                writer.writerows(kept)
            print(f"[combine] {efile.name}: dropped {dropped} dangling edges")


def combine(input_roots: list[Path], output_root: Path, *, staging_dir: Path | None = None) -> None:
    staging = staging_dir or (output_root.parent / "_combine_staging")
    resolved_roots: list[Path] = []
    for root in input_roots:
        if str(root).startswith("s3://"):
            resolved_roots.append(materialize_input_root(root, staging_dir=staging))
        else:
            resolved_roots.append(root)

    out_vertices = output_root / "vertices"
    out_edges = output_root / "edges"
    # Clean previous combined output so stale files don't carry over
    if out_vertices.exists():
        shutil.rmtree(out_vertices)
    if out_edges.exists():
        shutil.rmtree(out_edges)
    ensure_dir(out_vertices)
    ensure_dir(out_edges)

    for source_root in resolved_roots:
        vertices = source_root / "vertices"
        edges = source_root / "edges"
        if vertices.exists():
            for file in sorted(vertices.glob("*.csv")):
                dest = out_vertices / f"{source_root.name}-{file.name}"
                shutil.copy2(file, dest)
        if edges.exists():
            for file in sorted(edges.glob("*.csv")):
                dest = out_edges / f"{source_root.name}-{file.name}"
                shutil.copy2(file, dest)

    _drop_dangling_edges(out_vertices, out_edges)

    manifest = output_root / "load-manifest.txt"
    with manifest.open("w", encoding="utf-8") as f:
        for file in sorted(out_vertices.glob("*.csv")):
            f.write(f"vertices/{file.name}\n")
        for file in sorted(out_edges.glob("*.csv")):
            f.write(f"edges/{file.name}\n")


def publish_output(output_root: Path, storage: ExportStorage) -> None:
    if not storage.use_s3:
        return
    try:
        relative = output_root.relative_to(storage.local_root)
        rel_str = relative.as_posix()
    except ValueError:
        rel_str = output_root.name

    # Write the manifest with full S3 URIs one level ABOVE the data directory
    # so Neptune's directory-based scan (source: s3://.../neptune-load/) does
    # not pick it up as a CSV data file.
    manifest_local = output_root / "load-manifest.txt"
    manifest_s3_key_prefix = storage.s3_prefix.rstrip("/")  # e.g. synapse/neptune-load parent
    write_manifest_s3_paths(
        manifest_local,
        bucket=storage.s3_bucket,
        prefix=f"{manifest_s3_key_prefix}/{rel_str}",
    )

    # Upload only the vertices/ and edges/ subdirectories — NOT the manifest
    for subdir in ("vertices", "edges"):
        subpath = output_root / subdir
        if subpath.exists():
            storage.sync_tree(f"{rel_str}/{subdir}")

    # Upload the manifest separately, one level above the data prefix
    _upload_manifest_above(manifest_local, storage, rel_str)


def main() -> None:
    parser = argparse.ArgumentParser(description="Combine independent source CSV exports for Neptune load.")
    parser.add_argument(
        "--input-roots",
        nargs="+",
        required=True,
        help="One or more source output roots (e.g. batchjobs/output/sql ...)",
    )
    parser.add_argument("--output-root", required=True, help="Combined output folder (e.g. .../data/final).")
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    output_root = Path(args.output_root)
    if not output_root.is_absolute():
        output_root = (base / output_root).resolve()

    storage = resolve_export_storage(output_root.parent, base_dir=base, run_id=default_run_id())
    combine([Path(p) for p in args.input_roots], output_root)
    publish_output(output_root, storage)


if __name__ == "__main__":
    main()

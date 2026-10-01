#!/usr/bin/env python3
"""
Run all Neptune CSV export flows in one go:
  1. SQL export
  2. DynamoDB export
  3. OpenSearch export
  4. Combine into batchjobs/data/final/

Writes export-summary.json / export-summary.txt and optionally emails via SendGrid
when TRIGGER_MAIL_ON_COMPLETION=true (or triggermailoncompletion=true).

Loads batchjobs/csv_export/.env automatically (does not override variables
already set in the shell).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from common.concurrency import sources_parallel, run_parallel, worker_count
from common.mail import send_completion_email
from common.report import (
    StepResult,
    build_export_report,
    format_report_text,
    utc_now,
    write_report_files,
)
from common.storage import default_run_id, resolve_export_storage, write_manifest_s3_paths

REQUIREMENTS_FILE = "requirements.txt"
IMPORT_CHECKS = ("pyodbc", "boto3", "opensearchpy")


def resolve_python(base: Path) -> Path:
    venv_python = base / ".venv" / "bin" / "python"
    if venv_python.is_file():
        return venv_python
    return Path(sys.executable)


def ensure_venv(base: Path) -> Path:
    venv_python = base / ".venv" / "bin" / "python"
    if venv_python.is_file():
        return venv_python
    print(f"[setup] Creating virtual environment at {base / '.venv'}")
    subprocess.run([sys.executable, "-m", "venv", str(base / ".venv")], check=True)
    return venv_python


def _import_check(python: Path, module: str) -> tuple[bool, str]:
    result = subprocess.run(
        [str(python), "-c", f"import {module}"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True, ""
    return False, (result.stderr or result.stdout or "").strip()


def missing_modules(python: Path) -> list[str]:
    missing: list[str] = []
    for module in IMPORT_CHECKS:
        ok, _ = _import_check(python, module)
        if not ok:
            missing.append(module)
    return missing


def _pyodbc_runtime_hint(stderr: str) -> str:
    if "libodbc" in stderr.lower() or "unixodbc" in stderr.lower():
        return (
            "\nmacOS: install the ODBC runtime, then re-run:\n"
            "  brew install unixodbc\n"
            "For SQL Server connectivity also install Microsoft's ODBC Driver 18."
        )
    return ""


def install_requirements(python: Path, requirements: Path) -> None:
    print(f"[setup] Installing dependencies from {requirements}")
    subprocess.run(
        [str(python), "-m", "pip", "install", "--upgrade", "pip"],
        check=True,
    )
    subprocess.run(
        [str(python), "-m", "pip", "install", "-r", str(requirements)],
        check=True,
    )


def verify_imports(python: Path) -> None:
    for module in IMPORT_CHECKS:
        ok, err = _import_check(python, module)
        if ok:
            continue
        message = f"Cannot import {module}."
        if module == "pyodbc":
            message += _pyodbc_runtime_hint(err)
        elif err:
            message += f"\n{err}"
        raise SystemExit(message)


def prepare_python(base: Path, *, create_venv: bool, install_deps: bool) -> Path:
    python = ensure_venv(base) if create_venv else resolve_python(base)
    requirements = base / REQUIREMENTS_FILE
    if install_deps and missing_modules(python):
        if not requirements.is_file():
            raise SystemExit(f"Missing {REQUIREMENTS_FILE} in {base}")
        install_requirements(python, requirements)
    verify_imports(python)
    return python


def reexec_if_needed(python: Path) -> None:
    if Path(sys.executable).resolve() == python.resolve():
        return
    print(f"[setup] Re-running with {python}")
    os.execv(str(python), [str(python), *sys.argv])


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


def _has_sql_config() -> bool:
    if os.getenv("SQL_CONN_STR") or os.getenv("SQL_DSN"):
        return True
    required = ("SQL_HOST", "SQL_DATABASE", "SQL_USERNAME", "SQL_PASSWORD")
    return all(os.getenv(k) for k in required)


def _has_dynamo_config() -> bool:
    return bool(os.getenv("DYNAMO_CUSTOMER360_TABLE"))  # RolePermissions360 is optional


def _has_opensearch_config() -> bool:
    return bool(os.getenv("OPENSEARCH_ENDPOINT"))


def _validate_steps(
    *,
    run_sql: bool,
    run_dynamo: bool,
    run_opensearch: bool,
) -> None:
    missing: list[str] = []
    if run_sql and not _has_sql_config():
        missing.append("SQL (set SQL_CONN_STR, SQL_DSN, or SQL_HOST/SQL_DATABASE/SQL_USERNAME/SQL_PASSWORD)")
    if run_dynamo and not _has_dynamo_config():
        missing.append("DynamoDB (set DYNAMO_CUSTOMER360_TABLE; DYNAMO_ROLE_PERMISSIONS_TABLE is optional)")
    if run_opensearch and not _has_opensearch_config():
        missing.append("OpenSearch (set OPENSEARCH_ENDPOINT)")
    if missing:
        raise SystemExit("Missing required environment variables:\n  - " + "\n  - ".join(missing))


def run_step(name: str, cmd: list[str]) -> StepResult:
    print(f"\n{'=' * 60}")
    print(f"STEP: {name}")
    print(f"{'=' * 60}")
    print("Running:", " ".join(cmd))
    started = time.perf_counter()
    try:
        subprocess.run(cmd, check=True)
        elapsed = time.perf_counter() - started
        print(f"Done: {name} ({elapsed:.1f}s)")
        return StepResult(name=name, status="SUCCESS", seconds=elapsed)
    except subprocess.CalledProcessError as exc:
        elapsed = time.perf_counter() - started
        message = f"exit code {exc.returncode}"
        print(f"Failed: {name} ({elapsed:.1f}s) — {message}")
        return StepResult(name=name, status="FAILED", seconds=elapsed, error=message)


def _finalize_report(
    *,
    run_id: str,
    output_root: Path,
    storage,
    steps: list[StepResult],
    started_at: datetime,
    error: str | None,
) -> None:
    finished_at = utc_now()
    report = build_export_report(
        run_id=run_id,
        output_root=output_root,
        steps=steps,
        started_at=started_at,
        finished_at=finished_at,
        s3_destination=storage.destination_uri,
        error=error,
    )
    text = format_report_text(report)
    write_report_files(report, output_root)

    print(f"\n{'=' * 60}")
    print("EXPORT SUMMARY")
    print(f"{'=' * 60}")
    print(text)

    try:
        send_completion_email(report, text_body=text)
    except Exception as mail_error:
        print(f"[mail] Warning: could not send email: {mail_error}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run SQL, Dynamo, OpenSearch exports and combine outputs into one Neptune load folder.",
    )
    parser.add_argument(
        "--output-root",
        default="../data",
        help="Shared output root (default: batchjobs/data).",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Env file to load from csv_export directory (default: .env).",
    )
    parser.add_argument("--skip-sql", action="store_true", help="Skip SQL export.")
    parser.add_argument("--skip-dynamo", action="store_true", help="Skip DynamoDB export.")
    parser.add_argument("--skip-opensearch", action="store_true", help="Skip OpenSearch export.")
    parser.add_argument("--skip-combine", action="store_true", help="Skip combine step.")
    parser.add_argument(
        "--env-override",
        action="store_true",
        help="Let .env file override variables already set in the shell.",
    )
    parser.add_argument(
        "--no-venv",
        action="store_true",
        help="Do not create/use batchjobs/csv_export/.venv (use current python).",
    )
    parser.add_argument(
        "--no-install-deps",
        action="store_true",
        help="Skip automatic pip install of requirements.txt.",
    )
    args = parser.parse_args()

    base = Path(__file__).resolve().parent
    python = prepare_python(
        base,
        create_venv=not args.no_venv,
        install_deps=not args.no_install_deps,
    )
    reexec_if_needed(python)
    env_path = Path(args.env_file)
    if not env_path.is_absolute():
        env_path = base / env_path

    if load_dotenv(env_path, override=args.env_override):
        print(f"[config] Loaded environment from {env_path}")
    else:
        print(f"[config] No env file at {env_path} (using shell environment only)")

    run_id = default_run_id()
    os.environ["EXPORT_RUN_ID"] = run_id
    os.environ["EXPORT_S3_SYNC_EACH_STEP"] = "false"
    storage = resolve_export_storage(
        args.output_root,
        base_dir=base,
        run_id=run_id,
    )
    output_root = storage.local_root

    run_sql = not args.skip_sql
    run_dynamo = not args.skip_dynamo
    run_opensearch = not args.skip_opensearch
    run_combine = not args.skip_combine

    _validate_steps(run_sql=run_sql, run_dynamo=run_dynamo, run_opensearch=run_opensearch)

    print("[config] Output root:", output_root)
    if storage.use_s3:
        print("[config] S3 destination:", storage.destination_uri)
    print(f"[config] IS_LOCAL={os.getenv('IS_LOCAL', 'false')}")
    print(f"[config] EXPORT_WORKERS={worker_count()}")
    print(f"[config] EXPORT_SOURCES_PARALLEL={sources_parallel()}")
    print(f"[config] TRIGGER_MAIL_ON_COMPLETION={os.getenv('TRIGGER_MAIL_ON_COMPLETION', os.getenv('triggermailoncompletion', 'false'))}")

    py = str(python)
    export_jobs: list[tuple[str, list[str]]] = []
    if run_sql:
        export_jobs.append(
            ("SQL export", [py, str(base / "sql_export_job.py"), "--output-root", str(output_root)])
        )
    if run_dynamo:
        export_jobs.append(
            (
                "DynamoDB export",
                [py, str(base / "dynamo_export_job.py"), "--output-root", str(output_root)],
            )
        )
    if run_opensearch:
        export_jobs.append(
            (
                "OpenSearch export",
                [py, str(base / "opensearch_export_job.py"), "--output-root", str(output_root)],
            )
        )

    started_at = utc_now()
    steps: list[StepResult] = []
    pipeline_error: str | None = None

    try:
        if sources_parallel() and len(export_jobs) > 1:
            print("[config] Running source exports in parallel")
            parallel_results = run_parallel(
                [
                    (name, (lambda n=name, cmd=command: run_step(n, cmd)))
                    for name, command in export_jobs
                ],
                max_workers=len(export_jobs),
            )
            steps.extend(parallel_results.values())
        else:
            for name, command in export_jobs:
                steps.append(run_step(name, command))

        failed = [step for step in steps if step.status == "FAILED"]
        if failed:
            raise RuntimeError(
                "Export step(s) failed: " + ", ".join(f"{s.name} ({s.error})" for s in failed)
            )

        if run_combine:
            input_roots = []
            for name in ("sql", "dynamo", "opensearch"):
                root = output_root / name
                if root.exists() and (
                    (root / "vertices").exists() or (root / "edges").exists()
                ):
                    input_roots.append(str(root))
            if not input_roots:
                raise SystemExit("Nothing to combine: no export folders found under output root.")
            combine_step = run_step(
                "Combine exports",
                [
                    py,
                    str(base / "combine_exports_job.py"),
                    "--input-roots",
                    *input_roots,
                    "--output-root",
                    str(output_root / "final"),
                ],
            )
            steps.append(combine_step)
            if combine_step.status == "FAILED":
                raise RuntimeError(f"Combine failed: {combine_step.error}")

        if storage.use_s3:
            manifest = output_root / "final" / "load-manifest.txt"
            write_manifest_s3_paths(
                manifest,
                bucket=storage.s3_bucket,
                prefix=f"{storage.s3_prefix.rstrip('/')}/final",
            )
            storage.sync_all()

    except Exception as exc:
        pipeline_error = str(exc)
        raise
    finally:
        _finalize_report(
            run_id=run_id,
            output_root=output_root,
            storage=storage,
            steps=steps,
            started_at=started_at,
            error=pipeline_error,
        )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit(str(exc)) from exc

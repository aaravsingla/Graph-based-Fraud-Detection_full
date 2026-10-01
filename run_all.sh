#!/usr/bin/env bash
# Synapse Neptune CSV export — one-shot local runner.
# Creates venv, installs deps, loads .env, runs all export + combine steps.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"
REQUIREMENTS="$SCRIPT_DIR/requirements.txt"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env}"

echo "==> Synapse CSV batch export"
echo "    Directory: $SCRIPT_DIR"

# --- Python venv ---
if [[ ! -x "$VENV_PY" ]]; then
  echo "==> Creating virtual environment at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

echo "==> Upgrading pip"
"$VENV_PY" -m pip install --upgrade pip -q

echo "==> Installing Python dependencies"
"$VENV_PY" -m pip install -r "$REQUIREMENTS" -q

# --- SQL Server ODBC driver (optional preflight) ---
check_sql_driver() {
  if [[ "${1:-}" == *"--skip-sql"* ]]; then
    return 0
  fi
  if odbcinst -q -d 2>/dev/null | grep -qi "ODBC Driver 18 for SQL Server"; then
    return 0
  fi
  echo "WARN: 'ODBC Driver 18 for SQL Server' not registered."
  echo "      SQL export will fail until installed:"
  echo "        HOMEBREW_ACCEPT_EULA=Y brew install msodbcsql18"
  echo "      (requires up-to-date Xcode Command Line Tools)"
  echo "      Or run without SQL: ./run_all.sh --skip-sql"
  echo ""
}

check_sql_driver "$*"

# --- macOS ODBC runtime (pyodbc) ---
if ! "$VENV_PY" -c "import pyodbc" 2>/dev/null; then
  if [[ "$(uname -s)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
    if ! brew --prefix unixodbc >/dev/null 2>&1; then
      echo "==> Installing unixODBC (required by pyodbc on macOS)"
      brew install unixodbc
    fi
  fi
  if ! "$VENV_PY" -c "import pyodbc" 2>/dev/null; then
    echo "ERROR: pyodbc cannot load. On macOS run: brew install unixodbc"
    echo "       For SQL Server also install Microsoft ODBC Driver 18."
    exit 1
  fi
fi

# --- Run orchestrator (.env loaded inside Python — safe for values with spaces) ---
echo "==> Starting exports"
if [[ -f "$ENV_FILE" ]]; then
  echo "    Env file: $ENV_FILE (loaded by run_all_exports.py)"
else
  echo "    No env file at $ENV_FILE (using shell environment)"
fi
exec "$VENV_PY" "$SCRIPT_DIR/run_all_exports.py" --no-venv --env-file "$ENV_FILE" "$@"

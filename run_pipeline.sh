#!/bin/zsh
set -e

# Load AWS credentials and config from .env
ENV_FILE="$(dirname "$0")/batchjobs/csv_export/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "[error] .env file not found at: $ENV_FILE"
  exit 1
fi

export AWS_ACCESS_KEY_ID=$(grep -E '^AWS_ACCESS_KEY_ID=' "$ENV_FILE" | cut -d '=' -f2-)
export AWS_SECRET_ACCESS_KEY=$(grep -E '^AWS_SECRET_ACCESS_KEY=' "$ENV_FILE" | cut -d '=' -f2-)
export AWS_SESSION_TOKEN=$(grep -E '^AWS_SESSION_TOKEN=' "$ENV_FILE" | cut -d '=' -f2-)
export AWS_REGION=$(grep -E '^AWS_REGION=' "$ENV_FILE" | cut -d '=' -f2-)

echo "[pipeline] AWS credentials loaded from .env (key: ${AWS_ACCESS_KEY_ID:0:8}...)"

echo "[pipeline] Step 1: Running export jobs..."
cd /Users/aarav/Desktop/Synapse/batchjobs/csv_export
python3 run_all_exports.py --env-override 2>&1 | tee /tmp/export_run.log
echo "[pipeline] Export step done."

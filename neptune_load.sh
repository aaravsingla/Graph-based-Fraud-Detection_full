#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# neptune_load.sh — Upload fixed manifest to S3 then trigger Neptune bulk load
#
# Usage:
#   ./neptune_load.sh              # uses defaults from .env
#   ./neptune_load.sh --check      # poll status of the most recent load job
#
# Required env vars (set in .env or export before running):
#   NEPTUNE_ENDPOINT   — Neptune cluster endpoint (host only, no https://)
#   NEPTUNE_REGION     — AWS region, e.g. eu-west-1
#   S3_BUCKET          — S3 bucket name, e.g. dev-login-graph-data
#   S3_NEPTUNE_PREFIX  — S3 prefix where the final/ folder is uploaded,
#                        e.g. synapse/neptune-load
#   NEPTUNE_IAM_ROLE   — IAM role ARN Neptune uses to read from S3
#
# Optional:
#   FAIL_ON_ERROR      — TRUE | FALSE (default FALSE)
#   PARALLELISM        — LOW | MEDIUM | HIGH | OVERSUBSCRIBE (default HIGH)
#   QUEUE_REQUEST      — TRUE | FALSE (default TRUE)
# ---------------------------------------------------------------------------

set -euo pipefail

# ── Load .env if present (safe parser — no Bash variable expansion) ──────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    # Strip leading/trailing whitespace
    line="${raw_line#"${raw_line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    # Skip blank lines and comments
    [[ -z "$line" || "$line" == \#* ]] && continue
    # Strip leading 'export ' if present
    [[ "$line" == export\ * ]] && line="${line#export }"
    # Must contain '='
    [[ "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    # Strip surrounding quotes (single or double)
    if [[ "$value" == \"*\" ]] || [[ "$value" == \'*\' ]]; then
      value="${value:1:${#value}-2}"
    fi
    # Export without letting Bash interpret the value
    export "$key=$value"
  done < "$ENV_FILE"
fi

# ── Defaults ────────────────────────────────────────────────────────────────
NEPTUNE_ENDPOINT="${NEPTUNE_ENDPOINT:-neptune-dev-cluster.cluster-ck1qxluvekbm.eu-west-1.neptune.amazonaws.com}"
NEPTUNE_REGION="${NEPTUNE_REGION:-eu-west-1}"
S3_BUCKET="${S3_BUCKET:-dev-login-graph-data}"
S3_NEPTUNE_PREFIX="${S3_NEPTUNE_PREFIX:-synapse/neptune-load}"
NEPTUNE_IAM_ROLE="${NEPTUNE_IAM_ROLE:-arn:aws:iam::696292622193:role/TTL-custom-rdstos3Policy}"
FAIL_ON_ERROR="${FAIL_ON_ERROR:-FALSE}"
PARALLELISM="${PARALLELISM:-HIGH}"
QUEUE_REQUEST="${QUEUE_REQUEST:-TRUE}"

MANIFEST_LOCAL="$SCRIPT_DIR/../data/final/load-manifest.txt"
MANIFEST_S3_KEY="${S3_NEPTUNE_PREFIX}/load-manifest.txt"
NEPTUNE_URL="https://${NEPTUNE_ENDPOINT}:8182/loader"

# ── Helpers ─────────────────────────────────────────────────────────────────
require() {
  command -v "$1" >/dev/null 2>&1 || { echo "[error] '$1' not found — please install it"; exit 1; }
}

# ── Check mode: poll status of a load job ───────────────────────────────────
if [[ "${1:-}" == "--check" ]]; then
  LOAD_ID="${2:-}"
  if [[ -z "$LOAD_ID" ]]; then
    echo "Usage: $0 --check <loadId>"
    exit 1
  fi
  require awscurl
  echo "[check] Polling load status for $LOAD_ID ..."
  awscurl -X GET \
    --service neptune-db \
    --region "$NEPTUNE_REGION" \
    "${NEPTUNE_URL}/${LOAD_ID}"
  exit 0
fi

# ── Pre-flight checks ────────────────────────────────────────────────────────
require awscurl
require aws

if [[ ! -f "$MANIFEST_LOCAL" ]]; then
  echo "[error] Manifest not found: $MANIFEST_LOCAL"
  echo "        Run combine_exports_job.py first."
  exit 1
fi

# Reject manifests that still contain comment lines (would cause PARSING_ERROR)
if grep -qE '^\s*#' "$MANIFEST_LOCAL"; then
  echo "[error] Manifest contains comment lines — Neptune cannot parse these."
  echo "        Fix: remove all lines starting with '#' from $MANIFEST_LOCAL"
  exit 1
fi

echo "[manifest] Local path : $MANIFEST_LOCAL"
echo "[manifest] Uploading to: s3://${S3_BUCKET}/${MANIFEST_S3_KEY}"

# ── Upload manifest to S3 ────────────────────────────────────────────────────
aws s3 cp "$MANIFEST_LOCAL" "s3://${S3_BUCKET}/${MANIFEST_S3_KEY}" \
  --region "$NEPTUNE_REGION"

echo "[manifest] Upload complete."

# ── Trigger Neptune bulk load ─────────────────────────────────────────────────
SOURCE_URI="s3://${S3_BUCKET}/${S3_NEPTUNE_PREFIX}/load-manifest.txt"

echo ""
echo "[neptune] Submitting bulk load..."
echo "[neptune] Source  : $SOURCE_URI"
echo "[neptune] Endpoint: $NEPTUNE_URL"
echo "[neptune] Role    : $NEPTUNE_IAM_ROLE"
echo ""

RESPONSE=$(awscurl -X POST \
  -H 'Content-Type: application/json' \
  --service neptune-db \
  --region "$NEPTUNE_REGION" \
  "$NEPTUNE_URL" \
  -d "{
    \"source\": \"${SOURCE_URI}\",
    \"format\": \"csv\",
    \"iamRoleArn\": \"${NEPTUNE_IAM_ROLE}\",
    \"region\": \"${NEPTUNE_REGION}\",
    \"failOnError\": \"${FAIL_ON_ERROR}\",
    \"parallelism\": \"${PARALLELISM}\",
    \"updateSingleCardinalityProperties\": \"FALSE\",
    \"queueRequest\": \"${QUEUE_REQUEST}\"
  }")

echo "$RESPONSE"

# ── Extract and display the loadId for follow-up polling ─────────────────────
LOAD_ID=$(echo "$RESPONSE" | python3 -c \
  "import sys, json; d=json.load(sys.stdin); print(d.get('payload',{}).get('loadId',''))" 2>/dev/null || true)

if [[ -n "$LOAD_ID" ]]; then
  echo ""
  echo "[neptune] Load submitted. loadId = $LOAD_ID"
  echo ""
  echo "  Poll status with:"
  echo "    $0 --check $LOAD_ID"
  echo ""
  echo "  Or directly:"
  echo "    awscurl -X GET --service neptune-db --region $NEPTUNE_REGION ${NEPTUNE_URL}/${LOAD_ID}"
else
  echo ""
  echo "[neptune] Could not extract loadId from response — check output above."
fi

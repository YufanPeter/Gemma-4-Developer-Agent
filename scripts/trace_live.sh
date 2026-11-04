#!/usr/bin/env bash
# Start the trace platform and a cloudflared quick tunnel to its ingest port, then print
# the TRACE_SINK_URL / TRACE_SINK_TOKEN to put into the Kaggle notebook (Secrets or run config).
#
#   scripts/trace_live.sh            # UI http://127.0.0.1:8790, ingest :8791 tunnelled
#   TRACE_TOKEN=... scripts/trace_live.sh   # reuse a token
#
# Needs: python3, cloudflared (brew install cloudflared). Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v cloudflared >/dev/null || { echo "cloudflared not found: brew install cloudflared" >&2; exit 1; }

TOKEN="${TRACE_TOKEN:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')}"
LOG_DIR=trace_platform/data
mkdir -p "$LOG_DIR"

python3 -m trace_platform.server --token "$TOKEN" >"$LOG_DIR/server.log" 2>&1 &
SERVER=$!
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8791 >"$LOG_DIR/tunnel.log" 2>&1 &
TUNNEL=$!
trap 'kill $SERVER $TUNNEL 2>/dev/null' EXIT INT TERM

URL=""
for _ in $(seq 1 60); do
  URL=$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_DIR/tunnel.log" | head -1 || true)
  [ -n "$URL" ] && break
  sleep 1
done
[ -n "$URL" ] || { echo "tunnel did not come up; see $LOG_DIR/tunnel.log" >&2; exit 1; }

# Wait until the public URL actually reaches the ingest port.
for _ in $(seq 1 30); do
  curl -fsS "$URL/health" >/dev/null 2>&1 && break
  sleep 2
done

cat <<EOF

  Trace platform UI : http://127.0.0.1:8790/
  TRACE_SINK_URL    : $URL
  TRACE_SINK_TOKEN  : $TOKEN

  Put both into the dev-eval notebook (Kaggle Secrets TRACE_SINK_URL / TRACE_SINK_TOKEN,
  or the run-config cell), turn Internet ON, and run. The URL changes every time this
  script starts. Logs: $LOG_DIR/server.log, $LOG_DIR/tunnel.log. Ctrl-C to stop.

EOF
wait $SERVER

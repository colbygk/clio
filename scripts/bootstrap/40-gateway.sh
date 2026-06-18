#!/usr/bin/env bash
# 40-gateway.sh — install the LiteLLM gateway on the HEAD node only. Run as root.
set -euo pipefail
cd "$(dirname "$0")"
. ../lib/common.sh
need_root
. "$CLUSTER_DIR/inventory.env"
HOST=$(hostname -s)

if [ "$HOST" != "${HEAD:-clio}" ]; then
  log "Not the head node — skipping gateway"
  exit 0
fi

# Create the secrets env if missing (gitignored). User fills in the real Anthropic key.
GENV="$CLUSTER_DIR/gateway/gateway.env"
if [ ! -f "$GENV" ]; then
  log "Creating $GENV (EDIT to add your real ANTHROPIC_API_KEY for spillover)"
  KEY="sk-clio-$(head -c16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  cat > "$GENV" <<EOF
# Gateway secrets — DO NOT COMMIT. Token Claude Code/aider present to the gateway:
LITELLM_MASTER_KEY=$KEY
# Your real Anthropic key, used for spillover to Claude (fill this in):
ANTHROPIC_API_KEY=
EOF
  chmod 600 "$GENV"
  chown "${SUDO_USER:-colby}":"${SUDO_USER:-colby}" "$GENV"
fi

log "Pulling LiteLLM image"
docker pull ghcr.io/berriai/litellm:main-stable

log "Installing + enabling gateway service"
install -m 644 "$CLUSTER_DIR/systemd/clio-gateway.service" /etc/systemd/system/clio-gateway.service
systemctl daemon-reload
systemctl enable --now clio-gateway.service
sleep 5
curl -fsS "http://localhost:${GATEWAY_PORT:-4000}/health/liveliness" >/dev/null 2>&1 \
  && log "Gateway up on :${GATEWAY_PORT:-4000}" \
  || warn "Gateway not healthy yet — check: journalctl -u clio-gateway"
echo
log "Your gateway master key (for ceres / Claude Code):"
grep LITELLM_MASTER_KEY "$GENV"

#!/usr/bin/env bash
# 45-nginx.sh — put ComfyUI behind nginx with HTTPS + mutual TLS, and close port 8188.
#
# Expects three files staged in /tmp by `clusterctl nginx deploy` (which gets them from clio-pki
# on ceres — the CA *private* key never comes near a node):
#     /tmp/clio-pki/ca.crt      the cluster CA cert, used to verify CLIENT certificates
#     /tmp/clio-pki/server.crt  this node's HTTPS certificate
#     /tmp/clio-pki/server.key  its private key
#
# Packaging note: nginx is installed with `apt-get install -d` (download only) followed by an
# explicit `dpkg -i`. A plain `apt-get install` would also try to configure the 8 half-installed
# DOCA/OFED DKMS packages on these nodes, which cannot build against kernel 6.17, and would abort.
set -euo pipefail
cd "$(dirname "$0")"
. ../lib/common.sh
need_root
. "$CLUSTER_DIR/inventory.env"

HOST=$(hostname -s)
FQDN="$HOST.${CLUSTER_DOMAIN:-cgk.sh}"
PORT=${COMFYUI_PORT:-8188}
STAGE=/tmp/clio-pki
PKI=/etc/clio-pki

for f in ca.crt server.crt server.key; do
  [ -f "$STAGE/$f" ] || die "missing $STAGE/$f — run 'clusterctl nginx deploy $HOST' from ceres"
done

# ---------- 1. nginx ----------
if ! dpkg-query -W -f='${db:Status-Abbrev}' nginx 2>/dev/null | grep -q '^ii'; then
  log "Installing nginx (download-only + dpkg -i, to dodge apt's broken-DKMS cascade)"
  apt-get install -y -d nginx-light >/dev/null
  # Name the packages explicitly — /var/cache/apt/archives holds unrelated .debs too.
  dpkg -i /var/cache/apt/archives/nginx-common_*.deb \
          /var/cache/apt/archives/libnginx-mod-http-echo_*.deb \
          /var/cache/apt/archives/nginx_*.deb \
          /var/cache/apt/archives/nginx-light_*.deb
else
  log "nginx already installed"
fi

# ---------- 2. certificates ----------
log "Installing certificates to $PKI"
install -d -m 755 "$PKI"
install -m 644 "$STAGE/ca.crt"     "$PKI/ca.crt"
install -m 644 "$STAGE/server.crt" "$PKI/server.crt"
install -m 600 "$STAGE/server.key" "$PKI/server.key"
rm -rf "$STAGE"

# Fail loudly now rather than serving a cert that does not match its key.
a=$(openssl x509 -noout -modulus -in "$PKI/server.crt" | openssl md5)
b=$(openssl rsa  -noout -modulus -in "$PKI/server.key" | openssl md5)
[ "$a" = "$b" ] || die "server.crt and server.key do not match"

# ---------- 3. site config ----------
log "Writing nginx config for $FQDN -> 127.0.0.1:$PORT"
# `map` must live in the http context, not a server block.
cat > /etc/nginx/conf.d/10-websocket-upgrade.conf <<'EOF'
# ComfyUI upgrades to WebSocket for progress/preview. Echo the Connection header back correctly:
# a bare `Connection: upgrade` on ordinary requests confuses keepalive, so map it.
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

# Log whether a client certificate was offered and what happened to it. Without this, a 400 is
# ambiguous — "browser sent nothing" and "browser sent something we rejected" look identical, and
# nginx only says which at `info` level, far too noisy to leave on. $ssl_client_verify is one of
# NONE (none offered), SUCCESS, or FAILED:<reason>.
log_format mtls '$remote_addr verify=$ssl_client_verify dn="$ssl_client_s_dn" '
                '[$time_local] "$request" $status $body_bytes_sent "$http_user_agent"';
EOF

# Error pages, styled to match https://cgk.sh. The shared CSS is injected into each page at the
# <!--CSS--> marker so the served file is self-contained — a stylesheet fetched as a second request
# would need a client certificate of its own, leaving the "you are not allowed" page unstyled.
log "Installing styled error pages"
install -d -m 755 /usr/share/nginx/clio-errors
for page in denied upstream error; do
  src="$CLUSTER_DIR/nginx/errors/$page.html"
  [ -f "$src" ] || { warn "missing $src — skipping"; continue; }
  # Inject CSS, fill placeholders, then strip HTML comments — the source comments explain nginx
  # internals (which error codes map here, and the stock text being replaced) and there is no
  # reason to hand that to an unauthenticated caller staring at the denied page.
  awk -v cssfile="$CLUSTER_DIR/nginx/errors/_base.css" '
    /<!--CSS-->/ { while ((getline line < cssfile) > 0) print line; next }
    { print }' "$src" \
    | sed -e "s|__FQDN__|$FQDN|g" -e "s|__HOST__|$HOST|g" \
    | python3 -c 'import re,sys; sys.stdout.write(re.sub(r"<!--.*?-->", "", sys.stdin.read(), flags=re.S))' \
    > "/usr/share/nginx/clio-errors/_$page.html"
  chmod 644 "/usr/share/nginx/clio-errors/_$page.html"
done

sed -e "s|__FQDN__|$FQDN|g" -e "s|__HOST__|$HOST|g" -e "s|__PORT__|$PORT|g" \
    "$CLUSTER_DIR/nginx/comfyui.conf.tmpl" > /etc/nginx/sites-available/clio-comfyui
ln -sf /etc/nginx/sites-available/clio-comfyui /etc/nginx/sites-enabled/clio-comfyui
# The stock default site also claims default_server on :80 and would collide.
rm -f /etc/nginx/sites-enabled/default

nginx -t || die "nginx config test failed"
systemctl enable nginx >/dev/null 2>&1 || true
systemctl reload nginx 2>/dev/null || systemctl restart nginx
log "nginx active: $(systemctl is-active nginx)"

# ---------- 4. close 8188 to the network ----------
# Docker publishes on 0.0.0.0 by default, which is what exposes ComfyUI to the LAN. Rebinding the
# published port to loopback is the real fix; it needs the container recreated to take effect.
COMPOSE=$CLUSTER_DIR/comfyui-docker/compose-dgx_spark.yaml
if grep -qE '^\s*-\s*"?8188:8188' "$COMPOSE"; then
  log "Rebinding ComfyUI's published port to 127.0.0.1 in $(basename "$COMPOSE")"
  sed -i -E 's|^(\s*)-\s*"?8188:8188"?|\1- 127.0.0.1:8188:8188|' "$COMPOSE"
  RESTART_NEEDED=1
else
  log "ComfyUI port already bound to loopback"
  RESTART_NEEDED=0
fi

if [ "$RESTART_NEEDED" = 1 ]; then
  if curl -fsS -m 5 "http://127.0.0.1:$PORT/queue" 2>/dev/null | grep -q '"queue_running": \[\[' ; then
    warn "A job is RUNNING on this node — not restarting ComfyUI."
    warn "Port $PORT stays open to the LAN until you run: sudo systemctl restart clio-comfyui"
  elif systemctl is-active --quiet clio-comfyui; then
    log "Restarting clio-comfyui to apply the loopback binding"
    systemctl restart clio-comfyui
  else
    log "clio-comfyui not running; the new binding applies at next start"
  fi
fi

log "45-nginx done. https://$FQDN/ (client certificate required)"

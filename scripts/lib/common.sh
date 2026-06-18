# scripts/lib/common.sh — shared helpers for node-side bootstrap scripts.
# Sourced, not executed. Bootstrap scripts run AS root (via passwordless sudo) on the node.
set -euo pipefail

log()  { printf '\033[1;36m[%s]\033[0m %s\n' "$(hostname -s)" "$*" >&2; }
warn() { printf '\033[1;33m[%s] WARN:\033[0m %s\n' "$(hostname -s)" "$*" >&2; }
die()  { printf '\033[1;31m[%s] ERROR:\033[0m %s\n' "$(hostname -s)" "$*" >&2; exit 1; }

# Run idempotently: only act if a check fails.
need_root() { [ "$(id -u)" -eq 0 ] || die "must run as root (use sudo)"; }

# Directory layout on every node.
DATA_DIR=${DATA_DIR:-/data}
MODELS_DIR=${MODELS_DIR:-$DATA_DIR/models}
COMFY_DIR=${COMFY_DIR:-$DATA_DIR/comfyui}
CLUSTER_DIR=${CLUSTER_DIR:-/opt/clio}    # where this repo's runtime files live on the node

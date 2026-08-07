#!/usr/bin/env bash
# install.sh — set up clusterctl on this machine (ceres). Safe to re-run.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BINDIR="${BINDIR:-$HOME/.local/bin}"
mkdir -p "$BINDIR" "$HOME/.clio"
ln -sf "$REPO_ROOT/bin/clusterctl" "$BINDIR/clusterctl"
ln -sf "$REPO_ROOT/bin/clio-pki"  "$BINDIR/clio-pki"
chmod +x "$REPO_ROOT/bin/clusterctl" "$REPO_ROOT/bin/clio-pki"

echo "Installed clusterctl -> $BINDIR/clusterctl"
echo "Installed clio-pki   -> $BINDIR/clio-pki"
case ":$PATH:" in
  *":$BINDIR:"*) : ;;
  *) echo "NOTE: add $BINDIR to your PATH, e.g.:  echo 'export PATH=\"$BINDIR:\$PATH\"' >> ~/.zshrc" ;;
esac
echo
echo "Next:"
echo "  clusterctl bootstrap          # one-time: configure both Sparks (prompts for sudo pw once)"
echo "  clusterctl serve local-coder  # start the daily coding model on both nodes"
echo "  clusterctl use   local-coder  # point Claude Code here; then: source ~/.clio/claude-code.env"

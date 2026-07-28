#!/usr/bin/env bash
set -euo pipefail

# Self-update mechanism for qilab_web_control (mirrors qilab's own
# scripts/update_release.sh: versioned release dirs, a `current` symlink,
# atomic swap, automatic rollback if the restart doesn't come back up).
#
# Usage:
#   sudo ./scripts/self_update.sh <version> <wheel_path>
# Example:
#   sudo ./scripts/self_update.sh 0.3.0 /tmp/qilab_web_control-0.3.0-py3-none-any.whl
#
# IMPORTANT: invoke this via `systemd-run --unit=... --collect`, not a plain
# detached subprocess. This script calls `systemctl restart` on the very
# service it's usually launched from; a plain child process sharing that
# service's cgroup would be killed by that same restart (systemd's default
# KillMode=control-group) before it can verify success or roll back. A
# systemd-run transient unit has its own cgroup and survives. See README.md
# "Self-update".

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <version> <wheel_path>"
  echo "Example: $0 0.3.0 /tmp/qilab_web_control-0.3.0-py3-none-any.whl"
  exit 1
fi

VERSION="${1#v}"
WHEEL_PATH="$2"

SERVICE_NAME="qilab-web-control.service"
INSTALL_ROOT="/opt/qilab-web-control"
RELEASES_DIR="$INSTALL_ROOT/releases"
CURRENT_LINK="$INSTALL_ROOT/current"
TARGET_DIR="$RELEASES_DIR/$VERSION"
TARGET_VENV="$TARGET_DIR/venv"
LOG_DIR="/var/log/qilab-web-control"
LOG_FILE="$LOG_DIR/self_update.log"

if [[ $EUID -ne 0 ]]; then
  echo "This script must be run as root (use sudo)."
  exit 1
fi

if [[ ! -f "$WHEEL_PATH" ]]; then
  echo "Wheel not found: $WHEEL_PATH"
  exit 1
fi

mkdir -p "$RELEASES_DIR" "$LOG_DIR"
# Mirror stdout/stderr into a persistent log file so the web UI (which may be
# mid-restart for part of this run) can tail progress and the final result.
exec > >(tee -a "$LOG_FILE") 2>&1

log() {
  echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $*"
}

log "Starting self-update to version $VERSION"

PREV_TARGET=""
if [[ -L "$CURRENT_LINK" || -e "$CURRENT_LINK" ]]; then
  PREV_TARGET="$(readlink -f "$CURRENT_LINK" || true)"
fi

log "Building venv: $TARGET_VENV"
rm -rf "$TARGET_DIR"
mkdir -p "$TARGET_DIR"
python3 -m venv "$TARGET_VENV"
"$TARGET_VENV/bin/pip" install --upgrade pip
"$TARGET_VENV/bin/pip" install "$WHEEL_PATH"

log "Health check: importing app.main in the new venv"
if ! "$TARGET_VENV/bin/python" -c "import app.main"; then
  log "Update aborted: new venv fails to import app.main. Current install left untouched."
  exit 1
fi

log "Switching active symlink to: $TARGET_DIR"
# As in update_release.sh: if a previous/manual install left $CURRENT_LINK as
# a real directory (not a symlink), `ln -sfn` would silently create the link
# *inside* it and never move the active path. Force a fresh symlink.
if [[ -e "$CURRENT_LINK" && ! -L "$CURRENT_LINK" ]]; then
  log "Warning: $CURRENT_LINK is not a symlink; replacing it so the update can take effect."
  rm -rf "$CURRENT_LINK"
fi
ln -sfn "$TARGET_DIR" "$CURRENT_LINK"

log "Restarting service: $SERVICE_NAME"
systemctl restart "$SERVICE_NAME" || true

# `systemctl restart` returning success only means systemd queued/ran the
# restart job, not that the new process stayed up (Type=simple). Poll
# is-active for a few seconds before deciding this update actually worked.
ATTEMPTS=5
while [[ $ATTEMPTS -gt 0 ]] && ! systemctl is-active --quiet "$SERVICE_NAME"; do
  sleep 1
  ATTEMPTS=$((ATTEMPTS - 1))
done

if systemctl is-active --quiet "$SERVICE_NAME"; then
  log "Update successful: $VERSION"
  exit 0
fi

log "Service failed to become active after update. Rolling back..."
if [[ -n "$PREV_TARGET" && -d "$PREV_TARGET" ]]; then
  ln -sfn "$PREV_TARGET" "$CURRENT_LINK"
  systemctl restart "$SERVICE_NAME" || true
  log "Rollback attempted to: $PREV_TARGET"
else
  log "No valid previous target found. Manual recovery required."
fi

exit 1

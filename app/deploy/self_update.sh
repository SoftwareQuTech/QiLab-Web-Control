#!/usr/bin/env bash
set -euo pipefail

# Self-update mechanism for qilab_web_control (mirrors qilab's own
# scripts/update_release.sh: versioned release dirs, a `current` symlink,
# atomic swap, automatic rollback if the restart doesn't come back up).
#
# Usage:
#   sudo ./self_update.sh <version> <wheel_path>
# Example:
#   sudo ./self_update.sh 0.4.2 /tmp/qilab_web_control-0.4.2-py3-none-any.whl
#
# Layout it maintains (see README.md "Deployment layout"):
#   /opt/qilab-web-control/current -> releases/<version>
#   /opt/qilab-web-control/releases/<version>/site-packages/
#
# Two deployment constraints shape this script:
#
#   * No virtualenvs. `python3 -m venv` needs the python3-venv package, which is
#     not available on the legacy RT images, so a release is a plain
#     `pip install --target` directory that the unit puts on PYTHONPATH. pip is
#     the only requirement. (--target also sidesteps PEP 668 /
#     EXTERNALLY-MANAGED, so no --break-system-packages is needed.)
#
#   * No network assumed. The device may not reach PyPI, so a new release is
#     seeded by copying the live release's site-packages and installing the
#     wheel with --no-deps. A networked full install is attempted only if that
#     produces something that doesn't import — i.e. dependencies changed.
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
  echo "Example: $0 0.4.2 /tmp/qilab_web_control-0.4.2-py3-none-any.whl"
  exit 1
fi

VERSION="${1#v}"
WHEEL_PATH="$2"

SERVICE_NAME="${QILAB_WEB_CONTROL_SERVICE:-qilab-web-control.service}"
INSTALL_ROOT="${QILAB_WEB_CONTROL_ROOT:-/opt/qilab-web-control}"
RELEASES_DIR="$INSTALL_ROOT/releases"
CURRENT_LINK="$INSTALL_ROOT/current"
SCRIPT_INSTALL_PATH="$INSTALL_ROOT/scripts/self_update.sh"
UNIT_INSTALL_PATH="/etc/systemd/system/$SERVICE_NAME"
LOG_DIR="${QILAB_WEB_CONTROL_LOG_DIR:-/var/log/qilab-web-control}"
LOG_FILE="$LOG_DIR/self_update.log"

if [[ $EUID -ne 0 ]]; then
  echo "This script must be run as root (use sudo)."
  exit 1
fi

if [[ ! -f "$WHEEL_PATH" ]]; then
  echo "Wheel not found: $WHEEL_PATH"
  exit 1
fi

# pip parses a wheel's filename per PEP 427 (name-version-python-abi-platform.whl)
# and rejects anything else before reading a byte of it, so a caller that renamed
# the upload — added a unique prefix or suffix, say — fails deep inside pip with a
# confusing error. Catch it here instead. Uniqueness belongs on a containing
# directory, which is what the /self-update route does.
# A wheel filename has exactly 5 dash-separated components, or 6 with an
# optional build tag; the distribution name itself may not contain a dash (it is
# normalised to '_'), so counting them is an exact check rather than a heuristic.
WHEEL_NAME="$(basename "$WHEEL_PATH")"
IFS='-' read -ra WHEEL_PARTS <<<"${WHEEL_NAME%.whl}"
if [[ "$WHEEL_NAME" != *.whl || ${#WHEEL_PARTS[@]} -lt 5 || ${#WHEEL_PARTS[@]} -gt 6 ]]; then
  echo "Not a PEP 427 wheel filename: $WHEEL_NAME"
  echo "Expected <name>-<version>[-<build>]-<python>-<abi>-<platform>.whl"
  exit 1
fi

mkdir -p "$RELEASES_DIR" "$LOG_DIR"
# Mirror stdout/stderr into a persistent log file so the web UI (which may be
# mid-restart for part of this run) can tail progress and the final result.
exec > >(tee -a "$LOG_FILE") 2>&1

log() {
  echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $*"
}

# --- Preflight -------------------------------------------------------------
#
# Refuse to touch anything unless this install is actually swappable. Without
# this check, a deployment that installed the wheel into the system interpreter
# (nothing referencing `current`) would build a new release, flip a symlink no
# one reads, restart successfully and log "Update successful" while still
# running the old code — a silent no-op, the worst possible outcome.

unit_routes_through_current() {
  local shown files file
  shown="$(systemctl show -p ExecStart -p Environment "$SERVICE_NAME" 2>/dev/null || true)"
  if grep -qF "$CURRENT_LINK/" <<<"$shown"; then
    return 0
  fi

  # PYTHONPATH may come from an EnvironmentFile= rather than an inline
  # Environment=; `systemctl show -p Environment` reports only the inline ones.
  files="$(systemctl show -p EnvironmentFiles --value "$SERVICE_NAME" 2>/dev/null || true)"
  while read -r file; do
    file="${file%% *}"   # "/etc/default/x (ignore_errors=yes)" -> "/etc/default/x"
    file="${file#-}"
    [[ -n "$file" && -f "$file" ]] || continue
    if grep -qF "$CURRENT_LINK/" "$file"; then
      return 0
    fi
  done <<<"$files"

  return 1
}

preflight() {
  if ! command -v python3 >/dev/null 2>&1; then
    log "Preflight failed: python3 not found."
    return 1
  fi

  if ! python3 -m pip --version >/dev/null 2>&1; then
    log "Preflight failed: 'python3 -m pip' is unavailable, so no release can be installed."
    log "  Install the python3-pip package on this device."
    return 1
  fi

  if [[ -e "$CURRENT_LINK" && ! -L "$CURRENT_LINK" ]]; then
    log "Preflight failed: $CURRENT_LINK is a real directory, not a symlink."
    log "  The atomic swap this update relies on is impossible."
    log "  See docs/migration.md in the qilab_web_control repository."
    return 1
  fi

  if ! unit_routes_through_current; then
    log "Preflight failed: $SERVICE_NAME does not reach the app through $CURRENT_LINK."
    log "  Neither its ExecStart nor its environment references that path, so"
    log "  flipping the symlink would change nothing and this update would be a"
    log "  silent no-op. A system-wide install (the package in"
    log "  /usr/lib/python3/dist-packages) looks exactly like this."
    log "  Migrate this device first: see docs/migration.md in the"
    log "  qilab_web_control repository."
    return 1
  fi

  return 0
}

log "Starting self-update to version $VERSION"

if ! preflight; then
  log "Update aborted during preflight. Nothing was written."
  exit 1
fi

PREV_TARGET=""
if [[ -L "$CURRENT_LINK" ]]; then
  PREV_TARGET="$(readlink -f "$CURRENT_LINK" || true)"
fi

# Never build into the directory that is currently live: a same-version
# reinstall would otherwise delete the running release before its replacement
# exists. Directory names are only labels — the reported version comes from the
# installed distribution metadata.
TARGET_DIR="$RELEASES_DIR/$VERSION"
attempt=1
while [[ -n "$PREV_TARGET" && -d "$TARGET_DIR" && "$(readlink -f "$TARGET_DIR")" == "$PREV_TARGET" ]]; do
  attempt=$((attempt + 1))
  TARGET_DIR="$RELEASES_DIR/$VERSION-r$attempt"
done
TARGET_SITE_PACKAGES="$TARGET_DIR/site-packages"

# --- Build the new release --------------------------------------------------

pip_install_into() {
  local site_packages="$1"
  shift
  python3 -m pip install --disable-pip-version-check \
    --target "$site_packages" "$@" "$WHEEL_PATH"
}

health_check() {
  # Exactly how the unit imports it: system python3 plus PYTHONPATH.
  PYTHONPATH="$1" python3 -c 'import app.main'
}

log "Preparing release directory: $TARGET_DIR"
rm -rf "$TARGET_DIR"
mkdir -p "$TARGET_SITE_PACKAGES"

installed=0
retry_with_dependencies=0
if [[ -n "$PREV_TARGET" && -d "$PREV_TARGET/site-packages" ]]; then
  log "Seeding dependencies from the live release ($PREV_TARGET) — no network needed"
  cp -a "$PREV_TARGET/site-packages/." "$TARGET_SITE_PACKAGES/"
  # Remove the seeded copy of this app: pip --target does not uninstall, so an
  # old qilab_web_control-<old>.dist-info would survive next to the new one and
  # importlib.metadata could then report either version.
  rm -rf "$TARGET_SITE_PACKAGES/app" "$TARGET_SITE_PACKAGES"/qilab_web_control-*.dist-info

  log "Installing wheel with --no-deps"
  # A pip failure here is never a dependency problem: --no-deps resolves nothing,
  # so pip can only fail on the wheel itself. Don't retry with the network and
  # don't blame dependencies for it — report what actually broke.
  if ! pip_install_into "$TARGET_SITE_PACKAGES" --no-deps; then
    log "Update aborted: pip rejected the wheel (see its error above)."
    log "  Live release left untouched. Nothing about this is network-related."
    exit 1
  fi

  if health_check "$TARGET_SITE_PACKAGES"; then
    installed=1
  else
    # Installed fine but does not import: the seeded dependencies are the wrong
    # versions for this release. That is the one case a full install can fix.
    log "Seeded install does not import — this release's dependencies have changed."
    log "Retrying as a full install, which requires network access to PyPI."
    rm -rf "$TARGET_SITE_PACKAGES"
    mkdir -p "$TARGET_SITE_PACKAGES"
    retry_with_dependencies=1
  fi
fi

if [[ $installed -eq 0 ]]; then
  log "Installing wheel with dependencies"
  if ! pip_install_into "$TARGET_SITE_PACKAGES"; then
    log "Update aborted: pip install failed. Live release left untouched."
    if [[ $retry_with_dependencies -eq 1 ]]; then
      log "  This release changed dependencies, so it cannot be installed over"
      log "  the air on a device without PyPI access — deploy a new image."
    fi
    exit 1
  fi

  log "Health check: importing app.main from the new release"
  if ! health_check "$TARGET_SITE_PACKAGES"; then
    log "Update aborted: new release fails to import app.main. Live release left untouched."
    exit 1
  fi
fi

log "Health check passed"

# --- Swap ------------------------------------------------------------------

log "Switching active symlink to: $TARGET_DIR"
# As in update_release.sh: if a previous/manual install left $CURRENT_LINK as a
# real directory (not a symlink), `ln -sfn` would silently create the link
# *inside* it and never move the active path. Preflight rejects that case, so
# reaching here means the link is either absent or already a symlink.
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

if ! systemctl is-active --quiet "$SERVICE_NAME"; then
  log "Service failed to become active after update. Rolling back..."
  if [[ -n "$PREV_TARGET" && -d "$PREV_TARGET" ]]; then
    ln -sfn "$PREV_TARGET" "$CURRENT_LINK"
    systemctl restart "$SERVICE_NAME" || true
    log "Rollback attempted to: $PREV_TARGET"
  else
    log "No valid previous target found. Manual recovery required."
  fi
  exit 1
fi

log "Update successful: $VERSION (active release: $TARGET_DIR)"

# --- Post-update housekeeping ----------------------------------------------
#
# Runs only after the new release is verified live, so a bad script or unit in
# the new wheel can never take down a working install.

NEW_SCRIPT="$TARGET_SITE_PACKAGES/app/deploy/self_update.sh"
if [[ -f "$NEW_SCRIPT" ]] && ! cmp -s "$NEW_SCRIPT" "$SCRIPT_INSTALL_PATH"; then
  # Replace via temp file + mv: mv swaps the directory entry, so this
  # still-executing bash keeps reading its own (now unlinked) inode.
  if install -m 0755 "$NEW_SCRIPT" "$SCRIPT_INSTALL_PATH.incoming" \
    && mv -f "$SCRIPT_INSTALL_PATH.incoming" "$SCRIPT_INSTALL_PATH"; then
    log "Updated $SCRIPT_INSTALL_PATH from the new release"
  else
    log "Warning: could not update $SCRIPT_INSTALL_PATH from the new release"
  fi
fi

NEW_UNIT="$TARGET_SITE_PACKAGES/app/deploy/$(basename "$UNIT_INSTALL_PATH")"
if [[ -f "$NEW_UNIT" ]] && ! cmp -s "$NEW_UNIT" "$UNIT_INSTALL_PATH"; then
  # Deliberately not applied automatically: a unit change needs a daemon-reload
  # plus another restart, and getting that wrong strands the device. Flag it.
  log "Note: this release ships a unit file differing from $UNIT_INSTALL_PATH."
  log "  Review $NEW_UNIT and apply it manually (then daemon-reload + restart)."
fi

# Keep the live release and the one before it; drop anything older so the device
# does not accumulate one site-packages tree per update.
if [[ -n "$PREV_TARGET" ]]; then
  for release in "$RELEASES_DIR"/*; do
    [[ -d "$release" ]] || continue
    resolved="$(readlink -f "$release")"
    if [[ "$resolved" != "$TARGET_DIR" && "$resolved" != "$PREV_TARGET" ]]; then
      log "Pruning superseded release: $release"
      rm -rf "$release"
    fi
  done
fi

exit 0

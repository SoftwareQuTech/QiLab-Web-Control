# qilab_web_control

Minimal FastAPI + Jinja2 UI to control the qilab systemd service.

## Requirements

- Python 3.10+
- systemd available on the host
- A systemd service for qilab (example: qilab.service)

## Setup

```bash
python -m venv .venv
. .venv/bin/activate
pip install -U setuptools
pip install -e .
```

## Run

```bash
export QILAB_SERVICE=qilab.service
export QILAB_UI_TITLE="Qilab Control"
export QILAB_LOG_PATH="/var/log/qilab/qilab.log"
export QILAB_UPDATE_SCRIPT="/usr/bin/local/update_release.sh"
export QILAB_CONFIG_FILE="/etc/qilab/nodes.yaml"
sudo QILAB_SERVICE="$QILAB_SERVICE" \
	QILAB_UI_TITLE="$QILAB_UI_TITLE" \
	QILAB_LOG_PATH="$QILAB_LOG_PATH" \
	QILAB_UPDATE_SCRIPT="$QILAB_UPDATE_SCRIPT" \
	QILAB_CONFIG_FILE="$QILAB_CONFIG_FILE" \
	python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000

## Debian deployment (Scenario 1: root systemd + venv)

Install from the GitLab release wheel into a versioned, root-owned virtual
environment behind a `current` symlink (the same `releases/<version>` +
`current` convention the qilab binary itself deploys with), then run with
`uvicorn` under `systemd`. The symlink indirection is what lets the
[self-update](#self-update) route below swap versions atomically later.

1. Create the install root and the self-update script (installed once,
   outside the versioned release dirs — it changes rarely and must survive
   whatever version happens to be live):

```bash
sudo mkdir -p /opt/qilab-web-control/releases /opt/qilab-web-control/scripts
sudo cp scripts/self_update.sh /opt/qilab-web-control/scripts/self_update.sh
sudo chmod +x /opt/qilab-web-control/scripts/self_update.sh
```

2. Build the first versioned venv and install the release wheel (replace
   version/tag as needed):

```bash
VERSION=0.3.0
sudo python3 -m venv "/opt/qilab-web-control/releases/$VERSION/venv"
sudo "/opt/qilab-web-control/releases/$VERSION/venv/bin/pip" install --upgrade pip setuptools

curl -L -o /tmp/qilab_web_control.whl "<gitlab-release-wheel-url>"
sudo "/opt/qilab-web-control/releases/$VERSION/venv/bin/pip" install /tmp/qilab_web_control.whl

sudo ln -sfn "/opt/qilab-web-control/releases/$VERSION" /opt/qilab-web-control/current
```

3. Create a root-owned systemd unit, pointed at the `current` symlink rather
   than a fixed venv path:

```bash
sudo tee /etc/systemd/system/qilab-web-control.service >/dev/null <<'EOF'
[Unit]
Description=Qilab Web Control
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/qilab-web-control/current
Environment=QILAB_SERVICE=qilab.service
Environment=QILAB_UI_TITLE=Qilab Control
Environment=QILAB_LOG_PATH=/var/log/qilab/qilab.log
Environment=QILAB_UPDATE_SCRIPT=/usr/bin/local/update_release.sh
Environment=QILAB_CONFIG_FILE=/etc/qilab/nodes.yaml
Environment=QILAB_WEB_SCHEMA_PATH=/opt/qilab/current/config.schema.json
Environment=QILAB_WEB_SELF_UPDATE_SCRIPT=/opt/qilab-web-control/scripts/self_update.sh
# Telemetry paths are read from the qilab UNIT's environment, so they do not need
# to be repeated here. Set them once on qilab.service (or in a shared
# EnvironmentFile that both units read -- which this app now understands).
ExecStart=/opt/qilab-web-control/current/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
```

4. Enable and start service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now qilab-web-control.service
sudo systemctl status qilab-web-control.service --no-pager
```

Open http://localhost:8000

### Migrating an existing flat-venv install

If you deployed before this layout existed (a single
`/opt/qilab-web-control/venv`, no `current` symlink), migrate once:

```bash
sudo systemctl stop qilab-web-control.service

CURRENT_VERSION="$(sudo /opt/qilab-web-control/venv/bin/python -c \
  "from importlib.metadata import version; print(version('qilab_web_control'))")"
sudo mkdir -p "/opt/qilab-web-control/releases/$CURRENT_VERSION"
sudo mv /opt/qilab-web-control/venv "/opt/qilab-web-control/releases/$CURRENT_VERSION/venv"
sudo ln -sfn "/opt/qilab-web-control/releases/$CURRENT_VERSION" /opt/qilab-web-control/current

sudo mkdir -p /opt/qilab-web-control/scripts
sudo cp scripts/self_update.sh /opt/qilab-web-control/scripts/self_update.sh
sudo chmod +x /opt/qilab-web-control/scripts/self_update.sh
```

Then edit `/etc/systemd/system/qilab-web-control.service` to match step 3
above (`WorkingDirectory`/`ExecStart` under `current`, add
`QILAB_WEB_SELF_UPDATE_SCRIPT`), and:

```bash
sudo systemctl daemon-reload
sudo systemctl start qilab-web-control.service
```

## Self-update

The Control tab's "Update qilab_web_control" section lets an admin upload a
new release wheel and upgrade this app itself, without SSH access. It builds
and health-checks a brand-new versioned venv (`releases/<version>/venv`)
*before* touching anything live: if the wheel fails to install or fails a
basic `import app.main` sanity check, `current` is left completely untouched
and the running service is never even asked to restart. Only after that
check passes does it atomically flip the `current` symlink and restart —
and if the service doesn't come back up, it automatically rolls the symlink
back to the previous, known-good version and restarts again. See
`scripts/self_update.sh` for the full sequence.

One subtlety worth knowing: the update script deliberately runs as its own
`systemd-run --unit=qilab-web-control-selfupdate --collect` transient unit,
not as a plain background process of the request handler. It restarts
`qilab-web-control.service` partway through its own execution, and a plain
child process would share that service's cgroup and get killed by the same
restart before it could verify success or roll back — running it as an
independent unit is what lets it survive to finish the job.

Residual risk: if the restart *and* the rollback restart both fail (e.g. the
host itself is unhealthy), you still need console/SSH access to recover —
the same risk already accepted for the qilab binary's own update mechanism.

## Maintainer release workflow

1. Refresh the vendored schema fallback (`app/config/config.schema.json`) from the qilab tag this release targets, and update `app/config/VENDORED_SCHEMA_VERSION` to match its `x-qilab-schema-version`. `verify:vendored_schema` in CI fails the release if these two are out of sync — this is the fallback used whenever `QILAB_WEB_SCHEMA_PATH` isn't set, so keep it deliberately current rather than letting it silently drift from qilab's schema.
2. Update `CHANGELOG.md` with a section for the version you are releasing (for example `## [0.1.1] - 2026-03-03`).
3. Ensure `setup.py` `version` exactly matches the release tag version without the `v` prefix.
4. Push the version tag (format `vX.Y.Z`) to trigger build + GitLab release publication.

## Notes

- Start/stop uses `systemctl` and requires permission. If this service runs as a non-root user, add a sudoers rule or run it under a privileged service account.
- Start/stop/restart actions are available from the UI.
- `QILAB_LOG_LEVEL` can be set from the Control tab (`DEBUG`, `ERRRO`, `OFF`); saving it restarts `qilab.service`.
- The UI polls `/var/log/qilab/qilab.log` every 100ms and displays the latest lines.
- The `Config` tab is a schema-driven structured editor for the qilab `nodes.yaml`. It renders fields, dropdowns, and add/remove controls from qilab's `config.schema.json`, validates edits against that schema before saving, then writes canonical qilab-dialect YAML and restarts `qilab.service`. An invalid edit is rejected without touching the live file.
  - **Config path** is resolved the way qilab resolves it: it prefers the qilab unit's `QILAB_CONFIG_PATH` / `QILAB_CONFIG_DIR` (read via `systemctl show`), then this process's env, then `~/.qilab/config/nodes.yaml`. `QILAB_CONFIG_FILE` is **deprecated** (qilab never read it) and only used as a last resort. The resolved path and its source are shown in the UI.
  - **Schema source**: set `QILAB_WEB_SCHEMA_PATH=/opt/qilab/current/config.schema.json` to track qilab's schema live — the qilab release tarball now ships `config.schema.json` next to the binary, so `update_release.sh`'s symlink swap keeps this path pointing at the schema for whichever qilab version is actually deployed; otherwise a vendored copy bundled with this package is used. The schema cache is reloaded automatically right after a binary update (see `/update`); use the "Reload schema" button to force a reload at any other time.
- Binary updates are done by uploading a tarball + version from the UI, which executes `update_release.sh <version> <tarball_path>`.
- Binary updates are root-only: this web control process must run as root, and it executes the update script directly (no sudo fallback).
- Self-updates (of this app) are done by uploading the release wheel + version from the UI's "Update qilab_web_control" section; see [Self-update](#self-update) above. Also root-only, and the result isn't reported synchronously — check `self_update.log` in the UI (or `/var/log/qilab-web-control/self_update.log`) instead.
- The `Profiling` tab renders qilab's profiler CSVs as sortable tables, and the `Data acquisition` tab exposes the DAQ JSONL for download.
  - **Telemetry paths** are resolved the way qilab resolves them, and from the **qilab unit's** environment rather than this process's, since that is what decides where the simulator actually writes: `QILAB_PROFILER_PATH` / `QILAB_PROFILER_DIR` for the profiler and `QILAB_DAQ_PATH` / `QILAB_DAQ_DIR` for DAQ, falling back to `~/.qilab/logs/`. Note qilab's own asymmetry: `QILAB_PROFILER_PATH` is a **base with no extension** (qilab appends `_durations.csv` / `_events.csv`), while `QILAB_DAQ_PATH` is a **complete filename**. Each resolved path and its source are shown in the UI.
  - **`EnvironmentFile=` is understood.** `systemctl show -p Environment` reports only inline `Environment=` directives, so the app also reads the unit's `EnvironmentFile=` contents and layers them under the inline values, matching systemd's precedence. Without this, a deployment that keeps qilab's paths in a shared file (as the RT image does, in `/etc/default/qilab-global-env`) would silently resolve every path — config included — to the wrong place.
  - **Profiler data is written once per run**, when qilab shuts down gracefully, and each run truncates the previous file. So while qilab is active the tables show the *previous* run; the page says so, and always shows the file's modification time. A hard kill leaves no new file at all.
  - **The profiler base name is compiled into qilab** (`PROF_SAVE_DATA("profile")`), so the app cannot discover it. If it ever differs, set `QILAB_WEB_PROFILER_BASE`; when the expected file is missing the page names the bases it *did* find in that directory.
  - **DAQ is opt-in** via `QILAB_DAQ=1` on the qilab unit — compared against that exact string, so `true`/`yes` count as disabled. If the sink resolves under `$HOME` while the qilab unit sets `ProtectHome=true`, it can never write there; the page flags that specific combination.
  - Optional tuning: `QILAB_WEB_DAQ_PREVIEW_LINES` (default 5), `QILAB_WEB_DAQ_COUNT_MAX_BYTES` (default 8 MiB — above this the record count is estimated from a sample rather than scanning the whole file, and the UI marks it as an estimate).
- This server is intentionally minimal and has **no authentication**, while binding `0.0.0.0:8000` as root. The telemetry download endpoints therefore expose research data to anyone who can reach the port, as the log view and config editor already do. Bind it to a trusted interface, or put it behind an authenticating reverse proxy, before exposing it to any untrusted network.

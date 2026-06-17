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

Install from the GitLab release wheel into a root-owned virtual environment, then run with `uvicorn` under `systemd`.

1. Create app directory and venv:

```bash
sudo mkdir -p /opt/qilab-web-control
sudo python3 -m venv /opt/qilab-web-control/venv
sudo /opt/qilab-web-control/venv/bin/pip install --upgrade pip setuptools
```

2. Download and install the release wheel (replace version/tag as needed):

```bash
curl -L -o /tmp/qilab_web_control.whl "<gitlab-release-wheel-url>"
sudo /opt/qilab-web-control/venv/bin/pip install /tmp/qilab_web_control.whl
```

3. Create a root-owned systemd unit:

```bash
sudo tee /etc/systemd/system/qilab-web-control.service >/dev/null <<'EOF'
[Unit]
Description=Qilab Web Control
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/qilab-web-control
Environment=QILAB_SERVICE=qilab.service
Environment=QILAB_UI_TITLE=Qilab Control
Environment=QILAB_LOG_PATH=/var/log/qilab/qilab.log
Environment=QILAB_UPDATE_SCRIPT=/usr/bin/local/update_release.sh
Environment=QILAB_CONFIG_FILE=/etc/qilab/nodes.yaml
ExecStart=/opt/qilab-web-control/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
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

## Maintainer release workflow

1. Update `CHANGELOG.md` with a section for the version you are releasing (for example `## [0.1.1] - 2026-03-03`).
2. Ensure `setup.py` `version` exactly matches the release tag version without the `v` prefix.
3. Push the version tag (format `vX.Y.Z`) to trigger build + GitLab release publication.

## Notes

- Start/stop uses `systemctl` and requires permission. If this service runs as a non-root user, add a sudoers rule or run it under a privileged service account.
- Start/stop/restart actions are available from the UI.
- `QILAB_LOG_LEVEL` can be set from the Control tab (`DEBUG`, `ERRRO`, `OFF`); saving it restarts `qilab.service`.
- The UI polls `/var/log/qilab/qilab.log` every 100ms and displays the latest lines.
- The `Config` tab is a schema-driven structured editor for the qilab `nodes.yaml`. It renders fields, dropdowns, and add/remove controls from qilab's `config.schema.json`, validates edits against that schema before saving, then writes canonical qilab-dialect YAML and restarts `qilab.service`. An invalid edit is rejected without touching the live file.
  - **Config path** is resolved the way qilab resolves it: it prefers the qilab unit's `QILAB_CONFIG_PATH` / `QILAB_CONFIG_DIR` (read via `systemctl show`), then this process's env, then `~/.qilab/config/nodes.yaml`. `QILAB_CONFIG_FILE` is **deprecated** (qilab never read it) and only used as a last resort. The resolved path and its source are shown in the UI.
  - **Schema source**: set `QILAB_WEB_SCHEMA_PATH` to qilab's `docs/config.schema.json` (deployed alongside qilab) to track it live; otherwise a vendored copy bundled with this package is used. Use the "Reload schema" button after qilab's schema changes.
- Binary updates are done by uploading a tarball + version from the UI, which executes `update_release.sh <version> <tarball_path>`.
- Binary updates are root-only: this web control process must run as root, and it executes the update script directly (no sudo fallback).
- This server is intentionally minimal. Add authentication before exposing it to any untrusted network.

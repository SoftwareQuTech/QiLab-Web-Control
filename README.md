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

## Install as systemd service (recommended)

```bash
sudo cp <location of project>/examples/service/qilab-web-control.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now qilab-web-control.service
sudo systemctl status qilab-web-control.service --no-pager
```

The service file runs the web control as root and starts automatically on boot.

## Notes

- Start/stop uses `systemctl` and requires permission. If this service runs as a non-root user, add a sudoers rule or run it under a privileged service account.
- Start/stop/restart actions are available from the UI.
- `QILAB_LOG_LEVEL` can be set from the Control tab (`DEBUG`, `ERRRO`, `OFF`); saving it restarts `qilab.service`.
- The UI polls `/var/log/qilab/qilab.log` every 100ms and displays the latest lines.
- The `Config` tab shows YAML entries as lightweight `Name -> Value` fields; after save, values are written back to `QILAB_CONFIG_FILE` and `qilab.service` is restarted automatically.
- Binary updates are done by uploading a tarball + version from the UI, which executes `update_release.sh <version> <tarball_path>`.
- Binary updates are root-only: this web control process must run as root, and it executes the update script directly (no sudo fallback).
- This server is intentionally minimal. Add authentication before exposing it to any untrusted network.

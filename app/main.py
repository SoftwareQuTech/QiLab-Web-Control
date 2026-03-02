from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import yaml

APP_ROOT = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(APP_ROOT / "templates"))

QILAB_SERVICE = os.getenv("QILAB_SERVICE", "qilab.service")
UI_TITLE = os.getenv("QILAB_UI_TITLE", "Qilab Control")
QILAB_LOG_PATH = Path(os.getenv("QILAB_LOG_PATH", "/var/log/qilab/qilab.log"))
QILAB_LOG_LINES = int(os.getenv("QILAB_LOG_LINES", "200"))
QILAB_UPDATE_SCRIPT = os.getenv("QILAB_UPDATE_SCRIPT", "/home/mrkolev/work/qilab/scripts/update_release.sh")
QILAB_UPLOAD_DIR = Path(os.getenv("QILAB_UPLOAD_DIR", "/tmp/qilab_uploads"))
QILAB_CONFIG_FILE = Path(os.getenv("QILAB_CONFIG_FILE", "/etc/qilab/nodes.yaml"))
QILAB_LOG_LEVEL_OPTIONS = ("DEBUG", "ERROR", "OFF")

app = FastAPI(title=UI_TITLE)
app.mount("/static", StaticFiles(directory=str(APP_ROOT / "static")), name="static")

LAST_UPDATE_RESULT = {
    "ok": None,
    "timestamp": "",
    "version": "",
    "output": "No update has been run yet.",
}

LAST_CONFIG_RESULT = {
    "ok": None,
    "timestamp": "",
    "output": "No config save has been run yet.",
}

LAST_LOG_LEVEL_RESULT = {
    "ok": None,
    "timestamp": "",
    "output": "No log level change has been run yet.",
}


def _run_systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _service_status() -> Dict[str, str]:
    is_active = _run_systemctl("is-active", QILAB_SERVICE)
    status = is_active.stdout.strip() or "unknown"
    details = _run_systemctl("status", "--no-pager", QILAB_SERVICE)
    return {
        "status": status,
        "details": details.stdout.strip(),
    }


def _tail_log(log_path: Path, max_lines: int) -> str:
    if not log_path.exists():
        return f"Log file not found: {log_path}"

    try:
        with log_path.open("rb") as log_file:
            log_file.seek(0, os.SEEK_END)
            file_size = log_file.tell()
            block_size = 4096
            data = b""
            while file_size > 0 and data.count(b"\n") <= max_lines:
                read_size = min(block_size, file_size)
                file_size -= read_size
                log_file.seek(file_size)
                data = log_file.read(read_size) + data

        lines = data.decode("utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception as exc:
        return f"Failed to read log file: {exc}"


def _run_update_script(version: str, tarball_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [QILAB_UPDATE_SCRIPT, version, str(tarball_path)],
        capture_output=True,
        text=True,
        check=False,
    )


def _read_config_file() -> str:
    try:
        return QILAB_CONFIG_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except Exception as exc:
        return f"Failed to read config file: {exc}"


def _current_log_level() -> str:
    result = _run_systemctl("show", "-p", "Environment", QILAB_SERVICE)
    if result.returncode != 0:
        return "OFF"

    line = (result.stdout or "").strip()
    if not line.startswith("Environment="):
        return "OFF"

    match = re.search(r"QILAB_LOG_LEVEL=([^\s\"]+)", line)
    if not match:
        return "OFF"

    value = match.group(1).strip()
    return value if value in QILAB_LOG_LEVEL_OPTIONS else "OFF"


def _load_config_yaml() -> Tuple[Dict[str, Any], str]:
    try:
        raw_content = QILAB_CONFIG_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, ""
    except Exception as exc:
        return {}, f"Failed to read config file: {exc}"

    if not raw_content.strip():
        return {}, ""

    try:
        parsed = yaml.safe_load(raw_content)
    except Exception as exc:
        return {}, f"Invalid YAML: {exc}"

    if parsed is None:
        return {}, ""
    if not isinstance(parsed, dict):
        return {}, "Config root must be a key/value map."

    return parsed, ""


def _config_fields_from_yaml(data: Dict[str, Any]) -> List[Dict[str, str]]:
    fields: List[Dict[str, str]] = []
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            rendered = yaml.safe_dump(value, sort_keys=False, default_flow_style=False).strip()
            field_type = "yaml"
        elif value is None:
            rendered = ""
            field_type = "scalar"
        else:
            rendered = str(value)
            field_type = "scalar"

        fields.append(
            {
                "name": str(key),
                "value": rendered,
                "type": field_type,
            }
        )
    return fields


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    info = _service_status()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    active_tab = request.query_params.get("tab", "control")
    config_yaml, config_parse_error = _load_config_yaml()
    config_fields = _config_fields_from_yaml(config_yaml)
    return TEMPLATES.TemplateResponse(
        "index.html",
        {
            "request": request,
            "title": UI_TITLE,
            "service_name": QILAB_SERVICE,
            "status": info["status"],
            "details": info["details"],
            "timestamp": now,
            "active_tab": active_tab,
            "update_result": LAST_UPDATE_RESULT,
            "update_script": QILAB_UPDATE_SCRIPT,
            "log_path": str(QILAB_LOG_PATH),
            "config_file": str(QILAB_CONFIG_FILE),
            "config_result": LAST_CONFIG_RESULT,
            "config_fields": config_fields,
            "config_parse_error": config_parse_error,
            "log_level_options": QILAB_LOG_LEVEL_OPTIONS,
            "current_log_level": _current_log_level(),
            "log_level_result": LAST_LOG_LEVEL_RESULT,
        },
    )


@app.post("/start")
def start() -> RedirectResponse:
    _run_systemctl("start", QILAB_SERVICE)
    return RedirectResponse(url="/", status_code=303)


@app.post("/stop")
def stop() -> RedirectResponse:
    _run_systemctl("stop", QILAB_SERVICE)
    return RedirectResponse(url="/", status_code=303)


@app.post("/restart")
def restart() -> RedirectResponse:
    _run_systemctl("restart", QILAB_SERVICE)
    return RedirectResponse(url="/", status_code=303)


@app.get("/logs")
def logs() -> JSONResponse:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return JSONResponse(
        {
            "path": str(QILAB_LOG_PATH),
            "updated": now,
            "content": _tail_log(QILAB_LOG_PATH, QILAB_LOG_LINES),
        }
    )


@app.post("/update")
async def update_binary(
    version: str = Form(...),
    tarball: UploadFile = File(...),
) -> RedirectResponse:
    if os.geteuid() != 0:
        LAST_UPDATE_RESULT["ok"] = False
        LAST_UPDATE_RESULT["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        LAST_UPDATE_RESULT["version"] = version
        LAST_UPDATE_RESULT["output"] = "Update aborted: web control must run as root to execute update_release.sh."
        await tarball.close()
        return RedirectResponse(url="/", status_code=303)

    QILAB_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    safe_name = Path(tarball.filename or "qilab.tar.gz").name
    upload_target = Path(
        tempfile.mkstemp(prefix=f"qilab-{version}-", suffix=f"-{safe_name}", dir=QILAB_UPLOAD_DIR)[1]
    )

    try:
        with upload_target.open("wb") as out_file:
            shutil.copyfileobj(tarball.file, out_file)
    finally:
        await tarball.close()

    result = _run_update_script(version=version, tarball_path=upload_target)
    combined_output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")

    LAST_UPDATE_RESULT["ok"] = result.returncode == 0
    LAST_UPDATE_RESULT["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    LAST_UPDATE_RESULT["version"] = version
    LAST_UPDATE_RESULT["output"] = combined_output.strip() or "No output from update script."

    return RedirectResponse(url="/", status_code=303)


@app.post("/config/save")
async def save_config(request: Request) -> RedirectResponse:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if os.geteuid() != 0:
        LAST_CONFIG_RESULT["ok"] = False
        LAST_CONFIG_RESULT["timestamp"] = now
        LAST_CONFIG_RESULT["output"] = "Config save aborted: web control must run as root."
        return RedirectResponse(url="/?tab=config", status_code=303)

    form = await request.form()
    field_count_raw = str(form.get("field_count", "0"))
    try:
        field_count = int(field_count_raw)
    except ValueError:
        field_count = 0

    next_config: Dict[str, Any] = {}
    for index in range(field_count):
        field_name = str(form.get(f"field_name_{index}", "")).strip()
        field_type = str(form.get(f"field_type_{index}", "scalar")).strip()
        field_value = str(form.get(f"field_value_{index}", ""))

        if not field_name:
            continue

        try:
            if field_type == "yaml":
                parsed_value = yaml.safe_load(field_value) if field_value.strip() else None
            else:
                parsed_value = "" if field_value == "" else yaml.safe_load(field_value)
        except Exception as exc:
            LAST_CONFIG_RESULT["ok"] = False
            LAST_CONFIG_RESULT["timestamp"] = now
            LAST_CONFIG_RESULT["output"] = f"Invalid value for '{field_name}': {exc}"
            return RedirectResponse(url="/?tab=config", status_code=303)

        next_config[field_name] = parsed_value

    serialized_config = yaml.safe_dump(next_config, sort_keys=False, default_flow_style=False)

    try:
        QILAB_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        QILAB_CONFIG_FILE.write_text(serialized_config, encoding="utf-8")
    except Exception as exc:
        LAST_CONFIG_RESULT["ok"] = False
        LAST_CONFIG_RESULT["timestamp"] = now
        LAST_CONFIG_RESULT["output"] = f"Failed to write config file: {exc}"
        return RedirectResponse(url="/?tab=config", status_code=303)

    restart_result = _run_systemctl("restart", QILAB_SERVICE)
    if restart_result.returncode == 0:
        LAST_CONFIG_RESULT["ok"] = True
        LAST_CONFIG_RESULT["timestamp"] = now
        LAST_CONFIG_RESULT["output"] = "Config saved and service restarted successfully."
    else:
        LAST_CONFIG_RESULT["ok"] = False
        LAST_CONFIG_RESULT["timestamp"] = now
        LAST_CONFIG_RESULT["output"] = (
            "Config saved, but service restart failed.\n"
            + (restart_result.stdout or "")
            + ("\n" + restart_result.stderr if restart_result.stderr else "")
        ).strip()

    return RedirectResponse(url="/?tab=config", status_code=303)


@app.post("/log-level")
def set_log_level(log_level: str = Form(...)) -> RedirectResponse:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if os.geteuid() != 0:
        LAST_LOG_LEVEL_RESULT["ok"] = False
        LAST_LOG_LEVEL_RESULT["timestamp"] = now
        LAST_LOG_LEVEL_RESULT["output"] = "Log level change aborted: web control must run as root."
        return RedirectResponse(url="/?tab=control", status_code=303)

    if log_level not in QILAB_LOG_LEVEL_OPTIONS:
        LAST_LOG_LEVEL_RESULT["ok"] = False
        LAST_LOG_LEVEL_RESULT["timestamp"] = now
        LAST_LOG_LEVEL_RESULT["output"] = (
            f"Invalid log level '{log_level}'. Allowed: {', '.join(QILAB_LOG_LEVEL_OPTIONS)}"
        )
        return RedirectResponse(url="/?tab=control", status_code=303)

    drop_in_dir = Path(f"/etc/systemd/system/{QILAB_SERVICE}.d")
    drop_in_file = drop_in_dir / "10-log-level.conf"
    drop_in_content = "[Service]\n" f'Environment="QILAB_LOG_LEVEL={log_level}"\n'

    try:
        drop_in_dir.mkdir(parents=True, exist_ok=True)
        drop_in_file.write_text(drop_in_content, encoding="utf-8")
    except Exception as exc:
        LAST_LOG_LEVEL_RESULT["ok"] = False
        LAST_LOG_LEVEL_RESULT["timestamp"] = now
        LAST_LOG_LEVEL_RESULT["output"] = f"Failed to write log-level drop-in: {exc}"
        return RedirectResponse(url="/?tab=control", status_code=303)

    daemon_reload = _run_systemctl("daemon-reload")
    restart_result = _run_systemctl("restart", QILAB_SERVICE)

    if daemon_reload.returncode == 0 and restart_result.returncode == 0:
        LAST_LOG_LEVEL_RESULT["ok"] = True
        LAST_LOG_LEVEL_RESULT["timestamp"] = now
        LAST_LOG_LEVEL_RESULT["output"] = f"Log level set to {log_level}. Service restarted successfully."
    else:
        LAST_LOG_LEVEL_RESULT["ok"] = False
        LAST_LOG_LEVEL_RESULT["timestamp"] = now
        LAST_LOG_LEVEL_RESULT["output"] = (
            "Log level file updated, but reload/restart failed.\n"
            + (daemon_reload.stdout or "")
            + ("\n" + daemon_reload.stderr if daemon_reload.stderr else "")
            + ("\n" + restart_result.stdout if restart_result.stdout else "")
            + ("\n" + restart_result.stderr if restart_result.stderr else "")
        ).strip()

    return RedirectResponse(url="/?tab=control", status_code=303)

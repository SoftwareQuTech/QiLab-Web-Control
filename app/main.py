from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from enum import Enum
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Dict, Tuple

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import yaml

from . import telemetry
from .config import paths as config_paths
from .config import pipeline as config_pipeline
from .config import schema as config_schema

APP_ROOT = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(APP_ROOT / "templates"))

QILAB_SERVICE = os.getenv("QILAB_SERVICE", "qilab.service")
UI_TITLE = os.getenv("QILAB_UI_TITLE", "Qilab Control")
QILAB_LOG_PATH = Path(os.getenv("QILAB_LOG_PATH", "/var/log/qilab/qilab.log"))
QILAB_LOG_LINES = int(os.getenv("QILAB_LOG_LINES", "200"))
QILAB_UPDATE_SCRIPT = os.getenv("QILAB_UPDATE_SCRIPT", "/home/mrkolev/work/qilab/scripts/update_release.sh")
QILAB_UPLOAD_DIR = Path(os.getenv("QILAB_UPLOAD_DIR", "/tmp/qilab_uploads"))
QILAB_LOG_LEVEL_OPTIONS = ("DEBUG", "ERROR", "OFF")

# Self-update (updates qilab_web_control itself, not the qilab binary).
QILAB_WEB_CONTROL_SERVICE = os.getenv("QILAB_WEB_CONTROL_SERVICE", "qilab-web-control.service")
QILAB_WEB_SELF_UPDATE_SCRIPT = os.getenv(
    "QILAB_WEB_SELF_UPDATE_SCRIPT", str(APP_ROOT / "deploy" / "self_update.sh")
)
QILAB_WEB_SELF_UPDATE_UPLOAD_DIR = Path(
    os.getenv("QILAB_WEB_SELF_UPDATE_UPLOAD_DIR", "/tmp/qilab_web_control_uploads")
)
QILAB_WEB_SELF_UPDATE_LOG = Path(
    os.getenv("QILAB_WEB_SELF_UPDATE_LOG", "/var/log/qilab-web-control/self_update.log")
)
QILAB_WEB_SELF_UPDATE_LOG_LINES = int(os.getenv("QILAB_WEB_SELF_UPDATE_LOG_LINES", "200"))

# Telemetry (read-only views of qilab's profiler CSVs and DAQ JSONL).
QILAB_TABS = ("control", "config", "profiling", "data")
QILAB_TELEMETRY_TABS = ("profiling", "data")
# qilab passes this base to PROF_SAVE_DATA at compile time, so the app cannot
# discover it; overridable for binaries built with a different literal.
QILAB_WEB_PROFILER_BASE = os.getenv("QILAB_WEB_PROFILER_BASE", telemetry.DEFAULT_PROFILER_BASE)
QILAB_WEB_DAQ_PREVIEW_LINES = int(os.getenv("QILAB_WEB_DAQ_PREVIEW_LINES", "5"))
QILAB_WEB_DAQ_COUNT_MAX_BYTES = int(
    os.getenv("QILAB_WEB_DAQ_COUNT_MAX_BYTES", str(telemetry.COUNT_EXACT_MAX_BYTES))
)

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

LAST_SCHEMA_RESULT = {
    "ok": None,
    "timestamp": "",
    "output": "Schema has not been reloaded yet.",
}

LAST_LOG_LEVEL_RESULT = {
    "ok": None,
    "timestamp": "",
    "output": "No log level change has been run yet.",
}

LAST_SELF_UPDATE_TRIGGER = {
    "ok": None,
    "timestamp": "",
    "version": "",
    "output": "No self-update has been triggered yet.",
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


def _installed_version() -> str:
    try:
        return importlib_metadata.version("qilab_web_control")
    except importlib_metadata.PackageNotFoundError:
        return "unknown (not installed as a package)"


def _run_self_update_script(version: str, wheel_path: Path) -> None:
    """Launch self_update.sh detached, in its own systemd cgroup.

    This service's own restart is part of what the script does; a plain
    detached child (e.g. Popen(..., start_new_session=True)) would still
    share this service's cgroup and be killed by that restart before it
    could verify success or roll back. `systemd-run` gives it an
    independent transient unit that survives.
    """
    subprocess.Popen(
        [
            "systemd-run",
            "--unit=qilab-web-control-selfupdate",
            "--collect",
            QILAB_WEB_SELF_UPDATE_SCRIPT,
            version,
            str(wheel_path),
        ]
    )


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
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


def _load_current_document(config_path: Path, schema) -> Tuple[Dict[str, Any], str]:
    """Read the current config file into a doc for the form, with a UI notice.

    Always returns a usable mapping: the parsed file when readable, otherwise a
    blank default skeleton so the structured editor can still render.
    """
    try:
        raw_content = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return config_schema.default_document(), "Config file does not exist yet; a new one will be created on save."
    except Exception as exc:
        return config_schema.default_document(), f"Could not read config file: {exc}"

    if not raw_content.strip():
        return config_schema.default_document(), "Config file is empty; starting from a blank config."

    try:
        parsed = yaml.safe_load(raw_content)
    except Exception as exc:
        return config_schema.default_document(), f"Current file is not valid YAML ({exc}); starting from a blank config."

    if not isinstance(parsed, dict):
        return config_schema.default_document(), "Current file is not a key/value map; starting from a blank config."

    notice = ""
    if schema is not None and config_schema.validate(parsed, schema):
        notice = (
            "Current config does not match the known schema (it may be a legacy format). "
            "You can edit it below; saving rewrites it in the current format."
        )
    return parsed, notice


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    info = _service_status()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    active_tab = request.query_params.get("tab", "control")
    if active_tab not in QILAB_TABS:
        active_tab = "control"

    schema, schema_source, schema_error = config_schema.get_schema()
    resolution = config_paths.resolve_config(QILAB_SERVICE, systemctl=_run_systemctl)
    document, config_notice = _load_current_document(resolution.path, schema)
    config_raw = _safe_read_text(resolution.path) if schema is None else ""

    telemetry_ctx = (
        telemetry.snapshot(
            QILAB_SERVICE,
            systemctl=_run_systemctl,
            base_name=QILAB_WEB_PROFILER_BASE,
            service_active=info["status"] == "active",
            count_max_bytes=QILAB_WEB_DAQ_COUNT_MAX_BYTES,
        )
        if active_tab in QILAB_TELEMETRY_TABS
        else telemetry.empty_snapshot()
    )

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
            "config_result": LAST_CONFIG_RESULT,
            "schema_result": LAST_SCHEMA_RESULT,
            "config_path": str(resolution.path),
            "config_path_source": resolution.source,
            "config_notice": config_notice,
            "schema_available": schema is not None,
            "schema_source": schema_source,
            "schema_error": schema_error,
            "schema_obj": schema,
            "document_obj": document,
            "default_document_obj": config_schema.default_document(),
            "config_raw": config_raw,
            "log_level_options": QILAB_LOG_LEVEL_OPTIONS,
            "current_log_level": _current_log_level(),
            "log_level_result": LAST_LOG_LEVEL_RESULT,
            "installed_version": _installed_version(),
            "self_update_result": LAST_SELF_UPDATE_TRIGGER,
            **telemetry_ctx,
        },
        # Every value on this page is live state (service status, the config file,
        # the schema itself is inlined below). Without this the response carries no
        # freshness information at all, so a browser may answer a same-URL
        # navigation -- including the redirect after "Reload schema" -- from cache,
        # which looks exactly like the server ignoring the reload.
        headers={"Cache-Control": "no-store"},
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

    if result.returncode == 0:
        _, schema_source, schema_error = config_schema.reload_schema()
        if schema_error:
            LAST_UPDATE_RESULT["output"] += f"\nWarning: failed to reload config schema from {schema_source}: {schema_error}"
        else:
            LAST_UPDATE_RESULT["output"] += f"\nConfig schema reloaded from {schema_source}."

    return RedirectResponse(url="/", status_code=303)


@app.post("/self-update")
async def self_update(
    version: str = Form(...),
    wheel: UploadFile = File(...),
) -> RedirectResponse:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if os.geteuid() != 0:
        LAST_SELF_UPDATE_TRIGGER.update(
            ok=False,
            timestamp=now,
            version=version,
            output="Self-update aborted: web control must run as root to execute self_update.sh.",
        )
        await wheel.close()
        return RedirectResponse(url="/", status_code=303)

    QILAB_WEB_SELF_UPDATE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    safe_name = Path(wheel.filename or "qilab_web_control.whl").name
    upload_target = Path(
        tempfile.mkstemp(
            prefix=f"qilab-web-control-{version}-", suffix=f"-{safe_name}", dir=QILAB_WEB_SELF_UPDATE_UPLOAD_DIR
        )[1]
    )

    try:
        with upload_target.open("wb") as out_file:
            shutil.copyfileobj(wheel.file, out_file)
    finally:
        await wheel.close()

    # Fire-and-forget: this process (and this very request) may be restarted
    # partway through the update, so success/failure can't be reported
    # synchronously here — check the self-update log below instead.
    _run_self_update_script(version=version, wheel_path=upload_target)

    LAST_SELF_UPDATE_TRIGGER.update(
        ok=None,
        timestamp=now,
        version=version,
        output=(
            f"Self-update to {version} started in the background. This page's process may "
            "restart partway through — watch the log below for the result."
        ),
    )
    return RedirectResponse(url="/", status_code=303)


@app.get("/self-update/logs")
def self_update_logs() -> JSONResponse:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return JSONResponse(
        {
            "path": str(QILAB_WEB_SELF_UPDATE_LOG),
            "updated": now,
            "content": _tail_log(QILAB_WEB_SELF_UPDATE_LOG, QILAB_WEB_SELF_UPDATE_LOG_LINES),
        }
    )


@app.post("/config/save")
async def save_config(request: Request) -> JSONResponse:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if os.geteuid() != 0:
        LAST_CONFIG_RESULT.update(
            ok=False, timestamp=now, output="Config save aborted: web control must run as root."
        )
        return JSONResponse(LAST_CONFIG_RESULT, status_code=403)

    try:
        doc = await request.json()
    except Exception as exc:
        LAST_CONFIG_RESULT.update(ok=False, timestamp=now, output=f"Invalid request body: {exc}")
        return JSONResponse(LAST_CONFIG_RESULT, status_code=400)

    resolution = config_paths.resolve_config(QILAB_SERVICE, systemctl=_run_systemctl)
    schema, _, _ = config_schema.get_schema()
    result = config_pipeline.save_config_doc(
        doc,
        config_path=resolution.path,
        service=QILAB_SERVICE,
        systemctl=_run_systemctl,
        schema=schema,
    )
    LAST_CONFIG_RESULT.update(result)
    return JSONResponse(LAST_CONFIG_RESULT)


@app.post("/config/schema/reload")
def reload_config_schema() -> RedirectResponse:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    schema, source, error = config_schema.get_schema(force=True)
    if schema is None:
        LAST_SCHEMA_RESULT.update(ok=False, timestamp=now, output=f"Failed to load schema: {error}")
    else:
        suffix = f" (note: {error})" if error else ""
        LAST_SCHEMA_RESULT.update(ok=True, timestamp=now, output=f"Schema reloaded from {source}.{suffix}")
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


# --- Telemetry (read-only) ----------------------------------------------------
# These routes only read files, so unlike /update, /config/save and /log-level
# they deliberately do NOT gate on os.geteuid() -- that would break every dev run.


class Artifact(str, Enum):
    """Downloadable artifacts.

    A str-Enum path parameter means no caller-supplied path segment ever reaches
    the filesystem: Starlette rejects anything else with a 422 before the handler
    body runs, so traversal is structurally impossible rather than filtered.
    """

    profiler_durations = "profiler-durations"
    profiler_events = "profiler-events"
    daq = "daq"


@app.get("/data-acquisition/preview")
def data_acquisition_preview() -> JSONResponse:
    """Tail of the DAQ JSONL.

    Returns the same {path, updated, content} triple as /logs so the existing
    pollLog() renders it with no new JavaScript.
    """
    resolution = telemetry.resolve_daq_path(QILAB_SERVICE, systemctl=_run_systemctl)
    return JSONResponse(
        {
            "path": str(resolution.path),
            "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "content": _tail_log(resolution.path, QILAB_WEB_DAQ_PREVIEW_LINES),
        }
    )


@app.get("/telemetry/download/{artifact}")
def download_telemetry(artifact: Artifact) -> Response:
    target = telemetry.resolve_artifact(
        artifact.value,
        QILAB_SERVICE,
        systemctl=_run_systemctl,
        base_name=QILAB_WEB_PROFILER_BASE,
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Unknown artifact.")
    path, media_type, filename = target

    # The paths come from root-owned unit config, so the meaningful residual risk
    # is the log directory being swapped for a symlink, not traversal.
    if path.is_symlink() or path.parent.is_symlink():
        raise HTTPException(status_code=404, detail="Refusing to serve a symlinked telemetry path.")
    try:
        st = path.stat()
    except OSError:
        raise HTTPException(status_code=404, detail=f"Not found on disk: {path}")
    if not stat.S_ISREG(st.st_mode):
        raise HTTPException(status_code=404, detail=f"Not a regular file: {path}")

    if artifact is Artifact.daq:
        # Append-only, so any prefix is a valid JSONL document and streaming is safe.
        return FileResponse(path, media_type=media_type, filename=filename, stat_result=st)

    # The profiler CSVs are truncate-rewritten at every qilab shutdown. Streaming
    # one while that happens would send a Content-Length from the pre-truncation
    # stat and then a short body -- a corrupt download. They are kilobytes, so
    # snapshot them in a single read instead.
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=404, detail=f"Could not read {path}: {exc}")
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

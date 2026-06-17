"""The save pipeline: validate -> emit -> atomic write -> restart -> report.

The live config file is only ever touched after schema validation passes and the
new text is fully written to a temp file in the same directory; the swap is an
atomic ``os.replace``. A best-effort ``.bak`` backup of the previous file is kept.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import schema as schema_module
from .emitter import emit_yaml

SystemctlRunner = Callable[..., Any]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _result(ok: bool, output: str) -> Dict[str, Any]:
    return {"ok": ok, "timestamp": _now(), "output": output}


def save_config_doc(
    doc: Any,
    *,
    config_path: Path,
    service: str,
    systemctl: SystemctlRunner,
    schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    config_path = Path(config_path)

    errors = schema_module.validate(doc, schema)
    if errors:
        details = "\n".join(f"  {path}: {message}" for path, message in errors)
        return _result(False, "Config rejected — does not match the qilab schema:\n" + details)

    try:
        text = emit_yaml(doc)
    except Exception as exc:  # noqa: BLE001
        return _result(False, f"Failed to render config: {exc}")

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".nodes-", suffix=".yaml", dir=str(config_path.parent))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            if config_path.exists():
                try:
                    backup = config_path.parent / (config_path.name + ".bak")
                    backup.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")
                except Exception:  # noqa: BLE001 - backup is best-effort
                    pass
            os.replace(tmp_path, config_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()
    except Exception as exc:  # noqa: BLE001
        return _result(False, f"Failed to write config file: {exc}")

    restart = systemctl("restart", service)
    if getattr(restart, "returncode", 1) == 0:
        return _result(True, f"Config saved to {config_path} and {service} restarted successfully.")

    output = (
        f"Config saved to {config_path}, but {service} restart failed.\n"
        + (getattr(restart, "stdout", "") or "")
        + ("\n" + restart.stderr if getattr(restart, "stderr", "") else "")
    ).strip()
    return _result(False, output)

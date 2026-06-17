"""Resolve the qilab config file path the same way qilab itself does.

qilab resolves its config path (see qilab ``get_default_config_path``) as:
  1. ``QILAB_CONFIG_PATH``      -> used as-is
  2. ``QILAB_CONFIG_DIR``       -> ``$QILAB_CONFIG_DIR/nodes.yaml``
  3. default                    -> ``~/.qilab/config/nodes.yaml``

The control server must edit the *same* file the running simulator loads, so we
prefer the qilab unit's own environment (read via ``systemctl show``) over our
own process environment. ``QILAB_CONFIG_FILE`` (which qilab never reads) is kept
only as a deprecated last-resort alias for backwards compatibility.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional

DEFAULT_REL_PATH = ".qilab/config/nodes.yaml"

# A callable shaped like main._run_systemctl: (*args) -> CompletedProcess.
SystemctlRunner = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class Resolution:
    path: Path
    source: str


def _default_systemctl(*args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(["systemctl", *args], capture_output=True, text=True, check=False)


def _show_property(service: str, prop: str, systemctl: SystemctlRunner) -> str:
    """Return the value of ``systemctl show -p <prop>`` (text after ``Prop=``)."""
    try:
        result = systemctl("show", "-p", prop, service)
    except Exception:
        return ""
    if getattr(result, "returncode", 1) != 0:
        return ""
    line = (result.stdout or "").strip()
    prefix = f"{prop}="
    return line[len(prefix):] if line.startswith(prefix) else ""


def _unit_environment(service: str, systemctl: SystemctlRunner) -> Dict[str, str]:
    """Parse the qilab unit's ``Environment=`` assignments into a dict."""
    body = _show_property(service, "Environment", systemctl)
    env: Dict[str, str] = {}
    # systemd renders: Environment=KEY=VAL KEY2="quoted val" ...
    for match in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)=("[^"]*"|\S+)', body):
        key, value = match.group(1), match.group(2)
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]
        env[key] = value
    return env


def _home_for_user(user: str) -> str:
    if user and user not in ("root",):
        try:
            import pwd

            return pwd.getpwnam(user).pw_dir
        except (KeyError, ImportError):
            pass
    if user == "root":
        return "/root"
    return os.path.expanduser("~")


def resolve_config(
    service: str,
    *,
    environ: Optional[Mapping[str, str]] = None,
    systemctl: SystemctlRunner = _default_systemctl,
) -> Resolution:
    """Resolve the config path + a human-readable description of its source."""
    env = os.environ if environ is None else environ
    unit_env = _unit_environment(service, systemctl)

    if unit_env.get("QILAB_CONFIG_PATH"):
        return Resolution(Path(unit_env["QILAB_CONFIG_PATH"]), f"{service} env QILAB_CONFIG_PATH")
    if unit_env.get("QILAB_CONFIG_DIR"):
        return Resolution(
            Path(unit_env["QILAB_CONFIG_DIR"]) / "nodes.yaml",
            f"{service} env QILAB_CONFIG_DIR",
        )

    if env.get("QILAB_CONFIG_PATH"):
        return Resolution(Path(env["QILAB_CONFIG_PATH"]), "server env QILAB_CONFIG_PATH")
    if env.get("QILAB_CONFIG_DIR"):
        return Resolution(Path(env["QILAB_CONFIG_DIR"]) / "nodes.yaml", "server env QILAB_CONFIG_DIR")

    if env.get("QILAB_CONFIG_FILE"):
        return Resolution(
            Path(env["QILAB_CONFIG_FILE"]),
            "deprecated QILAB_CONFIG_FILE (qilab does not read this; set QILAB_CONFIG_DIR instead)",
        )

    home = _home_for_user(_show_property(service, "User", systemctl))
    path = Path(home) / DEFAULT_REL_PATH
    return Resolution(path, f"default ({path})")


def resolve_config_path(service: str, **kwargs) -> Path:
    return resolve_config(service, **kwargs).path


def describe_path_source(service: str, **kwargs) -> str:
    return resolve_config(service, **kwargs).source

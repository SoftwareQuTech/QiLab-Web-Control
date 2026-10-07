# Copyright (c) 2026, Quantum Internet Alliance, Delft University of Technology, Milen Kolev, Stephanie Wehner
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Resolve the qilab config file path the same way qilab itself does.

qilab resolves its config path (see qilab ``get_default_config_path``) as:
  1. ``QILAB_CONFIG_PATH``      -> used as-is
  2. ``QILAB_CONFIG_DIR``       -> ``$QILAB_CONFIG_DIR/nodes.yaml``
  3. default                    -> ``~/.qilab/config/nodes.yaml``

The control server must edit the *same* file the running simulator loads, so we
prefer the qilab unit's own environment over our own process environment.
``QILAB_CONFIG_FILE`` (which qilab never reads) is kept only as a deprecated
last-resort alias for backwards compatibility.

Reading the unit's environment needs BOTH sources, because ``systemctl show -p
Environment`` reports only inline ``Environment=`` directives -- it does **not**
expand ``EnvironmentFile=``, which systemd resolves at exec time. The deployed
image points both units at a shared ``/etc/default/qilab-global-env``, so relying
on ``Environment=`` alone silently falls through to the wrong tier and the UI
reads or edits the wrong file with no error. ``unit_environment`` therefore layers
``EnvironmentFile=`` contents *under* ``Environment=``, matching systemd's own
precedence.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional

DEFAULT_REL_PATH = ".qilab/config/nodes.yaml"

# A callable shaped like main._run_systemctl: (*args) -> CompletedProcess.
SystemctlRunner = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class Resolution:
    path: Path
    source: str


@dataclass(frozen=True)
class UnitEnv:
    """A unit's effective environment, with where each key came from.

    ``get()`` mirrors ``Mapping.get`` so callers can treat this like the plain
    dict it replaced; ``origin()`` exists so the UI can tell an operator *which*
    source won, which is the difference between a useful and a baffling status
    line when inline and file-provided values disagree.
    """

    values: Dict[str, str] = field(default_factory=dict)
    origins: Dict[str, str] = field(default_factory=dict)

    def get(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)

    def origin(self, key: str) -> str:
        return self.origins.get(key, "")


def default_systemctl(*args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(["systemctl", *args], capture_output=True, text=True, check=False)


def show_property(service: str, prop: str, systemctl: SystemctlRunner) -> str:
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


def _parse_env_assignments(body: str) -> Dict[str, str]:
    """Parse systemd's rendered ``KEY=VAL KEY2="quoted val"`` form."""
    env: Dict[str, str] = {}
    for match in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)=("[^"]*"|\S+)', body):
        key, value = match.group(1), match.group(2)
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]
        env[key] = value
    return env


def environment_files(service: str, systemctl: SystemctlRunner) -> List[Path]:
    """The unit's ``EnvironmentFile=`` paths, in declaration order.

    systemd renders this as::

        EnvironmentFiles=/etc/default/qilab-global-env (ignore_errors=no) /b (ignore_errors=yes)

    The ``ignore_errors`` flag only controls whether systemd fails to start on a
    missing file; a missing file is not an error for us either way, so it is
    parsed off and discarded.
    """
    body = show_property(service, "EnvironmentFiles", systemctl)
    return [Path(match.group(1)) for match in re.finditer(r"(\S+)\s+\(ignore_errors=(?:yes|no)\)", body)]


def read_environment_file(path: Path) -> Dict[str, str]:
    """Parse a systemd ``EnvironmentFile``: ``KEY=VALUE`` lines, ``#`` comments.

    Deliberately lenient -- an unreadable or malformed file yields whatever could
    be parsed rather than raising, because the caller's job is to resolve a path
    as well as it can, not to validate the deployment.
    """
    env: Dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return env
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        env[key] = value
    return env


def unit_environment(service: str, systemctl: SystemctlRunner) -> UnitEnv:
    """The unit's effective environment: ``EnvironmentFile=`` under ``Environment=``."""
    values: Dict[str, str] = {}
    origins: Dict[str, str] = {}

    for env_file in environment_files(service, systemctl):
        for key, value in read_environment_file(env_file).items():
            values[key] = value
            origins[key] = f"EnvironmentFile {env_file}"

    for key, value in _parse_env_assignments(show_property(service, "Environment", systemctl)).items():
        values[key] = value
        origins[key] = "Environment="

    return UnitEnv(values=values, origins=origins)


def home_for_user(user: str) -> str:
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
    systemctl: SystemctlRunner = default_systemctl,
) -> Resolution:
    """Resolve the config path + a human-readable description of its source."""
    env = os.environ if environ is None else environ
    unit_env = unit_environment(service, systemctl)

    if unit_env.get("QILAB_CONFIG_PATH"):
        return Resolution(
            Path(unit_env.get("QILAB_CONFIG_PATH")),
            f"{service} env QILAB_CONFIG_PATH (via {unit_env.origin('QILAB_CONFIG_PATH')})",
        )
    if unit_env.get("QILAB_CONFIG_DIR"):
        return Resolution(
            Path(unit_env.get("QILAB_CONFIG_DIR")) / "nodes.yaml",
            f"{service} env QILAB_CONFIG_DIR (via {unit_env.origin('QILAB_CONFIG_DIR')})",
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

    home = home_for_user(show_property(service, "User", systemctl))
    path = Path(home) / DEFAULT_REL_PATH
    return Resolution(path, f"default ({path})")


def resolve_config_path(service: str, **kwargs) -> Path:
    return resolve_config(service, **kwargs).path


def describe_path_source(service: str, **kwargs) -> str:
    return resolve_config(service, **kwargs).source

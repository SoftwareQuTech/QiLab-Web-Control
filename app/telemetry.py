"""Surface qilab's telemetry artifacts -- profiler CSVs and the DAQ JSONL -- read-only.

Path resolution mirrors qilab's own C++ resolvers, including an asymmetry qilab
itself has:

  * ``QILAB_PROFILER_PATH`` is a *base* with no extension; qilab's ``dump_csv``
    appends ``_durations.csv`` / ``_events.csv``  (src/logger/profiler.cpp)
  * ``QILAB_DAQ_PATH`` is a *complete filename*   (src/logger/data_acquisition.cpp)

Precedence in both cases: ``*_PATH`` -> ``*_DIR``/<base> -> ``$HOME/.qilab/logs``,
read from the **qilab unit's** environment first (see ``app.config.paths``), since
that is what decides where the simulator actually writes.

Two qilab behaviours shape everything here:

  * The profiler writes only at shutdown and *truncates*, so while qilab is
    running these CSVs describe the PREVIOUS run. Hence ``FileStatus.stale``.
  * DAQ is opt-in via ``QILAB_DAQ=1`` (compared against that literal string), so
    a missing JSONL is usually legitimate rather than an error.

Deliberately holds **no module-level cache**: the files are small, the record
count is byte-capped, and caching would fight the whole point of the page, which
is answering "how fresh is this file". If a cache is ever added it needs an
autouse reset fixture in conftest -- a lesson this suite already learned with
``schema._cache``.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .config.paths import (
    Resolution,
    SystemctlRunner,
    UnitEnv,
    default_systemctl,
    home_for_user,
    show_property,
    unit_environment,
)

DEFAULT_LOGS_REL_DIR = ".qilab/logs"
DEFAULT_PROFILER_BASE = "profile"
DEFAULT_DAQ_FILENAME = "data_acquisition.jsonl"
DURATIONS_SUFFIX = "_durations.csv"
EVENTS_SUFFIX = "_events.csv"

# Cost guards. Module constants so tests can shrink them via client._main.
COUNT_EXACT_MAX_BYTES = 8 * 1024 * 1024
ESTIMATE_SAMPLE_BYTES = 64 * 1024
TABLE_MAX_ROWS = 2000


@dataclass(frozen=True)
class FileStatus:
    """Everything the UI needs to say whether a file is worth looking at.

    ``exists=False`` is not an error -- ``error`` is reserved for genuine
    stat/read failures, mirroring how ``main._tail_log`` distinguishes "not
    found" from "failed to read".
    """

    path: str = ""
    source: str = ""
    exists: bool = False
    size_bytes: int = 0
    size_human: str = ""
    mtime: str = ""
    age_seconds: int = -1
    age_human: str = ""
    records: int = -1
    records_exact: bool = True
    stale: bool = False
    error: str = ""


@dataclass(frozen=True)
class Table:
    columns: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)
    error: str = ""
    truncated: bool = False


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------

def human_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    value = float(size_bytes)
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        value /= 1024.0
        if value < 1024.0:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} PiB"


def human_age(seconds: int) -> str:
    if seconds < 0:
        return ""
    if seconds < 2:
        return "just now"
    if seconds < 60:
        return f"{seconds} seconds ago"
    if seconds < 3600:
        return f"{seconds // 60} minutes ago"
    if seconds < 86400:
        return f"{seconds // 3600} hours ago"
    return f"{seconds // 86400} days ago"


def _utc(timestamp: float) -> str:
    """Format as UTC *explicitly*.

    ``datetime.fromtimestamp`` without a tz yields local time, which would then
    be labelled "UTC" and silently disagree with the page's own ``Updated:``
    line by the host's offset -- exactly the comparison the staleness UX rests on.
    """
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# --------------------------------------------------------------------------
# Pure resolvers: no subprocess, no filesystem
# --------------------------------------------------------------------------

def _pick(unit_env: UnitEnv, env: Mapping[str, str], key: str, service: str) -> Tuple[str, str]:
    """Value + human-readable origin for `key`, unit environment first."""
    if unit_env.get(key):
        origin = unit_env.origin(key) or "Environment="
        return unit_env.get(key), f"{service} env {key} (via {origin})"
    if env.get(key):
        return env[key], f"server env {key}"
    return "", ""


def profiler_bases(
    unit_env: UnitEnv,
    env: Mapping[str, str],
    *,
    service: str,
    home: str,
    base_name: str = DEFAULT_PROFILER_BASE,
) -> Tuple[Resolution, Resolution]:
    """(durations, events) full paths, mirroring resolve_profiler_base_path."""
    base_name = base_name or DEFAULT_PROFILER_BASE

    value, origin = _pick(unit_env, env, "QILAB_PROFILER_PATH", service)
    if value:
        base = PurePosixPath(value)
    else:
        value, origin = _pick(unit_env, env, "QILAB_PROFILER_DIR", service)
        if value:
            base = PurePosixPath(value) / base_name
        else:
            base = PurePosixPath(home) / DEFAULT_LOGS_REL_DIR / base_name
            origin = f"default ({base})"

    return (
        Resolution(Path(str(base) + DURATIONS_SUFFIX), origin),
        Resolution(Path(str(base) + EVENTS_SUFFIX), origin),
    )


def daq_file(
    unit_env: UnitEnv,
    env: Mapping[str, str],
    *,
    service: str,
    home: str,
) -> Resolution:
    """The DAQ JSONL path, mirroring resolve_daq_path."""
    value, origin = _pick(unit_env, env, "QILAB_DAQ_PATH", service)
    if value:
        return Resolution(Path(value), origin)

    value, origin = _pick(unit_env, env, "QILAB_DAQ_DIR", service)
    if value:
        return Resolution(Path(value) / DEFAULT_DAQ_FILENAME, origin)

    path = Path(home) / DEFAULT_LOGS_REL_DIR / DEFAULT_DAQ_FILENAME
    return Resolution(path, f"default ({path})")


def resolve_daq_path(
    service: str,
    *,
    environ: Optional[Mapping[str, str]] = None,
    systemctl: SystemctlRunner = default_systemctl,
) -> Resolution:
    """Injectable wrapper around :func:`daq_file`, in ``paths.resolve_config`` style.

    For callers that need only the DAQ path and should not pay for a full
    :func:`snapshot`.
    """
    env = os.environ if environ is None else environ
    unit_env = unit_environment(service, systemctl)
    home = home_for_user(show_property(service, "User", systemctl))
    return daq_file(unit_env, env, service=service, home=home)


def resolve_profiler_paths(
    service: str,
    *,
    environ: Optional[Mapping[str, str]] = None,
    systemctl: SystemctlRunner = default_systemctl,
    base_name: str = DEFAULT_PROFILER_BASE,
) -> Tuple[Resolution, Resolution]:
    """Injectable wrapper around :func:`profiler_bases`."""
    env = os.environ if environ is None else environ
    unit_env = unit_environment(service, systemctl)
    home = home_for_user(show_property(service, "User", systemctl))
    return profiler_bases(unit_env, env, service=service, home=home, base_name=base_name)


def daq_enabled(unit_env: UnitEnv, *, service: str, file_exists: bool = False) -> Tuple[bool, str]:
    """Whether qilab's DAQ sink is on, and why we think so.

    The unit's environment is authoritative, since that is what the simulator
    actually runs with -- this server's own ``QILAB_DAQ`` says nothing about it.
    But an existing data file is *evidence*, and it outranks the inference:
    reporting "disabled" next to a populated file and a download button would be
    self-contradictory, and the usual cause is qilab having been started outside
    systemd.

    qilab compares the variable against the literal ``"1"``, so ``true``/``yes``
    are *disabled* -- worth saying out loud, because it is an easy mistake.
    """
    raw = unit_env.get("QILAB_DAQ")
    if raw == "1":
        origin = unit_env.origin("QILAB_DAQ") or "Environment="
        return True, f"{service} env QILAB_DAQ=1 (via {origin})"

    if raw:
        detail = (
            f"{service} sets QILAB_DAQ={raw!r}, which qilab reads as disabled -- "
            'it compares against the exact string "1"'
        )
    else:
        detail = f"{service} does not set QILAB_DAQ=1"

    if file_exists:
        return True, f"data file present, so the sink is recording ({detail}; qilab may be running outside systemd)"
    return False, f"{detail}, so the sink is off"


# --------------------------------------------------------------------------
# Readers
# --------------------------------------------------------------------------

def _read_table(path: Path, *, expected_header: str, max_rows: int) -> Table:
    try:
        # newline="" lets csv normalise line endings; cells are stripped anyway
        # so a stray \r cannot leak into a numeric column and break sorting.
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                return Table(error=f"{path} is empty.")

            columns = [cell.strip() for cell in header]
            rows: List[List[str]] = []
            truncated = False
            for record in reader:
                if not record or all(not cell.strip() for cell in record):
                    continue
                if len(rows) >= max_rows:
                    truncated = True
                    break
                rows.append([cell.strip() for cell in record])
    except FileNotFoundError:
        return Table(error=f"Not found: {path}")
    except OSError as exc:
        return Table(error=f"Could not read {path}: {exc}")

    if columns and columns[0] != expected_header:
        return Table(
            columns=columns,
            rows=rows,
            truncated=truncated,
            error=(
                f"Unexpected first column {columns[0]!r} (expected {expected_header!r}); "
                "showing the file as-is."
            ),
        )
    return Table(columns=columns, rows=rows, truncated=truncated)


def read_durations(path: Path, *, max_rows: int = TABLE_MAX_ROWS) -> Table:
    """Parse profile_durations.csv.

    Column count is read from the header rather than assumed, so both the
    4-column form and the 5-column ``Avg[ns]`` form work and the two repos need
    not deploy in lockstep.
    """
    return _read_table(path, expected_header="Event", max_rows=max_rows)


def read_events(path: Path, *, max_rows: int = TABLE_MAX_ROWS) -> Table:
    """Parse profile_events.csv. Unbounded in qilab, hence the row cap."""
    return _read_table(path, expected_header="Message", max_rows=max_rows)


def count_records(path: Path, *, exact_max_bytes: int = COUNT_EXACT_MAX_BYTES) -> Tuple[int, bool]:
    """(count, exact) newline count for the JSONL.

    Exact below the byte cap, sampled above it -- a per-second-flushed stream over
    a long run reaches hundreds of MB, and this runs in a sync route that blocks a
    threadpool worker.

    Counts ``\\n``, so a partially-written trailing record is not counted. That is
    the safe direction: ``records`` means *complete* records. Never json-parses per
    line -- that turns an I/O-bound scan into a CPU-bound one for no gain.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return -1, True
    if size == 0:
        return 0, True
    try:
        with path.open("rb") as handle:
            if size <= exact_max_bytes:
                total = 0
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    total += chunk.count(b"\n")
                return total, True
            sample = handle.read(ESTIMATE_SAMPLE_BYTES)
            newlines = sample.count(b"\n")
            if newlines == 0:
                return -1, False
            return int(size / (len(sample) / newlines)), False
    except OSError:
        return -1, True


def stat_file(
    resolution: Resolution,
    *,
    records: Optional[int] = None,
    records_exact: bool = True,
    service_active: Optional[bool] = None,
    now: Optional[float] = None,
) -> FileStatus:
    path = resolution.path
    try:
        st = path.stat()
    except FileNotFoundError:
        return FileStatus(path=str(path), source=resolution.source, exists=False)
    except OSError as exc:
        return FileStatus(
            path=str(path), source=resolution.source, exists=False, error=f"Could not stat: {exc}"
        )

    current = datetime.now(timezone.utc).timestamp() if now is None else now
    age = max(0, int(current - st.st_mtime))
    return FileStatus(
        path=str(path),
        source=resolution.source,
        exists=True,
        size_bytes=st.st_size,
        size_human=human_size(st.st_size),
        mtime=_utc(st.st_mtime),
        age_seconds=age,
        age_human=human_age(age),
        records=-1 if records is None else records,
        records_exact=records_exact,
        # dump_csv truncates and runs only at shutdown, so if qilab is up, this
        # file necessarily predates the current run.
        stale=bool(service_active),
    )


def other_bases_in_dir(path: Path) -> List[str]:
    """Profiler bases actually present next to a missing expected file.

    The base name is a compile-time literal in qilab, so the app cannot discover
    it. Rather than reporting "not found" while files sit right there, name them.
    """
    try:
        found = sorted(p.name for p in path.parent.glob("*" + DURATIONS_SUFFIX))
    except OSError:
        return []
    return [name[: -len(DURATIONS_SUFFIX)] for name in found if name != path.name]


# --------------------------------------------------------------------------
# One call per render
# --------------------------------------------------------------------------

def empty_snapshot() -> Dict[str, Any]:
    """Placeholder context so the template needs no `is defined` guards."""
    from dataclasses import asdict

    blank = asdict(FileStatus())
    return {
        "telemetry_ready": False,
        "durations_status": blank,
        "events_status": dict(blank),
        "daq_status": dict(blank),
        "durations_table": asdict(Table()),
        "events_table": asdict(Table()),
        "daq_enabled": False,
        "daq_enabled_reason": "",
        "telemetry_notices": [],
    }


def snapshot(
    service: str,
    *,
    environ: Optional[Mapping[str, str]] = None,
    systemctl: SystemctlRunner = default_systemctl,
    base_name: str = DEFAULT_PROFILER_BASE,
    service_active: Optional[bool] = None,
    count_max_bytes: int = COUNT_EXACT_MAX_BYTES,
    max_rows: int = TABLE_MAX_ROWS,
) -> Dict[str, Any]:
    """Resolve, stat and parse every telemetry artifact in one pass.

    Reads the unit environment and ``User`` exactly once and threads them through
    the pure resolvers; resolving each artifact independently would cost four
    extra ``systemctl`` subprocesses per page render.

    Returns plain scalars/dicts, not dataclasses: both ``JSONResponse`` and
    Jinja's ``tojson`` use plain ``json.dumps`` and raise on dataclasses.
    """
    from dataclasses import asdict

    env = os.environ if environ is None else environ
    unit_env = unit_environment(service, systemctl)
    home = home_for_user(show_property(service, "User", systemctl))

    durations_res, events_res = profiler_bases(
        unit_env, env, service=service, home=home, base_name=base_name
    )
    daq_res = daq_file(unit_env, env, service=service, home=home)

    durations_status = stat_file(durations_res, service_active=service_active)
    events_status = stat_file(events_res, service_active=service_active)

    daq_records, daq_exact = count_records(daq_res.path, exact_max_bytes=count_max_bytes)
    daq_status = stat_file(daq_res, records=daq_records, records_exact=daq_exact)
    enabled, enabled_reason = daq_enabled(
        unit_env, service=service, file_exists=daq_status.exists
    )

    durations_table = (
        read_durations(durations_res.path, max_rows=max_rows) if durations_status.exists else Table()
    )
    events_table = read_events(events_res.path, max_rows=max_rows) if events_status.exists else Table()

    notices: List[str] = []

    if durations_status.exists and durations_status.stale:
        notices.append(
            "qilab is running. The profiler writes these CSVs only at shutdown and "
            f"overwrites them each run, so this shows the PREVIOUS run (modified "
            f"{durations_status.mtime}, {durations_status.age_human}). Stop or restart "
            "the service to refresh it."
        )

    if not durations_status.exists:
        alternatives = other_bases_in_dir(durations_res.path)
        if alternatives:
            names = ", ".join(repr(name) for name in alternatives)
            notices.append(
                f"No {durations_res.path.name} in {durations_res.path.parent}, but found "
                f"base(s) {names} there. The base name is compiled into qilab; set "
                f"QILAB_WEB_PROFILER_BASE to match."
            )
        else:
            notices.append(
                f"No profiler data at {durations_res.path}. It is written when qilab "
                "shuts down gracefully (the unit's SIGINT + TimeoutStopSec); a hard "
                "kill leaves no file."
            )

    if enabled and not daq_status.exists:
        protect_home = show_property(service, "ProtectHome", systemctl).strip().lower()
        if protect_home in ("yes", "true", "1", "read-only") and DEFAULT_LOGS_REL_DIR in str(daq_res.path):
            notices.append(
                f"QILAB_DAQ=1 but the sink resolves to {daq_res.path}, under $HOME, while "
                f"{service} runs with ProtectHome={protect_home}. qilab cannot write there. "
                "Set QILAB_DAQ_DIR to a path inside the unit's ReadWritePaths "
                "(e.g. /var/log/qilab/daq)."
            )
        else:
            notices.append(
                f"QILAB_DAQ=1 but no file at {daq_res.path} yet. It appears once qilab "
                "records its first data (flushed about once a second)."
            )

    return {
        "telemetry_ready": True,
        "durations_status": asdict(durations_status),
        "events_status": asdict(events_status),
        "daq_status": asdict(daq_status),
        "durations_table": asdict(durations_table),
        "events_table": asdict(events_table),
        "daq_enabled": enabled,
        "daq_enabled_reason": enabled_reason,
        "telemetry_notices": notices,
    }


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------

#: artifact key -> media type. Also the whitelist: a key absent here is a 404.
DOWNLOADABLE = {
    "profiler-durations": "text/csv",
    "profiler-events": "text/csv",
    "daq": "application/x-ndjson",
}


def resolve_artifact(
    key: str,
    service: str,
    *,
    environ: Optional[Mapping[str, str]] = None,
    systemctl: SystemctlRunner = default_systemctl,
    base_name: str = DEFAULT_PROFILER_BASE,
) -> Optional[Tuple[Path, str, str]]:
    """(path, media_type, download_filename) for a whitelisted artifact key.

    Resolves paths only -- it does not stat, parse or count, so serving a file
    costs nothing beyond reading the unit environment once.

    The filename carries the file's mtime, because the profiler CSVs are
    overwritten every run: a fixed name leaves an operator who collected three
    runs with ``profile_durations(2).csv`` and no way to tell them apart.
    """
    media_type = DOWNLOADABLE.get(key)
    if media_type is None:
        return None

    env = os.environ if environ is None else environ
    unit_env = unit_environment(service, systemctl)
    home = home_for_user(show_property(service, "User", systemctl))

    if key == "daq":
        path = daq_file(unit_env, env, service=service, home=home).path
    else:
        durations, events = profiler_bases(
            unit_env, env, service=service, home=home, base_name=base_name
        )
        path = durations.path if key == "profiler-durations" else events.path

    stamp = ""
    try:
        stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    except OSError:
        pass
    filename = f"{path.stem}-{stamp}{path.suffix}" if stamp else path.name
    return path, media_type, filename

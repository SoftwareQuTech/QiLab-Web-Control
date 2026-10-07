# Copyright (c) 2026, Quantum Internet Alliance, Delft University of Technology, Milen Kolev, Stephanie Wehner
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Pure unit tests for app.telemetry: resolution, parsing, status, diagnostics.

No TestClient and no systemd -- the resolvers are split so the interesting logic
takes an already-read unit environment, which is what makes these tests cheap.
"""

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import telemetry
from app.config.paths import UnitEnv
from conftest import make_systemctl

SERVICE = "qilab.service"


def unit(**values):
    return UnitEnv(values=dict(values), origins={key: "Environment=" for key in values})


# --- Path resolution ---------------------------------------------------------
# Mirrors qilab's own resolvers; the asymmetry between the two is qilab's, not ours.


def test_profiler_dir_appends_base_and_suffixes():
    durations, events = telemetry.profiler_bases(
        unit(QILAB_PROFILER_DIR="/var/log/qilab/profiler"), {}, service=SERVICE, home="/root"
    )
    assert str(durations.path) == "/var/log/qilab/profiler/profile_durations.csv"
    assert str(events.path) == "/var/log/qilab/profiler/profile_events.csv"
    assert "QILAB_PROFILER_DIR" in durations.source


def test_profiler_path_is_a_base_not_a_filename():
    """QILAB_PROFILER_PATH has no extension; dump_csv appends the suffixes."""
    durations, events = telemetry.profiler_bases(
        unit(QILAB_PROFILER_PATH="/tmp/run7"), {}, service=SERVICE, home="/root"
    )
    assert str(durations.path) == "/tmp/run7_durations.csv"
    assert str(events.path) == "/tmp/run7_events.csv"


def test_profiler_path_beats_dir():
    durations, _ = telemetry.profiler_bases(
        unit(QILAB_PROFILER_PATH="/tmp/wins", QILAB_PROFILER_DIR="/tmp/loses"),
        {},
        service=SERVICE,
        home="/root",
    )
    assert str(durations.path) == "/tmp/wins_durations.csv"


def test_profiler_falls_back_to_home():
    durations, _ = telemetry.profiler_bases(UnitEnv(), {}, service=SERVICE, home="/root")
    assert str(durations.path) == "/root/.qilab/logs/profile_durations.csv"
    assert "default" in durations.source


def test_profiler_base_name_is_overridable():
    """The base is a compile-time literal in qilab, so it must be configurable."""
    durations, _ = telemetry.profiler_bases(
        unit(QILAB_PROFILER_DIR="/logs"), {}, service=SERVICE, home="/root", base_name="test_trace"
    )
    assert str(durations.path) == "/logs/test_trace_durations.csv"


def test_daq_path_is_a_full_filename():
    """QILAB_DAQ_PATH is complete, unlike QILAB_PROFILER_PATH."""
    resolution = telemetry.daq_file(
        unit(QILAB_DAQ_PATH="/tmp/custom.jsonl"), {}, service=SERVICE, home="/root"
    )
    assert str(resolution.path) == "/tmp/custom.jsonl"


def test_daq_dir_appends_default_filename():
    resolution = telemetry.daq_file(
        unit(QILAB_DAQ_DIR="/var/log/qilab/daq"), {}, service=SERVICE, home="/root"
    )
    assert str(resolution.path) == "/var/log/qilab/daq/data_acquisition.jsonl"


def test_daq_falls_back_to_home():
    resolution = telemetry.daq_file(UnitEnv(), {}, service=SERVICE, home="/root")
    assert str(resolution.path) == "/root/.qilab/logs/data_acquisition.jsonl"


def test_unit_environment_beats_server_environment():
    durations, _ = telemetry.profiler_bases(
        unit(QILAB_PROFILER_DIR="/unit"),
        {"QILAB_PROFILER_DIR": "/server"},
        service=SERVICE,
        home="/root",
    )
    assert str(durations.path) == "/unit/profile_durations.csv"


def test_server_environment_used_when_unit_has_none():
    durations, _ = telemetry.profiler_bases(
        UnitEnv(), {"QILAB_PROFILER_DIR": "/server"}, service=SERVICE, home="/root"
    )
    assert str(durations.path) == "/server/profile_durations.csv"
    assert "server env" in durations.source


def test_resolve_wrappers_default_to_process_environment(monkeypatch, tmp_path):
    """environ=None must mean os.environ, matching paths.resolve_config.

    Regression test: defaulting to an empty dict silently disabled the whole
    server-env tier, and every test that passed environ= explicitly still passed.
    """
    monkeypatch.setenv("QILAB_PROFILER_DIR", str(tmp_path))
    monkeypatch.setenv("QILAB_DAQ_DIR", str(tmp_path))
    systemctl = make_systemctl()

    durations, _ = telemetry.resolve_profiler_paths(SERVICE, systemctl=systemctl)
    assert durations.path == tmp_path / "profile_durations.csv"
    assert telemetry.resolve_daq_path(SERVICE, systemctl=systemctl).path == (
        tmp_path / "data_acquisition.jsonl"
    )


# --- DAQ enablement ----------------------------------------------------------


def test_daq_enabled_requires_the_literal_one():
    assert telemetry.daq_enabled(unit(QILAB_DAQ="1"), service=SERVICE)[0] is True
    for value in ("true", "yes", "0", " 1"):
        enabled, reason = telemetry.daq_enabled(unit(QILAB_DAQ=value), service=SERVICE)
        assert enabled is False, value
        assert "disabled" in reason or "off" in reason


def test_daq_unset_is_disabled():
    enabled, reason = telemetry.daq_enabled(UnitEnv(), service=SERVICE)
    assert enabled is False
    assert "does not set QILAB_DAQ=1" in reason


def test_existing_file_outranks_a_disabled_unit_env():
    """Reporting "disabled" beside a populated file and a download button would
    contradict itself; the usual cause is qilab started outside systemd."""
    enabled, reason = telemetry.daq_enabled(UnitEnv(), service=SERVICE, file_exists=True)
    assert enabled is True
    assert "recording" in reason


# --- CSV parsing -------------------------------------------------------------


def test_reads_five_column_durations(tmp_profiler):
    table = telemetry.read_durations(tmp_profiler / "profile_durations.csv")
    assert table.columns == ["Event", "Max[ns]", "Min[ns]", "Count", "Avg[ns]"]
    assert table.rows[0] == ["send", "900", "100", "3", "400"]
    assert table.error == ""


def test_reads_four_column_durations(tmp_profiler_legacy):
    """Older qilab builds have no Avg[ns]; both forms must work."""
    table = telemetry.read_durations(tmp_profiler_legacy / "profile_durations.csv")
    assert table.columns == ["Event", "Max[ns]", "Min[ns]", "Count"]
    assert table.rows == [["send", "900", "100", "3"]]
    assert table.error == ""


def test_reads_events(tmp_profiler):
    table = telemetry.read_events(tmp_profiler / "profile_events.csv")
    assert table.columns == ["Message", "TimestampNs"]
    assert table.rows == [["started", "0"]]


def test_strips_carriage_returns(tmp_path):
    path = tmp_path / "profile_durations.csv"
    path.write_bytes(b"Event,Max[ns],Min[ns],Count\r\nsend,900,100,3\r\n")
    table = telemetry.read_durations(path)
    assert table.columns == ["Event", "Max[ns]", "Min[ns]", "Count"]
    assert table.rows == [["send", "900", "100", "3"]]


def test_missing_csv_reports_not_found(tmp_path):
    table = telemetry.read_durations(tmp_path / "absent_durations.csv")
    assert "Not found" in table.error
    assert table.rows == []


def test_empty_csv_is_reported_not_crashed(tmp_path):
    path = tmp_path / "profile_durations.csv"
    path.write_text("", encoding="utf-8")
    assert "empty" in telemetry.read_durations(path).error


def test_unexpected_header_still_shows_data(tmp_path):
    path = tmp_path / "profile_durations.csv"
    path.write_text("Something,Else\na,b\n", encoding="utf-8")
    table = telemetry.read_durations(path)
    assert table.rows == [["a", "b"]]
    assert "Unexpected first column" in table.error


def test_row_cap_marks_truncated(tmp_path):
    path = tmp_path / "profile_events.csv"
    path.write_text("Message,TimestampNs\n" + "".join(f"m{i},{i}\n" for i in range(50)), "utf-8")
    table = telemetry.read_events(path, max_rows=10)
    assert len(table.rows) == 10
    assert table.truncated is True


# --- Record counting ---------------------------------------------------------


def test_counts_records_exactly(tmp_daq):
    assert telemetry.count_records(tmp_daq) == (3, True)


def test_partial_trailing_record_is_not_counted(tmp_path):
    """Counting newlines means `records` is the number of COMPLETE records --
    the safe direction, since DAQ flushes while appending."""
    path = tmp_path / "daq.jsonl"
    path.write_text('{"a":1}\n{"a":2}\n{"a":3', encoding="utf-8")
    assert telemetry.count_records(path) == (2, True)


def test_empty_file_counts_zero_exactly(tmp_path):
    path = tmp_path / "daq.jsonl"
    path.write_text("", encoding="utf-8")
    assert telemetry.count_records(path) == (0, True)


def test_missing_file_counts_unknown(tmp_path):
    assert telemetry.count_records(tmp_path / "absent.jsonl") == (-1, True)


def test_large_file_is_estimated_not_scanned(tmp_path):
    """Above the byte cap the count is sampled, and says so."""
    path = tmp_path / "daq.jsonl"
    path.write_text('{"a":1}\n' * 1000, encoding="utf-8")
    count, exact = telemetry.count_records(path, exact_max_bytes=100)
    assert exact is False
    assert 900 <= count <= 1100


# --- Status ------------------------------------------------------------------


def test_status_reports_size_mtime_and_age(tmp_daq):
    from app.config.paths import Resolution

    status = telemetry.stat_file(Resolution(tmp_daq, "test"), records=3)
    assert status.exists is True
    assert status.size_bytes > 0
    assert status.size_human.endswith(" B")
    assert status.mtime.endswith(" UTC")
    assert status.records == 3
    assert status.error == ""


def test_mtime_is_utc_not_local(tmp_path):
    """Without an explicit tz, fromtimestamp yields local time labelled "UTC",
    which would silently disagree with the page's own Updated: line."""
    from app.config.paths import Resolution

    path = tmp_path / "f"
    path.write_text("x", encoding="utf-8")
    os.utime(path, (0, 0))  # epoch
    status = telemetry.stat_file(Resolution(path, "test"))
    assert status.mtime == "1970-01-01 00:00:00 UTC"


def test_missing_file_is_not_an_error(tmp_path):
    from app.config.paths import Resolution

    status = telemetry.stat_file(Resolution(tmp_path / "absent", "test"))
    assert status.exists is False
    assert status.error == ""


def test_active_service_marks_profiler_data_stale(tmp_daq):
    """dump_csv truncates and runs only at shutdown, so if qilab is up the file
    necessarily describes the previous run."""
    from app.config.paths import Resolution

    assert telemetry.stat_file(Resolution(tmp_daq, "t"), service_active=True).stale is True
    assert telemetry.stat_file(Resolution(tmp_daq, "t"), service_active=False).stale is False


# --- Diagnostics -------------------------------------------------------------


def test_other_bases_names_what_is_actually_present(tmp_profiler):
    """The base name is compiled into qilab, so a mismatch must be diagnosed
    rather than reported as a bare "not found"."""
    expected = tmp_profiler / "wrong_base_durations.csv"
    assert telemetry.other_bases_in_dir(expected) == ["profile"]


def test_other_bases_empty_when_nothing_present(tmp_path):
    assert telemetry.other_bases_in_dir(tmp_path / "x_durations.csv") == []


def test_snapshot_notices_wrong_base_name(tmp_profiler):
    snap = telemetry.snapshot(
        SERVICE,
        environ={"QILAB_PROFILER_DIR": str(tmp_profiler)},
        systemctl=make_systemctl(),
        base_name="wrong_base",
    )
    joined = " ".join(snap["telemetry_notices"])
    assert "wrong_base_durations.csv" in joined
    assert "'profile'" in joined
    assert "QILAB_WEB_PROFILER_BASE" in joined


def test_snapshot_notices_protect_home_conflict(tmp_path):
    """QILAB_DAQ=1 while the sink resolves under $HOME and the unit sets
    ProtectHome yields an enabled sink that can never write -- with no error
    anywhere unless we say so."""
    systemctl = make_systemctl(
        environment="QILAB_DAQ=1", user="root", protect_home="yes"
    )
    snap = telemetry.snapshot(SERVICE, environ={}, systemctl=systemctl)
    joined = " ".join(snap["telemetry_notices"])
    assert "ProtectHome" in joined
    assert "QILAB_DAQ_DIR" in joined


def test_snapshot_notices_stale_profiler_data(tmp_profiler):
    snap = telemetry.snapshot(
        SERVICE,
        environ={"QILAB_PROFILER_DIR": str(tmp_profiler)},
        systemctl=make_systemctl(),
        service_active=True,
    )
    joined = " ".join(snap["telemetry_notices"])
    assert "PREVIOUS run" in joined


def test_snapshot_is_json_serialisable(tmp_profiler, tmp_daq):
    """JSONResponse and Jinja's tojson both use plain json.dumps, which raises
    on dataclasses -- so the snapshot must be plain scalars throughout."""
    import json

    snap = telemetry.snapshot(
        SERVICE,
        environ={"QILAB_PROFILER_DIR": str(tmp_profiler), "QILAB_DAQ_PATH": str(tmp_daq)},
        systemctl=make_systemctl(),
    )
    json.dumps(snap)
    assert snap["durations_table"]["rows"][0][0] == "send"
    assert snap["daq_status"]["records"] == 3


def test_empty_snapshot_has_the_same_keys_as_a_real_one(tmp_profiler):
    """The template indexes these unconditionally, so the key sets must match or
    a non-telemetry tab raises at render time."""
    real = telemetry.snapshot(
        SERVICE, environ={"QILAB_PROFILER_DIR": str(tmp_profiler)}, systemctl=make_systemctl()
    )
    assert set(telemetry.empty_snapshot()) == set(real)


# --- Download targets --------------------------------------------------------


def test_resolve_artifact_stamps_the_filename_with_mtime(tmp_profiler):
    target = telemetry.resolve_artifact(
        "profiler-durations",
        SERVICE,
        environ={"QILAB_PROFILER_DIR": str(tmp_profiler)},
        systemctl=make_systemctl(),
    )
    assert target is not None
    path, media_type, filename = target
    assert path == tmp_profiler / "profile_durations.csv"
    assert media_type == "text/csv"
    # Overwritten every run, so a fixed name would collide across collected runs.
    assert filename.startswith("profile_durations-")
    assert filename.endswith(".csv")


def test_resolve_artifact_rejects_unknown_keys():
    for key in ("bogus", "../../etc/passwd", ""):
        assert telemetry.resolve_artifact(key, SERVICE, systemctl=make_systemctl()) is None


def test_resolve_artifact_daq_media_type(tmp_daq):
    target = telemetry.resolve_artifact(
        "daq", SERVICE, environ={"QILAB_DAQ_PATH": str(tmp_daq)}, systemctl=make_systemctl()
    )
    assert target is not None
    assert target[1] == "application/x-ndjson"


# --- Formatting --------------------------------------------------------------


@pytest.mark.parametrize(
    "size,expected",
    [(0, "0 B"), (512, "512 B"), (1024, "1.0 KiB"), (1536, "1.5 KiB"), (1048576, "1.0 MiB")],
)
def test_human_size(size, expected):
    assert telemetry.human_size(size) == expected


@pytest.mark.parametrize(
    "seconds,expected",
    [(-1, ""), (0, "just now"), (30, "30 seconds ago"), (120, "2 minutes ago"), (7200, "2 hours ago")],
)
def test_human_age(seconds, expected):
    assert telemetry.human_age(seconds) == expected

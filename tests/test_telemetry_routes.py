# Copyright (c) 2026, Quantum Internet Alliance, Delft University of Technology, Milen Kolev, Stephanie Wehner
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Route-level tests for the Profiling and Data acquisition tabs.

Asserts on rendered HTML substrings and response headers, matching the style of
tests/test_index.py. The `client` fixture's FakeSystemctl leaves the unit
environment empty, so pointing QILAB_* at tmp_path via monkeypatch.setenv works
through the server-env tier.
"""

import pytest


@pytest.fixture
def telemetry_client(client, monkeypatch, tmp_profiler, tmp_daq):
    monkeypatch.setenv("QILAB_PROFILER_DIR", str(tmp_profiler))
    monkeypatch.setenv("QILAB_DAQ_PATH", str(tmp_daq))
    client._profiler = tmp_profiler
    client._daq = tmp_daq
    return client


# --- Tabs --------------------------------------------------------------------


def test_nav_lists_the_new_tabs(client):
    text = client.get("/").text
    assert "/?tab=profiling" in text
    assert "/?tab=data" in text


def test_control_tab_is_highlighted_by_default(client):
    text = client.get("/").text
    assert '/?tab=control" class="tab-link tab-link-active' in text


def test_unknown_tab_falls_back_to_control(client):
    """The nav uses explicit equality now, so an unnormalised tab would render the
    Control body with NO link highlighted."""
    text = client.get("/?tab=bogus").text
    assert '/?tab=control" class="tab-link tab-link-active' in text
    assert "systemctl status" in text  # the Control body really did render


def test_existing_tabs_still_render(client):
    assert "systemctl status" in client.get("/?tab=control").text
    assert 'id="config-form"' in client.get("/?tab=config").text


# --- Profiling tab -----------------------------------------------------------


def test_profiling_tab_renders_the_durations_table(telemetry_client):
    text = telemetry_client.get("/?tab=profiling").text
    assert "Profiler durations" in text
    assert "Avg[ns]" in text
    assert "<td>send</td>" in text
    assert "<td>400</td>" in text
    assert "data-sortable" in text
    assert "/static/telemetry.js" in text


def test_profiling_tab_shows_the_resolved_path_and_source(telemetry_client):
    text = telemetry_client.get("/?tab=profiling").text
    assert str(telemetry_client._profiler / "profile_durations.csv") in text
    assert "server env QILAB_PROFILER_DIR" in text


def test_profiling_tab_offers_downloads(telemetry_client):
    text = telemetry_client.get("/?tab=profiling").text
    assert "/telemetry/download/profiler-durations" in text
    assert "/telemetry/download/profiler-events" in text


def test_profiling_tab_diagnoses_a_wrong_base_name(telemetry_client, monkeypatch):
    monkeypatch.setattr(telemetry_client._main, "QILAB_WEB_PROFILER_BASE", "test_trace")
    text = telemetry_client.get("/?tab=profiling").text
    assert "test_trace_durations.csv" in text
    assert "QILAB_WEB_PROFILER_BASE" in text


def test_profiling_tab_survives_missing_files(client, monkeypatch, tmp_path):
    monkeypatch.setenv("QILAB_PROFILER_DIR", str(tmp_path / "nothing-here"))
    response = client.get("/?tab=profiling")
    assert response.status_code == 200
    assert "No profiler data at" in response.text


# --- Data acquisition tab ----------------------------------------------------


def test_data_tab_shows_status_and_record_count(telemetry_client):
    text = telemetry_client.get("/?tab=data").text
    assert "Data acquisition" in text
    assert str(telemetry_client._daq) in text
    assert "3 records" in text
    assert "/telemetry/download/daq" in text


def test_data_tab_reports_disabled_when_no_file(client, monkeypatch, tmp_path):
    monkeypatch.setenv("QILAB_DAQ_PATH", str(tmp_path / "absent.jsonl"))
    text = client.get("/?tab=data").text
    assert "disabled" in text
    assert "No file yet" in text


def test_data_tab_includes_the_preview_element(telemetry_client):
    """The preview is driven by the existing pollLog(), which needs both ids."""
    text = telemetry_client.get("/?tab=data").text
    assert 'id="daq-preview"' in text
    assert 'id="daq-preview-updated"' in text


def test_preview_endpoint_matches_the_logs_payload_shape(telemetry_client):
    payload = telemetry_client.get("/data-acquisition/preview").json()
    assert set(payload) == {"path", "updated", "content"}
    assert payload["path"] == str(telemetry_client._daq)
    assert '"channel":"b"' in payload["content"]
    assert payload["updated"].endswith(" UTC")


def test_preview_endpoint_handles_a_missing_file(client, monkeypatch, tmp_path):
    monkeypatch.setenv("QILAB_DAQ_PATH", str(tmp_path / "absent.jsonl"))
    payload = client.get("/data-acquisition/preview").json()
    assert "not found" in payload["content"].lower()


# --- Downloads ---------------------------------------------------------------


@pytest.mark.parametrize(
    "artifact,media_type",
    [
        ("profiler-durations", "text/csv"),
        ("profiler-events", "text/csv"),
        ("daq", "application/x-ndjson"),
    ],
)
def test_download_serves_the_file_as_an_attachment(telemetry_client, artifact, media_type):
    response = telemetry_client.get(f"/telemetry/download/{artifact}")
    assert response.status_code == 200
    assert media_type in response.headers["content-type"]
    assert "attachment;" in response.headers["content-disposition"]


def test_download_body_matches_the_file_byte_for_byte(telemetry_client):
    """Downloads must stay byte-identical to what qilab wrote -- no reformatting,
    no spreadsheet-injection sanitising -- for reproducibility."""
    expected = (telemetry_client._profiler / "profile_durations.csv").read_bytes()
    assert telemetry_client.get("/telemetry/download/profiler-durations").content == expected


def test_download_filename_carries_the_mtime(telemetry_client):
    disposition = telemetry_client.get(
        "/telemetry/download/profiler-durations"
    ).headers["content-disposition"]
    assert "profile_durations-" in disposition
    assert disposition.rstrip('"').endswith(".csv")


def test_download_404s_when_the_file_is_absent(client, monkeypatch, tmp_path):
    monkeypatch.setenv("QILAB_PROFILER_DIR", str(tmp_path / "nothing-here"))
    assert client.get("/telemetry/download/profiler-durations").status_code == 404


def test_download_rejects_unknown_artifacts(client):
    """A str-Enum path param means Starlette rejects anything else before the
    handler runs, so traversal is structurally impossible rather than filtered."""
    assert client.get("/telemetry/download/bogus").status_code == 422
    assert client.get("/telemetry/download/../../etc/passwd").status_code == 404


def test_download_refuses_a_symlinked_path(client, monkeypatch, tmp_path):
    real = tmp_path / "real_durations.csv"
    real.write_text("Event,Max[ns],Min[ns],Count\n", encoding="utf-8")
    link_dir = tmp_path / "linked"
    link_dir.mkdir()
    (link_dir / "profile_durations.csv").symlink_to(real)

    monkeypatch.setenv("QILAB_PROFILER_DIR", str(link_dir))
    assert client.get("/telemetry/download/profiler-durations").status_code == 404


def test_download_is_not_root_gated(telemetry_client, monkeypatch):
    """Unlike the write routes, these are read-only and must work as any user."""
    monkeypatch.setattr(telemetry_client._main.os, "geteuid", lambda: 1000)
    assert telemetry_client.get("/telemetry/download/daq").status_code == 200

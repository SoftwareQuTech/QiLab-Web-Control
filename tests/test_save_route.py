import yaml


def test_save_happy_path(client, good_doc):
    path = client._config_path
    response = client.post("/config/save", json=good_doc)

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert client._systemctl.restart_calls(), "service should be restarted on success"

    saved = path.read_text(encoding="utf-8")
    assert yaml.safe_load(saved) == good_doc
    assert list(path.parent.glob(".nodes-*")) == [], "temp file should be cleaned up"
    assert (path.parent / (path.name + ".bak")).exists(), "previous config should be backed up"


def test_save_invalid_leaves_file_untouched(client):
    path = client._config_path
    before = path.read_text(encoding="utf-8")
    bad = {"nodes": [{"qubit_arrays": [{"num_qubits": 1, "noise_model": {"type": "ADVANCED", "t2_value": 1}}]}]}

    response = client.post("/config/save", json=bad)

    body = response.json()
    assert body["ok"] is False
    assert "schema" in body["output"].lower()
    assert path.read_text(encoding="utf-8") == before
    assert client._systemctl.restart_calls() == [], "no restart on invalid config"


def test_save_requires_root(client, good_doc, monkeypatch):
    path = client._config_path
    before = path.read_text(encoding="utf-8")
    monkeypatch.setattr(client._main.os, "geteuid", lambda: 1000)

    response = client.post("/config/save", json=good_doc)

    assert response.status_code == 403
    assert response.json()["ok"] is False
    assert path.read_text(encoding="utf-8") == before
    assert client._systemctl.restart_calls() == []


def test_save_reports_restart_failure(client, good_doc):
    path = client._config_path
    client._systemctl.restart_returncode = 1
    client._systemctl.restart_stderr = "unit failed"

    body = client.post("/config/save", json=good_doc).json()

    assert body["ok"] is False
    assert "restart failed" in body["output"].lower()
    # The file is still saved (the failure is the restart, not the write).
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == good_doc


def test_atomic_write_failure_preserves_live_file(client, good_doc, monkeypatch):
    import app.config.pipeline as pipeline

    path = client._config_path
    before = path.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(pipeline.os, "replace", boom)

    body = client.post("/config/save", json=good_doc).json()

    assert body["ok"] is False
    assert "failed to write" in body["output"].lower()
    assert path.read_text(encoding="utf-8") == before
    assert list(path.parent.glob(".nodes-*")) == [], "temp file should be cleaned up on failure"
    assert client._systemctl.restart_calls() == []

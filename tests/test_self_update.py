def test_self_update_requires_root(client, monkeypatch):
    monkeypatch.setattr(client._main.os, "geteuid", lambda: 1000)
    popen_calls = []
    monkeypatch.setattr(client._main.subprocess, "Popen", lambda *a, **k: popen_calls.append(a))

    response = client.post(
        "/self-update",
        data={"version": "0.3.0"},
        files={"wheel": ("qilab_web_control-0.3.0-py3-none-any.whl", b"fake wheel bytes")},
    )

    assert response.status_code == 200  # 303 -> followed to "/"
    assert "must run as root" in response.text
    assert popen_calls == [], "self_update.sh must not be launched without root"


def test_self_update_launches_detached_isolated_script(client, monkeypatch, tmp_path):
    popen_calls = []
    monkeypatch.setattr(client._main.subprocess, "Popen", lambda *a, **k: popen_calls.append(a))
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(client._main, "QILAB_WEB_SELF_UPDATE_UPLOAD_DIR", upload_dir)

    response = client.post(
        "/self-update",
        data={"version": "0.3.0"},
        files={"wheel": ("qilab_web_control-0.3.0-py3-none-any.whl", b"fake wheel bytes")},
    )

    assert response.status_code == 200  # 303 -> followed to "/"
    assert "started in the background" in response.text

    assert len(popen_calls) == 1
    (cmd,) = popen_calls[0]
    # Launched via systemd-run so it survives this service's own restart
    # (a plain child process would share its cgroup and be killed by it).
    assert cmd[0] == "systemd-run"
    assert "--unit=qilab-web-control-selfupdate" in cmd
    assert "--collect" in cmd
    assert cmd[-2] == "0.3.0"
    assert cmd[-1].endswith(".whl")
    assert upload_dir.exists()


def test_self_update_logs_endpoint(client, monkeypatch, tmp_path):
    log_path = tmp_path / "self_update.log"
    log_path.write_text("line one\nline two\n", encoding="utf-8")
    monkeypatch.setattr(client._main, "QILAB_WEB_SELF_UPDATE_LOG", log_path)

    response = client.get("/self-update/logs")

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "line one\nline two"
    assert body["path"] == str(log_path)


def test_installed_version_shown_on_control_tab(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Installed version:" in response.text

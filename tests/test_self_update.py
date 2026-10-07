# Copyright (c) 2026, Quantum Internet Alliance, Delft University of Technology, Milen Kolev, Stephanie Wehner
# SPDX-License-Identifier: BSD-3-Clause-Clear

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


def test_self_update_preserves_the_wheel_filename(client, monkeypatch, tmp_path):
    """pip parses wheel filenames per PEP 427 and rejects anything else outright.

    Uniqueness therefore has to live on a containing directory: prefixing or
    suffixing the file itself makes every single update fail inside pip.
    """
    from pathlib import Path

    popen_calls = []
    monkeypatch.setattr(client._main.subprocess, "Popen", lambda *a, **k: popen_calls.append(a))
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(client._main, "QILAB_WEB_SELF_UPDATE_UPLOAD_DIR", upload_dir)

    wheel_name = "qilab_web_control-0.4.1-py3-none-any.whl"
    client.post(
        "/self-update",
        data={"version": "0.4.1"},
        files={"wheel": (wheel_name, b"fake wheel bytes")},
    )

    wheel_path = Path(popen_calls[0][0][-1])
    assert wheel_path.name == wheel_name, "the wheel's own filename must reach pip verbatim"
    assert wheel_path.parent.parent == upload_dir
    assert wheel_path.read_bytes() == b"fake wheel bytes"


def test_self_update_rejects_a_non_wheel_upload(client, monkeypatch, tmp_path):
    popen_calls = []
    monkeypatch.setattr(client._main.subprocess, "Popen", lambda *a, **k: popen_calls.append(a))
    monkeypatch.setattr(client._main, "QILAB_WEB_SELF_UPDATE_UPLOAD_DIR", tmp_path / "uploads")

    response = client.post(
        "/self-update",
        data={"version": "0.4.1"},
        files={"wheel": ("qilab_web_control-0.4.1.tar.gz", b"not a wheel")},
    )

    assert response.status_code == 200
    assert "is not a .whl file" in response.text
    assert popen_calls == [], "a non-wheel upload must not reach the update script"


def test_self_update_prunes_old_upload_dirs(client, monkeypatch, tmp_path):
    monkeypatch.setattr(client._main.subprocess, "Popen", lambda *a, **k: None)
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    stale = [upload_dir / f"0.0.{i}-old" for i in range(5)]
    for index, path in enumerate(stale):
        path.mkdir()
        # Distinct mtimes so "newest" is unambiguous; index 0 is the oldest.
        os_utime(path, index)
    monkeypatch.setattr(client._main, "QILAB_WEB_SELF_UPDATE_UPLOAD_DIR", upload_dir)

    client.post(
        "/self-update",
        data={"version": "0.4.1"},
        files={"wheel": ("qilab_web_control-0.4.1-py3-none-any.whl", b"fake wheel bytes")},
    )

    # 3 newest kept, plus the one just created for this upload.
    assert sorted(p.name for p in upload_dir.iterdir() if p.is_dir())[:3] == [
        "0.0.2-old",
        "0.0.3-old",
        "0.0.4-old",
    ]
    assert not stale[0].exists() and not stale[1].exists()


def os_utime(path, index):
    import os

    stamp = 1_000_000 + index * 10
    os.utime(path, (stamp, stamp))


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

# Copyright (c) 2026, Quantum Internet Alliance, Delft University of Technology, Milen Kolev, Stephanie Wehner
# SPDX-License-Identifier: BSD-3-Clause-Clear

"""Guards on the root-run write paths: version input, upload dirs, CSRF, YAML."""

import os

import pytest

from app.config.emitter import emit_yaml

WHEEL = ("qilab_web_control-0.4.1-py3-none-any.whl", b"fake wheel bytes")


@pytest.mark.parametrize(
    "version", ["../../..", "0.4.1/../../x", "..", "0.4.1 ; rm -rf /", "", "x" * 100, ".hidden"]
)
def test_self_update_rejects_unsafe_versions(client, monkeypatch, tmp_path, version):
    popen_calls = []
    monkeypatch.setattr(client._main.subprocess, "Popen", lambda *a, **k: popen_calls.append(a))
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(client._main, "QILAB_WEB_SELF_UPDATE_UPLOAD_DIR", upload_dir)

    response = client.post("/self-update", data={"version": version}, files={"wheel": WHEEL})

    assert response.status_code in (200, 422)
    assert popen_calls == [], "an unsafe version must never reach self_update.sh"
    assert not upload_dir.exists() or not any(upload_dir.iterdir())


def test_binary_update_rejects_unsafe_versions(client, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(client._main, "_run_update_script", lambda **k: calls.append(k))
    monkeypatch.setattr(client._main, "QILAB_UPLOAD_DIR", tmp_path / "uploads")

    response = client.post(
        "/update",
        data={"version": "../../etc"},
        files={"tarball": ("qilab.tar.gz", b"fake tarball")},
    )

    assert response.status_code == 200
    assert "Invalid version" in response.text
    assert calls == []


@pytest.mark.parametrize("version", ["0.4.2", "v0.4.2", "0.5.0rc1", "1.0.0.dev3"])
def test_self_update_accepts_normal_versions(client, monkeypatch, tmp_path, version):
    popen_calls = []
    monkeypatch.setattr(client._main.subprocess, "Popen", lambda *a, **k: popen_calls.append(a))
    monkeypatch.setattr(client._main, "QILAB_WEB_SELF_UPDATE_UPLOAD_DIR", tmp_path / "uploads")

    client.post("/self-update", data={"version": version}, files={"wheel": WHEEL})

    assert len(popen_calls) == 1


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX permissions only")
def test_self_update_refuses_a_world_writable_upload_dir(client, monkeypatch, tmp_path):
    popen_calls = []
    monkeypatch.setattr(client._main.subprocess, "Popen", lambda *a, **k: popen_calls.append(a))
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    upload_dir.chmod(0o777)
    monkeypatch.setattr(client._main, "QILAB_WEB_SELF_UPDATE_UPLOAD_DIR", upload_dir)

    response = client.post("/self-update", data={"version": "0.4.1"}, files={"wheel": WHEEL})

    assert "unsafe upload directory" in response.text
    assert popen_calls == []


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX symlinks only")
def test_self_update_refuses_a_symlinked_upload_dir(client, monkeypatch, tmp_path):
    popen_calls = []
    monkeypatch.setattr(client._main.subprocess, "Popen", lambda *a, **k: popen_calls.append(a))
    real = tmp_path / "elsewhere"
    real.mkdir()
    link = tmp_path / "uploads"
    link.symlink_to(real)
    monkeypatch.setattr(client._main, "QILAB_WEB_SELF_UPDATE_UPLOAD_DIR", link)

    response = client.post("/self-update", data={"version": "0.4.1"}, files={"wheel": WHEEL})

    assert "unsafe upload directory" in response.text
    assert popen_calls == []


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Referer": "https://evil.example/page"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_cross_origin_posts_are_refused(client, monkeypatch, headers):
    calls = []
    monkeypatch.setattr(client._main, "_run_systemctl", lambda *a: calls.append(a))

    response = client.post("/stop", headers=headers, follow_redirects=False)

    assert response.status_code == 403
    assert calls == []


def test_same_origin_posts_are_allowed(client):
    response = client.post(
        "/stop", headers={"Origin": "http://testserver"}, follow_redirects=False
    )
    assert response.status_code == 303


def test_cross_origin_gets_are_unaffected(client):
    assert client.get("/", headers={"Origin": "https://evil.example"}).status_code == 200


@pytest.mark.parametrize("name", ["alice\nsystem:", "alice\r\n  x: 1", "a\x00b"])
def test_emitter_refuses_line_breaks_in_values(name):
    with pytest.raises(ValueError):
        emit_yaml({"nodes": [{"name": name}]})

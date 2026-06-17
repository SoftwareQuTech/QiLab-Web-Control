import subprocess

import pytest


def _cp(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(list(args), returncode, stdout=stdout, stderr=stderr)


class FakeSystemctl:
    """Stand-in for app.main._run_systemctl. Records calls; never touches systemd."""

    def __init__(self):
        self.calls = []
        self.restart_returncode = 0
        self.restart_stderr = ""

    def __call__(self, *args):
        self.calls.append(tuple(args))
        verb = args[0] if args else ""
        if verb == "show":
            # No unit environment / user -> path resolution falls back to env.
            return _cp(args, returncode=1)
        if verb == "is-active":
            return _cp(args, stdout="active")
        if verb == "status":
            return _cp(args, stdout="status ok")
        if verb == "restart":
            return _cp(args, self.restart_returncode, stderr=self.restart_stderr)
        return _cp(args)

    def restart_calls(self):
        return [c for c in self.calls if c and c[0] == "restart"]


GOOD_YAML = (
    "nodes:\n"
    "  - qubit_arrays:\n"
    "      - num_qubits: 1\n"
    "        noise_model:\n"
    "          type: BASIC\n"
    "          t2_value: 1000\n"
)


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    """Each test starts with a clean schema cache (lazily reloads the vendored copy)."""
    from app.config import schema as schema_module

    schema_module._cache = None
    yield
    schema_module._cache = None


@pytest.fixture
def good_doc():
    return {
        "entanglement_noise_model": {"type": "BASIC", "t2_value": 1000},
        "nodes": [
            {
                "name": "alice",
                "node_type": "NV_CENTER",
                "qubit_arrays": [
                    {
                        "num_qubits": 2,
                        "noise_model": {
                            "type": "ADVANCED",
                            "t1_value": 5000,
                            "t2_value": 2000,
                            "gate_error_rate": 0.001,
                        },
                    }
                ],
            }
        ],
    }


@pytest.fixture
def tmp_config(tmp_path):
    path = tmp_path / "nodes.yaml"
    path.write_text(GOOD_YAML, encoding="utf-8")
    return path


@pytest.fixture
def fake_systemctl():
    return FakeSystemctl()


@pytest.fixture
def client(monkeypatch, tmp_config, fake_systemctl):
    import app.main as main

    monkeypatch.setattr(main, "_run_systemctl", fake_systemctl)
    monkeypatch.setattr(main.os, "geteuid", lambda: 0)
    monkeypatch.setenv("QILAB_CONFIG_PATH", str(tmp_config))
    monkeypatch.delenv("QILAB_WEB_SCHEMA_PATH", raising=False)

    from fastapi.testclient import TestClient

    test_client = TestClient(main.app)
    test_client._main = main  # convenience handle for tests
    test_client._systemctl = fake_systemctl
    test_client._config_path = tmp_config
    return test_client

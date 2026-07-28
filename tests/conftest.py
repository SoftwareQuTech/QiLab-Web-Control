import subprocess

import pytest


def _cp(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(list(args), returncode, stdout=stdout, stderr=stderr)


def make_systemctl(environment="", user="", environment_files=(), protect_home=""):
    """Fake systemctl returning canned ``show -p <prop>`` output.

    ``environment_files`` is a list of paths rendered in systemd's own
    ``EnvironmentFiles=/a (ignore_errors=no)`` form, so the parser under test sees
    exactly what the real property looks like.
    """

    def run(*args):
        if len(args) >= 3 and args[0] == "show" and args[1] == "-p":
            prop = args[2]
            if prop == "Environment":
                return _cp(args, stdout=f"Environment={environment}" if environment else "")
            if prop == "User":
                return _cp(args, stdout=f"User={user}" if user else "")
            if prop == "EnvironmentFiles":
                rendered = " ".join(f"{path} (ignore_errors=no)" for path in environment_files)
                return _cp(args, stdout=f"EnvironmentFiles={rendered}" if rendered else "")
            if prop == "ProtectHome":
                return _cp(args, stdout=f"ProtectHome={protect_home}" if protect_home else "")
        return _cp(args, returncode=1)

    return run


class FakeSystemctl:
    """Stand-in for app.main._run_systemctl. Records calls; never touches systemd."""

    def __init__(self):
        self.calls = []
        self.restart_returncode = 0
        self.restart_stderr = ""
        # Left empty, every `show` still returns rc=1 exactly as before.
        self.unit_environment = {}
        self.environment_files = []
        self.protect_home = ""
        self.active_state = "active"

    def __call__(self, *args):
        self.calls.append(tuple(args))
        verb = args[0] if args else ""
        if verb == "show":
            prop = args[2] if len(args) >= 3 and args[1] == "-p" else ""
            if prop == "Environment" and self.unit_environment:
                body = " ".join(f'{k}="{v}"' for k, v in self.unit_environment.items())
                return _cp(args, stdout="Environment=" + body)
            if prop == "EnvironmentFiles" and self.environment_files:
                rendered = " ".join(f"{p} (ignore_errors=no)" for p in self.environment_files)
                return _cp(args, stdout="EnvironmentFiles=" + rendered)
            if prop == "ProtectHome" and self.protect_home:
                return _cp(args, stdout="ProtectHome=" + self.protect_home)
            # No unit environment / user -> path resolution falls back to env.
            return _cp(args, returncode=1)
        if verb == "is-active":
            return _cp(args, stdout=self.active_state)
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
    "        single_gate_delay_ns: 0\n"
    "        qubit_decoherence_noise_model:\n"
    "          type: BASIC\n"
    "          t2_value: 1000\n"
    "        gate_noise_model:\n"
    "          type: DEPOLARIZING\n"
    "          depolarizing_probability: 0.0\n"
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
        "nodes": [
            {
                "name": "alice",
                "node_type": "NV_CENTER",
                "two_qubit_gate_noise_model": {
                    "type": "DEPOLARIZING",
                    "depolarizing_probability": 0.02,
                },
                "qubit_arrays": [
                    {
                        "num_qubits": 2,
                        "single_gate_delay_ns": 200,
                        "qubit_decoherence_noise_model": {
                            "type": "ADVANCED",
                            "t1_value": 5000,
                            "t2_value": 2000,
                            "gate_error_rate": 0.001,
                        },
                        "gate_noise_model": {
                            "type": "DEPOLARIZING",
                            "depolarizing_probability": 0.001,
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
def tmp_profiler(tmp_path):
    """Profiler CSVs in the 5-column (Avg[ns]) form qilab writes today."""
    directory = tmp_path / "profiler"
    directory.mkdir()
    (directory / "profile_durations.csv").write_text(
        "Event,Max[ns],Min[ns],Count,Avg[ns]\n"
        "send,900,100,3,400\n"
        "recv,120,110,7,115\n",
        encoding="utf-8",
    )
    (directory / "profile_events.csv").write_text(
        "Message,TimestampNs\nstarted,0\n", encoding="utf-8"
    )
    return directory


@pytest.fixture
def tmp_profiler_legacy(tmp_path):
    """The 4-column form, so the two repos need not deploy in lockstep."""
    directory = tmp_path / "profiler_legacy"
    directory.mkdir()
    (directory / "profile_durations.csv").write_text(
        "Event,Max[ns],Min[ns],Count\nsend,900,100,3\n", encoding="utf-8"
    )
    return directory


@pytest.fixture
def tmp_daq(tmp_path):
    directory = tmp_path / "daq"
    directory.mkdir()
    path = directory / "data_acquisition.jsonl"
    path.write_text(
        '{"ts_us":1,"channel":"a"}\n{"ts_us":2,"channel":"a"}\n{"ts_us":3,"channel":"b"}\n',
        encoding="utf-8",
    )
    return path


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

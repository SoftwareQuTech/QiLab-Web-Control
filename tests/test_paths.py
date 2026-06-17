import subprocess

from app.config import paths


def _cp(args, returncode=0, stdout=""):
    return subprocess.CompletedProcess(list(args), returncode, stdout=stdout, stderr="")


def make_systemctl(environment="", user=""):
    """Fake systemctl returning canned ``show -p Environment`` / ``-p User`` output."""

    def run(*args):
        if len(args) >= 3 and args[0] == "show" and args[1] == "-p":
            prop = args[2]
            if prop == "Environment":
                return _cp(args, stdout=f"Environment={environment}" if environment else "")
            if prop == "User":
                return _cp(args, stdout=f"User={user}" if user else "")
        return _cp(args, returncode=1)

    return run


def test_unit_config_path_wins():
    res = paths.resolve_config(
        "qilab.service",
        environ={"QILAB_CONFIG_PATH": "/server/should-not-win.yaml"},
        systemctl=make_systemctl(environment="QILAB_CONFIG_PATH=/srv/nodes.yaml"),
    )
    assert str(res.path) == "/srv/nodes.yaml"
    assert "qilab.service env QILAB_CONFIG_PATH" in res.source


def test_unit_config_dir():
    res = paths.resolve_config(
        "qilab.service",
        environ={},
        systemctl=make_systemctl(environment="QILAB_CONFIG_DIR=/etc/qilab"),
    )
    assert str(res.path) == "/etc/qilab/nodes.yaml"
    assert "QILAB_CONFIG_DIR" in res.source


def test_falls_back_to_server_env():
    res = paths.resolve_config(
        "qilab.service",
        environ={"QILAB_CONFIG_DIR": "/opt/cfg"},
        systemctl=make_systemctl(),  # empty unit env
    )
    assert str(res.path) == "/opt/cfg/nodes.yaml"
    assert res.source.startswith("server env")


def test_deprecated_config_file_alias_last():
    res = paths.resolve_config(
        "qilab.service",
        environ={"QILAB_CONFIG_FILE": "/etc/qilab/nodes.yaml"},
        systemctl=make_systemctl(),
    )
    assert str(res.path) == "/etc/qilab/nodes.yaml"
    assert "deprecated" in res.source.lower()


def test_default_for_root_user():
    res = paths.resolve_config(
        "qilab.service",
        environ={},
        systemctl=make_systemctl(user="root"),
    )
    assert str(res.path) == "/root/.qilab/config/nodes.yaml"
    assert res.source.startswith("default")


def test_default_home_when_nothing_set():
    res = paths.resolve_config("qilab.service", environ={}, systemctl=make_systemctl())
    assert str(res.path).endswith(".qilab/config/nodes.yaml")


def test_quoted_environment_value_is_unquoted():
    res = paths.resolve_config(
        "qilab.service",
        environ={},
        systemctl=make_systemctl(environment='QILAB_CONFIG_PATH="/srv/with space/nodes.yaml"'),
    )
    assert str(res.path) == "/srv/with space/nodes.yaml"

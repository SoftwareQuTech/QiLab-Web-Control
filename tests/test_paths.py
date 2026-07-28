from app.config import paths
from conftest import make_systemctl


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


# --- EnvironmentFile= support -------------------------------------------------
# systemctl show -p Environment reports ONLY inline Environment= directives; it
# never expands EnvironmentFile=. The deployed image puts qilab's paths in a
# shared /etc/default/qilab-global-env, so without this the UI silently resolves
# to the wrong tier and reads/edits a different file than the simulator.


def test_environment_file_is_read(tmp_path):
    env_file = tmp_path / "qilab-global-env"
    env_file.write_text("QILAB_CONFIG_DIR=/etc/qilab\n", encoding="utf-8")

    res = paths.resolve_config(
        "qilab.service",
        environ={},
        systemctl=make_systemctl(environment_files=[env_file]),
    )
    assert str(res.path) == "/etc/qilab/nodes.yaml"
    assert "qilab.service env QILAB_CONFIG_DIR" in res.source
    assert str(env_file) in res.source


def test_inline_environment_overrides_environment_file(tmp_path):
    """systemd applies Environment= after EnvironmentFile=, so inline wins."""
    env_file = tmp_path / "qilab-global-env"
    env_file.write_text("QILAB_CONFIG_DIR=/from/file\n", encoding="utf-8")

    res = paths.resolve_config(
        "qilab.service",
        environ={},
        systemctl=make_systemctl(
            environment="QILAB_CONFIG_DIR=/from/inline",
            environment_files=[env_file],
        ),
    )
    assert str(res.path) == "/from/inline/nodes.yaml"
    assert "Environment=" in res.source


def test_environment_file_beats_server_env(tmp_path):
    """The qilab unit's environment outranks the web server's, file or not."""
    env_file = tmp_path / "qilab-global-env"
    env_file.write_text("QILAB_CONFIG_DIR=/unit/wins\n", encoding="utf-8")

    res = paths.resolve_config(
        "qilab.service",
        environ={"QILAB_CONFIG_DIR": "/server/loses"},
        systemctl=make_systemctl(environment_files=[env_file]),
    )
    assert str(res.path) == "/unit/wins/nodes.yaml"


def test_later_environment_file_wins(tmp_path):
    first = tmp_path / "a-env"
    first.write_text("QILAB_CONFIG_DIR=/first\n", encoding="utf-8")
    second = tmp_path / "b-env"
    second.write_text("QILAB_CONFIG_DIR=/second\n", encoding="utf-8")

    env = paths.unit_environment(
        "qilab.service", make_systemctl(environment_files=[first, second])
    )
    assert env.get("QILAB_CONFIG_DIR") == "/second"


def test_missing_environment_file_is_not_fatal(tmp_path):
    res = paths.resolve_config(
        "qilab.service",
        environ={"QILAB_CONFIG_DIR": "/server/fallback"},
        systemctl=make_systemctl(environment_files=[tmp_path / "absent"]),
    )
    assert str(res.path) == "/server/fallback/nodes.yaml"


def test_read_environment_file_parses_comments_and_quotes(tmp_path):
    env_file = tmp_path / "env"
    env_file.write_text(
        "\n"
        "# a comment\n"
        "QILAB_DAQ=1\n"
        '  QUOTED="/has space/x"  \n'
        "SINGLE='/single'\n"
        "not a key=value\n"
        "NO_EQUALS\n",
        encoding="utf-8",
    )
    parsed = paths.read_environment_file(env_file)
    assert parsed == {
        "QILAB_DAQ": "1",
        "QUOTED": "/has space/x",
        "SINGLE": "/single",
    }


def test_environment_files_parses_systemd_rendering():
    systemctl = make_systemctl(environment_files=["/etc/default/one", "/etc/default/two"])
    assert [str(p) for p in paths.environment_files("qilab.service", systemctl)] == [
        "/etc/default/one",
        "/etc/default/two",
    ]

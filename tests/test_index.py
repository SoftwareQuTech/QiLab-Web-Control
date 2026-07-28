def test_config_tab_renders_structured_form(client):
    response = client.get("/?tab=config")
    assert response.status_code == 200
    text = response.text
    assert 'id="config-form"' in text
    assert 'id="qilab-schema"' in text
    assert "/static/config.js" in text
    # Resolved path + its source are shown so the operator can confirm the target.
    assert str(client._config_path) in text
    assert "server env QILAB_CONFIG_PATH" in text


def test_page_is_never_cached(client):
    # The schema and the current config are inlined into this page, so a cached
    # copy shows an operator a config that is no longer the one on disk.
    for url in ("/", "/?tab=config"):
        assert client.get(url).headers["cache-control"] == "no-store"


def test_config_tab_inlines_the_live_schema(client):
    schema = client._main.config_schema.get_schema()[0]
    text = client.get("/?tab=config").text
    # Every field the editor can render has to reach the browser in the page, so
    # a schema reload is visible after a plain refresh.
    for key in schema["properties"]:
        assert key in text


def test_control_tab_still_works(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "systemctl status" in response.text


def test_schema_reload(client):
    response = client.post("/config/schema/reload")  # 303 -> followed to config tab
    assert response.status_code == 200
    assert "Schema reloaded from" in response.text


def test_schema_unavailable_falls_back_to_readonly(client, monkeypatch):
    monkeypatch.setattr(
        client._main.config_schema, "get_schema", lambda *a, **k: (None, "(none)", "boom")
    )
    response = client.get("/?tab=config")
    assert response.status_code == 200
    text = response.text
    assert "structured editing is disabled" in text
    assert 'id="config-form"' not in text

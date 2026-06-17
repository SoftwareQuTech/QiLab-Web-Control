# Changelog

All notable changes to this project are documented in this file.

## [0.2.0] - 2026-06-17
### Added
- Schema-driven structured config editor: the Config tab now renders typed fields,
  dropdowns for node and noise-model types, and add/remove controls for nodes and
  qubit arrays, driven by qilab's `config.schema.json`.
- Edits are validated against the schema (via `jsonschema`) before saving; invalid
  configs are rejected without touching the live file or restarting the service.
- `POST /config/schema/reload` and a "Reload schema" button to pick up schema
  changes without redeploying.
- Config path is now resolved the way qilab resolves it (prefers the qilab unit's
  `QILAB_CONFIG_PATH` / `QILAB_CONFIG_DIR`), so the UI edits the file the simulator
  actually loads. The resolved path and its source are shown in the UI.

### Changed
- Saves emit canonical qilab-dialect YAML and write atomically (temp file +
  `os.replace`), keeping a best-effort `nodes.yaml.bak` backup.
- `QILAB_CONFIG_FILE` is deprecated (qilab never read it); kept only as a
  last-resort alias. Prefer `QILAB_CONFIG_DIR` / `QILAB_CONFIG_PATH`.

### Note
- The first structured save rewrites a legacy-format config file canonically.

## [0.1.0] - 2026-03-03
### Added
- Initial release.

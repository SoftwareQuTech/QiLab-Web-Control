# Changelog

All notable changes to this project are documented in this file.

## [0.4.3] - 2026-10-07
### Security
- **The `version` form field could escape the release directory as root.**
  `/self-update` and `/update` passed it unchecked into temp-file prefixes and to
  the update scripts, and `self_update.sh` builds and `rm -rf`s
  `releases/<version>`, so a value such as `../../..` pointed that at `/`. Both
  routes and `self_update.sh` now accept only dot-separated alphanumerics
  (`0.4.2`, `v0.4.2`, `0.5.0rc1`).
- **Cross-site requests can no longer drive the UI.** Every POST runs as root, and
  any page an operator had open could submit a form or a no-cors `fetch` to the
  device, including a wheel upload to `/self-update`. Non-GET requests whose
  `Origin`/`Referer` does not match `Host`, or that carry
  `Sec-Fetch-Site: cross-site`, are now refused with 403. Clients that send
  neither header, like `curl`, are unaffected.
- Upload directories must be real directories owned by the service's user and
  not group- or world-writable. The defaults live under `/tmp`, where a local
  user could pre-create them and swap an upload before the root-run script read
  it.
- The config emitter refuses values containing a line break or NUL. The qilab
  dialect has no quoting, so a node name with a newline was written out as extra
  config keys.

### Changed
- Released under the Clear BSD License (`BSD-3-Clause-Clear`). Added `LICENSE`
  and a copyright and SPDX header to every source file; the wheel and sdist
  ship `LICENSE`.
- `QILAB_UPDATE_SCRIPT` now defaults to `/opt/qilab/scripts/update_release.sh`,
  matching the reference unit, instead of a developer's home directory.

## [0.4.2] - 2026-08-06
### Fixed
- **Self-update could never install a wheel.** `/self-update` staged the upload
  with `tempfile.mkstemp`, producing
  `qilab-web-control-<version>-<token>-<original name>.whl`. pip parses wheel
  filenames per PEP 427 and rejects anything else before opening the file, so
  every attempt failed with "is not a valid wheel filename" — including on
  0.4.1, where the layout itself was already correct. The upload now goes into a
  unique *directory* and keeps its filename verbatim; non-`.whl` uploads are
  refused up front, and `self_update.sh` validates the filename itself so a
  future caller that renames it fails with an explanation rather than pip's
  error. Upload directories are pruned to the newest three.
- Self-update no longer misreports why an install failed. A pip failure in the
  offline path was reported as "dependencies have changed", followed by a
  pointless networked retry and a closing suggestion to reflash the image — none
  of which applied, since `--no-deps` resolves nothing and can therefore only
  fail on the wheel itself. Only an install that succeeds but then fails to
  import triggers the networked retry now.

### Added
- `QILAB_WEB_CONTROL_LOG_DIR` overrides the self-update log directory, so the
  script can be exercised outside a real deployment.

## [0.4.1] - 2026-07-28
### Changed
- **Deployment is now venv-free.** A release is a `pip install --target`
  directory that the unit puts on `PYTHONPATH`, not a virtualenv:
  `python3 -m venv` needs the `python3-venv` package, which is unavailable on
  the legacy RT images. `pip` is the only requirement, and `--target` sidesteps
  PEP 668 so no `--break-system-packages` is involved. The `current` symlink and
  atomic-swap layout are unchanged; `releases/<version>/venv` becomes
  `releases/<version>/site-packages`. This is now the only supported layout;
  devices on an older one need a one-time migration, see
  [docs/migration.md](docs/migration.md).
- **Deployment assets ship inside the wheel** under `app/deploy/`
  (`self_update.sh` plus a reference systemd unit), so what lands on a device
  always matches the version installed and the rpi-img-gen layer no longer
  carries its own copies. `QILAB_WEB_SELF_UPDATE_SCRIPT` now defaults to
  `app/deploy/self_update.sh`. The example unit under `examples/service/` and
  the top-level `scripts/` directory are gone: they were a third divergent copy
  of the unit, and one of them advertised a mode where self-update could not
  work at all.

### Added
- **Self-update preflight.** The script now refuses to write anything unless the
  install is genuinely swappable: pip usable, `current` a symlink rather than a
  real directory, and the unit reaching the app through `current` (checked
  against both `ExecStart` and the environment, including `EnvironmentFile=`
  contents). Previously, a system-wide `pip3 install --break-system-packages`
  deployment -- what the image actually shipped -- would build a new release,
  flip a symlink nothing read, restart successfully and log "Update successful"
  while still running the old code.
- **Offline self-update.** A new release is seeded by copying the live release's
  `site-packages`, then the wheel is installed with `--no-deps`, so an update
  needs no PyPI access. If a release changed dependencies the health check
  catches it and the script retries a full networked install, aborting with the
  live release intact if that also fails.
- Self-update housekeeping after a verified-live swap: `self_update.sh` is
  replanted from the new wheel, a changed unit file is flagged (not applied),
  and releases older than the previous one are pruned.
- A same-version reinstall builds into `releases/<version>-r2` instead of
  deleting the directory it is running from.
- [docs/image-layer.md](docs/image-layer.md) documents the rpi-img-gen layer
  contract and a working `customize-hooks` implementation.

## [0.4.0] - 2026-07-28
### Added
- **Profiling tab**: qilab's `profile_durations.csv` and `profile_events.csv`
  rendered as click-to-sort tables, with each file's resolved path, the env var
  it came from, its size and modification time, and a download link. Sorting is
  progressive enhancement in a single flat `app/static/telemetry.js` -- no
  charting library and no build step. Column types are inferred from the data,
  so qilab's new `Avg[ns]` column sorts numerically with no change here.
- **Data acquisition tab**: whether the sink is enabled, the resolved JSONL
  path, size, record count and last-modified time, a preview of the most recent
  records, and a download link.
- `GET /telemetry/download/{artifact}` for `profiler-durations`,
  `profiler-events` and `daq`. The path parameter is a `str`-Enum, so no
  caller-supplied path segment ever reaches the filesystem; symlinked paths and
  non-regular files are refused. Downloads are byte-identical to what qilab
  wrote, and the filename carries the file's mtime because the profiler CSVs are
  overwritten on every run.
- `GET /data-acquisition/preview`, returning the same `{path, updated, content}`
  shape as `/logs` so the existing `pollLog()` drives it with no new JavaScript.
- `app/telemetry.py`, mirroring qilab's own path resolvers -- including qilab's
  asymmetry, where `QILAB_PROFILER_PATH` is a *base* without extension while
  `QILAB_DAQ_PATH` is a complete filename.
- Self-diagnosing notices for the three ways this silently shows nothing useful:
  profiler data that predates the current run (it is written only at shutdown,
  and truncated each time); a `QILAB_WEB_PROFILER_BASE` that does not match the
  base compiled into the running qilab (the page names the files that *are*
  present); and `QILAB_DAQ=1` resolving under `$HOME` while the qilab unit sets
  `ProtectHome=true`, where the sink can never write.
- New env vars: `QILAB_WEB_PROFILER_BASE`, `QILAB_WEB_DAQ_PREVIEW_LINES`,
  `QILAB_WEB_DAQ_COUNT_MAX_BYTES`.

### Fixed
- **`EnvironmentFile=` was invisible to path resolution.** `systemctl show -p
  Environment` reports only inline `Environment=` directives; systemd expands
  `EnvironmentFile=` at exec time. The deployed image points both units at a
  shared `/etc/default/qilab-global-env`, so every path resolved from the qilab
  unit -- the existing **config path included** -- silently fell through to the
  wrong tier, and the UI could read or edit a different file than the simulator,
  with no error shown. `app/config/paths.py` now reads `EnvironmentFiles` and
  layers those values *under* `Environment=`, matching systemd's precedence, and
  reports which source a value came from.
- The vendored `app/config/config.schema.json` had drifted badly from qilab's
  schema (it still described a removed top-level `entanglement_noise_model`).
  Re-vendored, which required updating the test fixtures and
  `schema.default_document()` to the current config format -- the qubit-array
  keys `qubit_decoherence_noise_model`, `gate_noise_model` and
  `single_gate_delay_ns`, and the node-level `two_qubit_gate_noise_model`.
- **The structured editor kept rendering the previous config format after the
  schema was re-vendored**, and looked for all the world like a caching problem:
  the new schema *was* inlined in the page, but `app/static/config.js` named
  qilab's fields directly (`noise_model`, a top-level `entanglement_noise_model`,
  a `$defs.entanglement_noise_model` that no longer exists), so it showed the old
  form regardless. Worse, its Save button then POSTed that old shape, which the
  schema now rejects. The renderer walks the schema instead: objects, arrays,
  `$ref`, `oneOf` discriminated unions, enums and scalars each have one generic
  case, and serialization is the inverse walk of the same tree, so `system`,
  `entanglement_links`, `two_qubit_gate_noise_model`, `single_gate_delay_ns` and
  the split qubit-array noise models appear with no JavaScript change -- as do
  the next ones. Optional sections get a presence toggle, optional booleans a
  three-state select (true / false / schema default), and blank optional fields
  are omitted so qilab's own defaults still apply. Keys the current schema does
  not define are listed in a notice rather than silently dropped on save.
- `tests/test_schema_drift.py` fails if either generic file names a schema field
  again, since nothing else does: the form still renders and the save still
  succeeds while showing an obsolete format.
- **Invisible download buttons.** `.btn` sets `color: #fff` and left the
  background to a colour modifier, so the four bare `.btn` anchors -- both
  profiler "Download CSV" links, the Profiling "Reload" link, and the DAQ
  "Download JSONL" link -- were white text on a white card, underlined and
  ignoring their padding. `.btn` now carries a neutral background of its own
  (6.05:1 against white) plus the `display`/`text-decoration` an anchor needs,
  so a missing modifier can no longer make a control unreadable.
  `tests/test_styles.py` pins that, and that every modifier the template uses
  exists.
- Reporting "disabled" for the DAQ sink next to a populated file and a working
  download button. An existing data file is now treated as evidence that
  outranks the unit-environment inference, which is what happens when qilab is
  run outside systemd.

### Changed
- The emitter's key order is derived from the schema (`emitter.key_order`)
  instead of a hand-maintained list, which had gone stale in the same way as the
  editor -- it still ranked `noise_model` and `single_gate_rotation`. Unknown keys
  keep their insertion order and sort last, as before.
- The page is served `Cache-Control: no-store`. It carries live state -- service
  status, the current config, the inlined schema -- but previously went out with
  no freshness information at all, so a browser could legitimately answer a
  same-URL navigation from cache, including the redirect after "Reload schema".
- Tabs are validated server-side against a known list; an unknown `?tab=` value
  now renders Control *and* highlights it. Previously the nav's `!= 'config'`
  test meant an unknown tab rendered the Control body with no link highlighted.
- `pollLog()` takes an optional interval. The two log pollers keep their 100 ms
  cadence; the telemetry preview uses 2000 ms, since it is subprocess-backed.
- The telemetry snapshot is computed only on the two tabs that display it, so
  the Control tab does not pay for three file stats and extra `systemctl` calls
  on every render.

## [0.3.0] - 2026-07-21
### Added
- `POST /update` now reloads the config schema cache right after the update
  script runs, and reports the reload result alongside the binary-update
  result, instead of requiring a separate manual "Reload schema" click.
- "Start from default config" action in the Config tab, giving an explicit,
  safe way to recover the structured editor when the current `nodes.yaml` no
  longer validates against a newer schema.
- `app/config/VENDORED_SCHEMA_VERSION` manifest plus a `verify:vendored_schema`
  CI job that fails the release if the vendored `config.schema.json` fallback
  drifts from the version recorded in the manifest.

### Changed
- Deployment instructions now set
  `QILAB_WEB_SCHEMA_PATH=/opt/qilab/current/config.schema.json`: qilab's
  release tarball ships `config.schema.json` next to the binary and
  `update_release.sh`'s symlink swap keeps this path pointing at the schema
  for whichever qilab version is actually deployed.
- Refreshed the vendored `config.schema.json` fallback to match qilab's
  current schema (adds the `system` section, drops the removed
  `DEPOLARIZING`/`AMPLITUDE_DAMPING` noise models).

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

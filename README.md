# qilab_web_control

Minimal FastAPI + Jinja2 UI to control the qilab systemd service, including
updating the qilab binary and updating this app itself from the browser.

## Requirements

- Python 3.10+ with `pip` (`python3-pip`). No `python3-venv`, no build tools.
- systemd available on the host
- A systemd service for qilab (example: `qilab.service`)

## Deployment layout

Everything about the update mechanism follows from one rule: **the systemd unit
reaches the app only through a symlink, never through a concrete install path.**
That is what lets an update build a new copy alongside the live one and switch
over atomically, with a rollback that is just "point the symlink back".

```
/opt/qilab-web-control/
├── current -> releases/0.4.0             # the ONLY path the unit references
├── releases/
│   ├── 0.3.0/site-packages/              # previous release, kept for rollback
│   └── 0.4.0/site-packages/              # live release (app + its dependencies)
└── scripts/
    └── self_update.sh                    # outside releases/: survives any swap
/var/log/qilab-web-control/self_update.log
/etc/systemd/system/qilab-web-control.service
```

A release is a `pip install --target` directory that the unit puts on
`PYTHONPATH` — deliberately not a virtualenv. `python3 -m venv` requires the
`python3-venv` package, which the RT images do not carry, so `pip` is the only
tool involved. `--target` also sidesteps PEP 668, so nothing needs
`--break-system-packages`.

What the indirection buys:

- **A failed update cannot break the device.** The new release is built and
  import-checked before `current` moves. A bad wheel leaves the live release
  untouched and the service is never even restarted.
- **Rollback needs no network and no artifacts.** The previous release is still
  on disk; recovery is a symlink flip.
- **Updates need no network either.** A new release is seeded from the live one,
  so only the app's own files come off the uploaded wheel.

Two rules follow from it, and both are enforced rather than documented and
hoped for:

- Nothing may reference a concrete release path — not `ExecStart`, not
  `PYTHONPATH`, not `WorkingDirectory`. `self_update.sh` refuses to run if the
  unit does not route through `current`.
- The app must not also be installed system-wide. A copy in
  `/usr/lib/python3/dist-packages` would shadow the release directory on
  `import app` and pin the device to one version forever.

Deployment assets — `self_update.sh` and the systemd unit — ship **inside the
wheel** under `app/deploy/`, and installers copy them out of the installed
package. So what lands on a device always matches the version installed, and
there is exactly one definition of each: the unit is
[app/deploy/qilab-web-control.service](app/deploy/qilab-web-control.service).

## Install on the RT image

The rpi-img-gen layer is maintained in the image-generation repository. What it
has to produce, and a working `customize-hooks` implementation, are in
[docs/image-layer.md](docs/image-layer.md).

## Install on a device

The same layout by hand, for a device you can reach over SSH. This is also the
path out of an older deployment — see [docs/migration.md](docs/migration.md)
first if `/opt/qilab-web-control` does not already look like the tree above.

```bash
VERSION=0.4.0
ROOT=/opt/qilab-web-control
SP="$ROOT/releases/$VERSION/site-packages"

sudo mkdir -p "$SP" "$ROOT/scripts" /var/log/qilab-web-control

curl -L -o /tmp/qilab_web_control.whl "<gitlab-release-wheel-url>"
sudo python3 -m pip install --target "$SP" /tmp/qilab_web_control.whl

# Fail here rather than at boot.
sudo env PYTHONPATH="$SP" python3 -c 'import app.main'

# Deployment assets come from the wheel, so they match this exact version.
sudo install -m 0755 "$SP/app/deploy/self_update.sh" "$ROOT/scripts/self_update.sh"
sudo install -m 0644 "$SP/app/deploy/qilab-web-control.service" \
  /etc/systemd/system/qilab-web-control.service

sudo ln -sfn "$ROOT/releases/$VERSION" "$ROOT/current"
sudo systemctl daemon-reload
sudo systemctl enable --now qilab-web-control.service
sudo systemctl status qilab-web-control.service --no-pager
```

Open http://localhost:8000

Read the unit before enabling it: it assumes qilab lives at `/opt/qilab`, and
picks up shared qilab paths from `/etc/default/qilab-global-env` if that file
exists.

Every subsequent version should go on through the UI — the install above is
needed once per device.

## Self-update

The Control tab's "Update qilab_web_control" section lets an admin upload a new
release wheel and upgrade this app itself, without SSH access.

Sequence (`app/deploy/self_update.sh`, deployed to the device as
`/opt/qilab-web-control/scripts/self_update.sh`):

1. **Preflight** — refuse to write anything unless this install is actually
   swappable: `pip` is usable, `current` is a symlink (not a real directory),
   and the unit reaches the app through `current` via its `ExecStart` or its
   environment (`EnvironmentFile=` contents included, since `systemctl show`
   reports only inline `Environment=`). An install that does not satisfy the
   layout aborts here with an explanation instead of silently succeeding at
   nothing; see [docs/migration.md](docs/migration.md).
2. **Build the new release** in `releases/<version>/site-packages`, leaving the
   live release untouched. A same-version reinstall builds into
   `<version>-r2` rather than deleting the directory it is running from.
3. **Health check** — `PYTHONPATH=<new release> python3 -c 'import app.main'`,
   exactly how the unit will import it. On failure, abort: `current` is never
   moved and the service is never restarted.
4. **Atomic swap** — flip `current` to the new release.
5. **Restart and verify** — `systemctl restart`, then poll `is-active`, because
   a successful `restart` only means systemd ran the job, not that a
   `Type=simple` process stayed up.
6. **Rollback on failure** — point `current` back at the previous release and
   restart again.
7. **Housekeeping**, only once the new release is verified live: replant
   `self_update.sh` from the new wheel (so script fixes reach devices), warn if
   the new wheel's unit file differs from the installed one, and prune releases
   older than the previous one.

Three implementation details worth knowing:

- **The script runs as its own transient unit** (`systemd-run
  --unit=qilab-web-control-selfupdate --collect`), not as a background child of
  the request handler. It restarts `qilab-web-control.service` partway through
  its own execution, and a plain child would share that service's cgroup and be
  killed by the same restart before it could verify success or roll back.
- **Dependency installs are offline-first.** The device is not assumed to reach
  PyPI, so the new release is seeded by copying the live release's
  `site-packages` and the wheel is then installed with `--no-deps`. Because a
  release is a plain directory rather than a venv, that copy involves no
  absolute paths and no shebang rewriting. The seeded copy of the app itself
  (package plus its `dist-info`) is deleted before the install, since
  `pip --target` does not uninstall and two `dist-info` directories would make
  the reported version ambiguous.
- **A release that changes dependencies needs network, or a new image.** The
  `--no-deps` install would leave the old dependency version behind; step 3
  catches that, and the script then retries a full networked `pip install`. If
  that also fails, the update aborts with the live release intact.

Residual risk: if the restart *and* the rollback restart both fail (e.g. the
host itself is unhealthy), you still need console/SSH access to recover — the
same risk already accepted for the qilab binary's own update mechanism.

### Not yet implemented

The app does not check the layout itself, only the script does. So on a
misconfigured install the UI still accepts the upload and the refusal shows up in
`self_update.log` a moment later, rather than the form declining up front.

## Local development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -U setuptools
pip install -e .
```

```bash
sudo QILAB_SERVICE=qilab.service \
     QILAB_UI_TITLE="Qilab Control" \
     QILAB_LOG_PATH=/var/log/qilab/qilab.log \
     QILAB_UPDATE_SCRIPT=/opt/qilab/scripts/update_release.sh \
     python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Self-update is unavailable in this mode — there is no `current` symlink to flip,
so the script's preflight declines and logs why.

## Maintainer release workflow

1. Refresh the vendored schema fallback (`app/config/config.schema.json`) from the qilab tag this release targets, and update `app/config/VENDORED_SCHEMA_VERSION` to match its `x-qilab-schema-version`. `verify:vendored_schema` in CI fails the release if these two are out of sync — this is the fallback used whenever `QILAB_WEB_SCHEMA_PATH` isn't set, so keep it deliberately current rather than letting it silently drift from qilab's schema.
2. Update `CHANGELOG.md` with a section for the version you are releasing (for example `## [0.4.1] - 2026-08-04`).
3. Ensure `setup.py` `version` exactly matches the release tag version without the `v` prefix.
4. Push the version tag (format `vX.Y.Z`) to trigger build + GitLab release publication.

The published wheel is the single deployment artifact: it carries the app, the
unit file, and `self_update.sh`. Nothing in the image layer needs updating for a
normal release — only the wheel path/version it points at.

One release property is worth calling out in the changelog when it applies: **a
release that changes `install_requires` cannot be delivered over the air to a
device without PyPI access**, because the offline seeding path installs the wheel
with `--no-deps`. Such a release needs network on the device or a new image. Keep
dependency bumps deliberate for that reason.

## Notes

- Start/stop uses `systemctl` and requires permission. If this service runs as a non-root user, add a sudoers rule or run it under a privileged service account.
- Start/stop/restart actions are available from the UI.
- `QILAB_LOG_LEVEL` can be set from the Control tab (`DEBUG`, `ERROR`, `OFF`); saving it restarts `qilab.service`.
- The UI polls `/var/log/qilab/qilab.log` every 100ms and displays the latest lines.
- The `Config` tab is a schema-driven structured editor for the qilab `nodes.yaml`. It renders fields, dropdowns, and add/remove controls from qilab's `config.schema.json`, validates edits against that schema before saving, then writes canonical qilab-dialect YAML and restarts `qilab.service`. An invalid edit is rejected without touching the live file.
  - **Config path** is resolved the way qilab resolves it: it prefers the qilab unit's `QILAB_CONFIG_PATH` / `QILAB_CONFIG_DIR` (read via `systemctl show`), then this process's env, then `~/.qilab/config/nodes.yaml`. `QILAB_CONFIG_FILE` is **deprecated** (qilab never read it) and only used as a last resort. The resolved path and its source are shown in the UI.
  - **Schema source**: set `QILAB_WEB_SCHEMA_PATH=/opt/qilab/current/config.schema.json` to track qilab's schema live — the qilab release tarball now ships `config.schema.json` next to the binary, so `update_release.sh`'s symlink swap keeps this path pointing at the schema for whichever qilab version is actually deployed; otherwise a vendored copy bundled with this package is used. The schema cache is reloaded automatically right after a binary update (see `/update`); use the "Reload schema" button to force a reload at any other time.
- Binary updates are done by uploading a tarball + version from the UI, which executes `update_release.sh <version> <tarball_path>`.
- Binary updates are root-only: this web control process must run as root, and it executes the update script directly (no sudo fallback).
- Self-updates (of this app) are done by uploading the release wheel + version from the UI's "Update qilab_web_control" section; see [Self-update](#self-update) above. Also root-only, and the result isn't reported synchronously — check `self_update.log` in the UI (or `/var/log/qilab-web-control/self_update.log`) instead.
- The `Profiling` tab renders qilab's profiler CSVs as sortable tables, and the `Data acquisition` tab exposes the DAQ JSONL for download.
  - **Telemetry paths** are resolved the way qilab resolves them, and from the **qilab unit's** environment rather than this process's, since that is what decides where the simulator actually writes: `QILAB_PROFILER_PATH` / `QILAB_PROFILER_DIR` for the profiler and `QILAB_DAQ_PATH` / `QILAB_DAQ_DIR` for DAQ, falling back to `~/.qilab/logs/`. Note qilab's own asymmetry: `QILAB_PROFILER_PATH` is a **base with no extension** (qilab appends `_durations.csv` / `_events.csv`), while `QILAB_DAQ_PATH` is a **complete filename**. Each resolved path and its source are shown in the UI.
  - **`EnvironmentFile=` is understood.** `systemctl show -p Environment` reports only inline `Environment=` directives, so the app also reads the unit's `EnvironmentFile=` contents and layers them under the inline values, matching systemd's precedence. Without this, a deployment that keeps qilab's paths in a shared file (as the RT image does, in `/etc/default/qilab-global-env`) would silently resolve every path — config included — to the wrong place.
  - **Profiler data is written once per run**, when qilab shuts down gracefully, and each run truncates the previous file. So while qilab is active the tables show the *previous* run; the page says so, and always shows the file's modification time. A hard kill leaves no new file at all.
  - **The profiler base name is compiled into qilab** (`PROF_SAVE_DATA("profile")`), so the app cannot discover it. If it ever differs, set `QILAB_WEB_PROFILER_BASE`; when the expected file is missing the page names the bases it *did* find in that directory.
  - **DAQ is opt-in** via `QILAB_DAQ=1` on the qilab unit — compared against that exact string, so `true`/`yes` count as disabled. If the sink resolves under `$HOME` while the qilab unit sets `ProtectHome=true`, it can never write there; the page flags that specific combination.
  - Optional tuning: `QILAB_WEB_DAQ_PREVIEW_LINES` (default 5), `QILAB_WEB_DAQ_COUNT_MAX_BYTES` (default 8 MiB — above this the record count is estimated from a sample rather than scanning the whole file, and the UI marks it as an estimate).
- This server is intentionally minimal and has **no authentication**, while binding `0.0.0.0:8000` as root. The telemetry download endpoints therefore expose research data to anyone who can reach the port, as the log view and config editor already do. Bind it to a trusted interface, or put it behind an authenticating reverse proxy, before exposing it to any untrusted network.
</content>
</invoke>

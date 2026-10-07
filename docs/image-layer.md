# rpi-img-gen layer for web-control-server

Reference for the layer that installs this app into the RT image. It lives in
the image-generation repository; this file is the contract it has to satisfy, so
the two can be kept in sync deliberately rather than by accident.

## What the layer must produce

```
/opt/qilab-web-control/
├── current -> releases/<version>            # the only path the unit names
├── releases/<version>/site-packages/        # pip install --target output
└── scripts/self_update.sh                   # outside releases/, survives swaps
/etc/systemd/system/qilab-web-control.service
/var/log/qilab-web-control/
```

Three requirements, each load-bearing:

1. **No virtualenv.** `python3 -m venv` needs the `python3-venv` package, which
   the RT images do not carry. A release is a plain `pip install --target`
   directory that the unit puts on `PYTHONPATH`, so `pip` is the only tool
   required. `--target` also sidesteps PEP 668, so `--break-system-packages` is
   not needed anywhere.
2. **Nothing may reference a concrete release path** — not `ExecStart`, not
   `PYTHONPATH`, not `WorkingDirectory`. The unit reaches the app only through
   `current`, because that indirection is what
   [self-update](../README.md#self-update) swaps; without it, an update flips a
   symlink nobody reads. `self_update.sh` preflights this and refuses to run
   rather than reporting a success that changed nothing.
3. **No system-wide install.** A copy in `/usr/lib/python3/dist-packages` would
   shadow the release directory on `import app` and pin the device to whatever
   version the image shipped.

`self_update.sh` and the unit file are **not** carried by the layer — they ship
inside the wheel under `app/deploy/` and the layer copies them out of the
installed package. A normal release therefore needs no layer change, only a new
wheel path.

## The layer

```yaml
# METABEGIN
# X-Env-Layer-Name: web-control-server
# X-Env-Layer-Category: app
# X-Env-Layer-Desc: Installs web-control-server into a versioned release dir from release artifacts
# X-Env-Layer-Requires: device-base,systemd-min
# METAEND
---
mmdebstrap:
  packages:
    - python3-pip
  customize-hooks:
    - |
      set -eu
      echo "Installing web-control-server..."
      WHL_PATH="${WEB_CONTROL_WHL_PATH:-$SRCROOT/files/qilab_web_control-0.4.3-py3-none-any.whl}"
      WHL_NAME="$(basename "$WHL_PATH")"
      # qilab_web_control-0.4.3-py3-none-any.whl -> 0.4.3
      VERSION="$(echo "$WHL_NAME" | cut -d- -f2)"

      if [ ! -f "$WHL_PATH" ]; then
        echo "Web control wheel not found: $WHL_PATH"
        exit 1
      fi

      ROOT=/opt/qilab-web-control
      REL="$ROOT/releases/$VERSION"
      SP="$REL/site-packages"

      mkdir -p "$1$SP" "$1$ROOT/scripts" "$1/var/log/qilab-web-control"
      cp "$WHL_PATH" "$1/tmp/$WHL_NAME"

      # Installed inside the chroot, so any compiled dependency (pydantic-core)
      # resolves to a wheel for the target architecture.
      chroot "$1" python3 -m pip install --disable-pip-version-check \
        --target "$SP" "/tmp/$WHL_NAME"

      # Fail the image build, not the first boot, if the install is unusable.
      chroot "$1" env PYTHONPATH="$SP" python3 -c 'import app.main'

      # Deployment assets come from the wheel, so they match this exact version.
      chroot "$1" install -m 0755 "$SP/app/deploy/self_update.sh" \
        "$ROOT/scripts/self_update.sh"
      chroot "$1" install -m 0644 "$SP/app/deploy/qilab-web-control.service" \
        /etc/systemd/system/qilab-web-control.service

      # Absolute target: dangles on the build host, correct inside the image.
      ln -sfn "$REL" "$1$ROOT/current"

      chroot "$1" systemctl enable qilab-web-control.service

      rm -f "$1/tmp/$WHL_NAME"
```

## Notes

- **The build host needs outbound network**, because `pip install` resolves
  dependencies from PyPI inside the chroot. To make image builds offline and
  reproducible, vendor a wheelhouse in the layer's `files/` and add
  `--no-index --find-links=/tmp/wheelhouse` to the `pip install` call.
- **`WorkingDirectory`** is `/opt/qilab-web-control/current`, which contains no
  importable `app/` — the import comes from `PYTHONPATH`. Do not point it at a
  release directory or at `site-packages`.
- **Devices already in the field** that were flashed from an older image need a
  one-time migration rather than just a new wheel — see
  [migration.md](migration.md).
- The unit sets `EnvironmentFile=-/etc/default/qilab-global-env` before its
  inline `Environment=` lines, so the shared file can supply qilab paths while
  the unit's own values win on conflict.

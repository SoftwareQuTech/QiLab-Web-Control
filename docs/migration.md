# Migrating a device to the release-directory layout

One-time notes for devices deployed before
[the current layout](../README.md#deployment-layout) existed. New devices need
none of this — flash an image built with the
[current layer](image-layer.md) and you are already there.

Both older layouts are unsupported. The point of migrating is not tidiness: on
either of them a self-update cannot take effect.

## From a system-wide install

Symptom: no `/opt/qilab-web-control` at all, and the unit runs `python3 -m
uvicorn app.main:app` with the package in `/usr/lib/python3/dist-packages`
(installed with `pip3 install --break-system-packages`). Self-update reports
success and changes nothing, because there is no symlink for it to flip.

The system-wide copy has to go, not just be left alongside: it stays on
`sys.path` ahead of nothing in particular, so `import app` can resolve to it and
shadow the release directory, pinning the device to whatever version the image
shipped.

```bash
sudo systemctl stop qilab-web-control.service
sudo pip3 uninstall -y qilab_web_control
sudo rm -rf /usr/bin/local/web-control-server     # stale WorkingDirectory
```

Then follow [Install on a device](../README.md#install-on-a-device).

## From a venv install

Symptom: `/opt/qilab-web-control/releases/<version>/venv`, or a flat
`/opt/qilab-web-control/venv` with no `current` symlink. Virtualenvs are no
longer used — `python3 -m venv` needs the `python3-venv` package, which the RT
images do not carry.

```bash
sudo systemctl stop qilab-web-control.service
```

Install the version you want as in
[Install on a device](../README.md#install-on-a-device). That writes a
`site-packages` release directory and replaces the unit with the
`PYTHONPATH`-based one, so the old venv is then dead weight:

```bash
sudo rm -rf /opt/qilab-web-control/releases/*/venv /opt/qilab-web-control/venv
```

## Verifying the result

```bash
# Must be a symlink into releases/, not a real directory.
readlink -f /opt/qilab-web-control/current

# Must reference .../current — this is exactly what self_update.sh preflights.
systemctl show -p ExecStart -p Environment qilab-web-control.service \
  | grep -c /opt/qilab-web-control/current/

# Must report the version you installed, and must not resolve into
# /usr/lib/python3/dist-packages.
systemctl show -p MainPID --value qilab-web-control.service
curl -s localhost:8000 | grep -o 'Installed version:[^<]*'
```

A self-update that still cannot take effect aborts during preflight and says why
in `/var/log/qilab-web-control/self_update.log`, without touching the live
install.

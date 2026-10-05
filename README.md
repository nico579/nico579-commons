# nico579-commons

Shared building blocks for four small open-source apps:
[blink2video](https://github.com/nico579/blink2video),
[lidar2map](https://github.com/nico579/lidar2map),
[watch2notif](https://github.com/nico579/watch2notif) and
[gpxsolar](https://github.com/nico579/gpxsolar).

Each of them grew its own copy of the same pieces: the system tray icon, the
local web server, atomic file writes, the update check. Every fix had to be
carried by hand into the other copies, and some were forgotten. This package
gathers those pieces in one place, one module each, so that the four apps
behave the same and a fix lands everywhere at once.

## Modules

- `nico579_commons.tray`: the tray icon, with the very same menu in all four
  apps: Open, "Update to x.y" only when a newer version is known, Restart,
  Stop, and "Create a Desktop shortcut". There is no room for app-specific
  entries on purpose: everything else lives in the page that Open brings
  up. The menu is rebuilt every five seconds, on the main thread under
  macOS; Restart, Stop and Update run off the icon's message loop, and the
  icon waits for them before returning.
- `nico579_commons.raccourci`: a desktop shortcut in one call, as a .lnk on
  Windows, a small .app on macOS and a trusted .desktop file on Linux.
- `nico579_commons.maj`: a light check for a newer GitHub release, asked at
  most once an hour by a background thread, so the tray menu and the page
  read the answer without waiting for the network.
- `nico579_commons.relance`: what "Restart" needs. Under the app's own
  systemd user service, systemd itself restarts it, since a process spawned
  from inside the service would be killed along with it; elsewhere, a new
  detached process, in its own session so that launchd leaves it alone.
  `hors_du_service()` does the same for a second process that must outlive
  the app, such as lidar2map's "New instance": under the service, it starts
  in a scope of its own (`systemd-run --user --scope`).
- `nico579_commons.environnement`: gives system programs (systemctl,
  xdg-open, the browser) the `LD_LIBRARY_PATH` they had before the
  PyInstaller bootloader prefixed it with the app's own libraries.
- `nico579_commons.atomique`: atomic JSON writes, tolerant JSON reads (BOM,
  transient Windows refusals) and an inter-process file lock.
- `nico579_commons.dossiers`: where an app keeps its state (the OS data
  folder) and its outputs (Documents), an `<APP>_HOME` override, and the
  one-time carry-over of the state an older version kept in its working
  folder. An app is a `Dossiers("name", ...)` with its own file names.
- `nico579_commons.serveweb`: the local web server behind each app's page:
  static files, `/api/*` routes in JSON, and the checks on where a request
  comes from (Host, client address, Origin, Sec-Fetch-Site). An app subclasses
  `Handler` for its own variable and routes with query parameters. Also finds
  a free port and tells whether an instance of the app already answers.

The inventory of what is shared, and where the work stands, lives in
[INVENTAIRE.md](INVENTAIRE.md) (in French). `python outils/inventaire_communs.py`
re-measures what is still duplicated across the four repositories (read-only);
[ANALYSE-MUTUALISATION-2026-09-29.md](ANALYSE-MUTUALISATION-2026-09-29.md) is
the analysis it produced.

## Install

```sh
pip install nico579-commons          # relance, raccourci, maj, environnement
pip install "nico579-commons[tray]"  # plus pystray and Pillow, for the icon
```

The package on its own requires nothing: only `tray` needs pystray and Pillow
(the `tray` extra, plus jeepney on Linux), and `tray.disponible()` says whether
they, and a notification area, are there. An app that lists them itself does not
need the extra.

On Linux, when the desktop offers a StatusNotifierItem host (GNOME with the
AppIndicator extension, as on Ubuntu; KDE Plasma), the icon talks that protocol
directly over D-Bus (`nico579_commons.tray_sni`, pure Python with jeepney, no GTK
or PyGObject), because pystray's X11 fallback shows the icon under Wayland but
opens no menu. Elsewhere pystray is used as before.

## Use

```python
from nico579_commons import tray

actions = tray.Actions(ouvrir=open_page, redemarrer=restart, arreter=stop,
                       version_disponible=cached_newer_version,
                       mettre_a_jour=start_update,
                       creer_raccourci=create_desktop_shortcut,
                       langue=current_language)
tray.Tray("myapp", Path("assets/myapp.ico"), actions).executer()
```

Python 3.8 or later: blink2video still ships a Windows 7 build on 3.8.

## License

GPL-3.0-or-later, like the four apps.

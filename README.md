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

- `nico579_commons.tray`: the tray icon, with the menu shared by the four
  apps: Open, the app's own items, "Update to x.y" only when a newer version
  is known, Restart, Stop, and "Create a Desktop shortcut". The menu is
  rebuilt every five seconds, on the main thread under macOS; Restart, Stop
  and Update run off the icon's message loop, and the icon waits for them
  before returning.
- `nico579_commons.raccourci`: a desktop shortcut in one call, as a .lnk on
  Windows, a small .app on macOS and a trusted .desktop file on Linux.
- `nico579_commons.maj`: a light check for a newer GitHub release, asked at
  most once an hour by a background thread, so the tray menu and the page
  read the answer without waiting for the network.

The inventory of what is shared, and where the work stands, lives in
[INVENTAIRE.md](INVENTAIRE.md) (in French).

## Use

```python
from nico579_commons import tray

actions = tray.Actions(ouvrir=open_page, redemarrer=restart, arreter=stop,
                       version_disponible=cached_newer_version,
                       mettre_a_jour=start_update, langue=current_language)
tray.Tray("myapp", Path("assets/myapp.ico"), actions).executer()
```

Python 3.8 or later: blink2video still ships a Windows 7 build on 3.8.

## License

GPL-3.0-or-later, like the four apps.

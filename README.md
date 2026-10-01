# iLO4 Fan Control

A standalone Windows desktop app for managing fan profiles on HPE ProLiant servers (Gen8/Gen9) with **patched iLO 4 firmware**. Pick a profile with a rotary-style dial, watch live fan speeds, and restart iLO with one click.

Original post on how to mod ilo4 firmware can be found on this reddit post:
https://www.reddit.com/r/homelab/comments/sx3ldo/hp_ilo4_v277_unlocked_access_to_fan_controls/

<!-- Add a screenshot: put it in the repo as screenshot.png and uncomment -->
<!-- ![Screenshot](screenshot.png) -->

## Features

- **Rotary dial** profile selector (mouse wheel, drag, or click the centre to apply)
- **Editable profiles** with min/max fan percentage, applied to all fans
- **Live fan speeds** for every fan, with a configurable refresh interval
- **Settle delay** after applying a profile before readings resume
- **Restart iLO** button (`reset map1`) that reconnects and re-applies your last profile
- **Test connection** button and a live log (GUI panel and console)
- Persistent SSH session, no cloud, no telemetry

## Requirements

- An iLO 4 running **patched firmware** that exposes the `fan` SSH command, e.g. [kendallgoto/ilo4_unlock](https://github.com/kendallgoto/ilo4_unlock). Stock firmware will reject fan commands.
- An iLO user account with SSH access.

## Download

Grab `iLO4FanControl.exe` from the [Releases](../../releases) page and run it. No Python needed.

## Run from source

```
py -m pip install -r requirements.txt
py ilo_fan_control.py
```

> **Important:** use `paramiko==3.5.1`. Newer versions removed the legacy SSH algorithms iLO 4 relies on, and you'll get "no acceptable kex algorithm".

## First-time setup

1. Open **Connection settings**, enter your iLO IP, username and password, and click **Test connection**.
2. Set the number of fans and (if needed) the first fan ID used by `show system1/fanX`.
3. Check **Value for 100%**: patched iLO 4 `fan p` commands are believed to use 0-255, so the default is 255. Set it to 100 if your firmware uses percent.
4. Pick a profile and click **Apply profile**.

Settings are stored in `ilo_fan_config.json` next to the app. **The password is stored in plain text there**, so keep the file private (it is git-ignored).

## Commands used (editable in settings)

| Purpose | Default |
|---|---|
| Fan info | `show system1/fan{x}` |
| Set min | `fan p {n} min {v}` |
| Set max | `fan p {n} max {v}` |
| Restart iLO | `reset map1` |

## Build the exe yourself

```
py -m pip install -r requirements.txt pyinstaller
py -m PyInstaller --onefile --name iLO4FanControl ilo_fan_control.py
```

Pushing a tag like `v1.0.0` builds the exe on GitHub Actions and attaches it to the release automatically.

## Troubleshooting

- **No acceptable kex algorithm**: install `paramiko==3.5.1`.
- **Fan commands return errors**: your iLO firmware is probably not patched.
- **Fan readings stuck or wrong**: use **Restart iLO**.
- **Antivirus flags the exe**: a known false positive with PyInstaller one-file builds; build it yourself if you prefer.

## Disclaimer

Unofficial, not affiliated with HPE. Changing fan limits can cause overheating and hardware damage. Monitor your temperatures and use at your own risk.

## License

MIT

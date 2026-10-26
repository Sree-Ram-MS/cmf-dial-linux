# CMF Dial for Ubuntu

Control your Ubuntu computer with the **CMF Buds Pro 2** case dial: scroll pages, change volume, flip slides, switch tabs, or run any shortcut or command. It comes with a settings app where you pick what every dial gesture does, and a live tester that shows what the buds are sending.

Inspired by [Orbit by Yemcube](https://github.com/harsha20599/orbit), which does this on Windows, macOS and Android. This is an independent Linux version.

## Install

Download `cmf-dial_<version>_all.deb` from [Releases](https://github.com/Sree-Ram-MS/cmf-dial-linux/releases), then:

```bash
sudo apt install ./cmf-dial_2.0.0_all.deb
```

Reboot once after the first install so the permissions take effect. CMF Dial then starts on its own at every login.

## Set up the buds

1. In the **Nothing X** app on your phone, go to Controls → Dial and set:
   - Single press: **Play/Pause**
   - Double press: **Next track**
   - Triple press: **Previous track**
2. Connect the buds to the computer in Settings → Bluetooth, and choose them as the output in Settings → Sound.
3. If the buds are also connected to your phone, pause audio on the phone, since the dial sends its signals to whichever device is playing.
4. Open **CMF Dial** from your apps. The status should read *Connected*.

## Using it

| Gesture | Default |
|---|---|
| Turn the dial | Drives the current mode (scroll, volume, slides, tabs…) |
| Single press | The current mode's press action (play/pause, black out slides, close tab…) |
| Double press | Next mode (a pop-up shows its name) |
| Triple press | Previous mode |

You can change all of this in the app: turn modes on or off, rename them, reorder them, add your own, and set any action, including a custom shortcut (`ctrl+shift+t`, `super+d`) or a command (`gnome-calculator`).

## Troubleshooting

Open the app's **Test** tab, play some audio through the buds, and turn or press the dial. Each signal the buds send appears there.

- **Nothing appears:** check that the buds are this computer's sound output and that audio is playing here, not on your phone.
- **"Needs attention" about /dev/uinput or the button device:** reboot once. As a quick fix, run `sudo chmod a+rw /dev/uinput /dev/input/event*` and press Restart.
- **Turning at 100% or 0% volume does nothing in non-volume modes:** the dial is read through volume changes, so set the volume somewhere in the middle.
- **See the log in a terminal:** `systemctl --user stop cmf-dial; cmf-dial --foreground`

## How it works

- Dial presses arrive as AVRCP play/pause, next and previous on BlueZ's `CMF Buds Pro 2 (AVRCP)` input device, which CMF Dial reads with python-evdev.
- Dial turns arrive as AVRCP absolute volume (the buds' PipeWire sink volume changes, which it watches with `pactl subscribe` and then restores) or as volume keys on the same device.
- Actions are performed through a virtual keyboard and mouse (`/dev/uinput`), so they work on both Wayland and X11.
- Settings live in `~/.config/cmf-dial/config.json`. The background service is a systemd user unit (`cmf-dial.service`).

## Build from source

```bash
./build-deb.sh        # -> dist/cmf-dial_<version>_all.deb
```

Run without installing: `python3 -m cmfdial.daemon -f` and `python3 -m cmfdial.ui`.

## License

MIT. Inspired by Orbit by Yemcube ([@yem.cube](https://www.instagram.com/yem.cube)). Not affiliated with Nothing or CMF.

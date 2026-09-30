# AirPods (grivera.airpods)

Omarchy bar widget for AirPods. It shows per-bud and case battery and ear
detection, and controls noise mode, Conversational Awareness, connection and
pairing. It also auto-pauses media when you take an AirPod out.

## Install

Use Omarchy's plugin manager:

```
omarchy plugin add https://github.com/grivera82/omarchy-airpods.git --enable
```

This clones the plugin into `~/.config/omarchy/plugins/grivera.airpods`, checks it,
and adds the widget to your bar. When run interactively, it asks which bar section
to use (default: right). Without `--enable`, you can turn it on later with:

```
omarchy plugin enable grivera.airpods --section right
```

It needs Bluetooth (BlueZ, `bluetoothctl`), `gdbus`, and Python 3, which Omarchy
already includes. Paths starting with `bin/airpods` below are relative to the plugin
folder.

To update or uninstall:

```
omarchy plugin update grivera.airpods
omarchy plugin disable grivera.airpods   # hide it but keep it installed
omarchy plugin remove grivera.airpods    # delete it
```

## Bar widget

- **Left click**: open the panel.
- **Right click**: cycle noise control (Transparency → Adaptive → Noise Cancellation).
- **Middle click**: connect / disconnect.
- The icon turns red when a bud not on the charger drops to 10% or below. Hover to see the battery.

In the panel, `1`–`4` pick a noise mode and `c` connects or disconnects. Noise modes
are shown only for models that have them (Pro, Pro 2/3, AirPods 4 ANC, Max).

## Pairing

Open the panel and click **Pair**. Then open the case lid and hold the button on the
back until the light flashes white. The plugin scans, then pairs, trusts and connects
the AirPods. From a terminal you can run `bin/airpods pair` instead.

## CLI

For Hyprland keybindings:

```
bin/airpods toggle                     # connect / disconnect
bin/airpods mode cycle | off | transparency | adaptive | anc
bin/airpods conversational [on|off|toggle]
bin/airpods autopause [on|off|toggle]
bin/airpods status                     # JSON
```

`mode` and `conversational` need the widget's daemon, which holds the control channel.

## How it works

- Pairing and connecting go through BlueZ (`bluetoothctl`). `gdbus monitor` on
  `org.bluez` picks up connection changes immediately.
- While connected, `Service.qml` runs `bin/airpods daemon`. It opens Apple's AAP control
  channel (L2CAP PSM 0x1001, protocol as documented by
  [LibrePods](https://github.com/kavishdevar/librepods)), receives battery,
  ear-detection and mode notifications, and sends mode changes.
- Auto-pause uses MPRIS over D-Bus. It pauses whatever is playing when a bud leaves
  your ear, and resumes those players when the bud goes back in.
- It uses only Python's standard library, so there is nothing to install. The saved
  device and settings are in `~/.local/state/grivera-airpods/config.json`.

If the panel says the control channel is unavailable, the AirPods are connected for
audio but refused AAP. Disconnecting and reconnecting usually fixes it. Some firmware
only enables certain features for Apple hosts. LibrePods documents setting
`DeviceID = bluetooth:004C:0000:0000` in `/etc/bluetooth/main.conf` as a workaround.

## License

MIT. See [LICENSE](LICENSE).

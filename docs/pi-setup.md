---
name: pi-setup
description: Flash Raspberry Pi OS onto the Pi 5, bring up the 5" touchscreen, and install the app as a full-screen kiosk
date: 2026-09-30
aliases: [Pi setup, flash, kiosk, rpi]
tags: [build, app, part5]
---

# Pi setup — flash, screen, app

From a blank microSD card to the app full screen on the touchscreen. About an hour, most
of it waiting on downloads. Do this **before** wiring the strip — it is [build](build.md) step 1,
written out — and do every step in order, because each one checks the one before it.

> **Facts in this note were checked against primary sources on 2026-09-30** (Raspberry Pi
> documentation source, the Pi and Debian package archives, Waveshare's wiki). The current
> Raspberry Pi OS is **Trixie** (Debian 13, Python 3.13, labwc desktop). If you are reading
> this much later, re-check §1 and §3.

## 0. What you need

| | |
|---|---|
| Raspberry Pi 5 | 4 GB is plenty; 8 GB only matters for D-23's reversal (Audiveris on the Pi) |
| microSD | 32 GB or more, A2-rated |
| Power | the official **27 W USB-C** supply (5.1 V 5 A). The Pi gets its own supply; the strip never powers it ([hardware](hardware.md) §6) |
| Touchscreen | one of the two below, **with the cable for a Pi 5** |
| A PC | to run Raspberry Pi Imager, and to compile scores ([score](score.md)) |
| USB keyboard | optional, but makes §2 painless if Wi-Fi does not come up |

### Which panel do you have?

The app is laid out at **800×480** and scales itself to any screen, so both work — but they
are set up differently, and the BOM line ([hardware](hardware.md) §7, row 9) does not name a model.

| | Waveshare-class **5" DSI LCD, 800×480** | Official **Raspberry Pi Touch Display 2, 5"** |
|---|---|---|
| Native | 800×480, landscape | **720×1280, portrait** |
| Needs config | yes, one `dtoverlay` line (§3a) | no, auto-detected on a Pi 5 |
| Landscape | native | rotate once in Control Centre (§3b) |
| App scale | 1.0, pixel-exact | 1.5, letterboxed 40 px each side |
| Cable | 22-pin (Pi 5) ↔ 15-pin (panel); confirm it is in the box | 22-way ↔ 15-way, supplied |

The Pi 5's display connectors are the **small 22-pin** ones; a Pi 4 cable (15-pin both
ends) does not fit. Plug the ribbon in **with the Pi unpowered**.

## 1. Flash the card (on the PC)

1. Install **Raspberry Pi Imager** (2.0.x, from raspberrypi.com/software) and open it.
2. **Device:** Raspberry Pi 5.
3. **OS:** *Raspberry Pi OS (64-bit)* — the desktop image, **not Lite**: the kiosk needs the
   desktop's compositor (labwc).
4. **Storage:** the microSD card.
5. **Customisation** (the tab that opens before writing — do not skip it):
   - **Hostname:** `fingerlight` → the Pi will answer at `fingerlight.local`
   - **Localisation:** capital city *Los Angeles* (sets time zone and Wi-Fi country), keyboard `us`
   - **User:** `kevin` and a password you will remember
   - **Wi-Fi:** your network name and password
   - **Remote access:** enable **SSH** (password is fine; a public key is better)
6. **Write**, let it verify, eject.

## 2. First boot

1. Ribbon to the panel, card in, **then** power. First boot resizes the card and reboots
   once; give it two minutes.
2. From the PC (Git Bash or PowerShell):
   ```
   ssh kevin@fingerlight.local
   ```
   If `fingerlight.local` does not resolve, find the Pi's IP in your router's client list
   and use that instead.
3. Bring everything up to date, including the bootloader (Touch Display 2 needs a recent one):
   ```
   sudo apt update && sudo apt full-upgrade -y
   sudo rpi-eeprom-update -a
   sudo reboot
   ```

### 2a. A second Wi-Fi network (taking the Pi somewhere else)

Raspberry Pi OS uses NetworkManager. `connection add` saves a network without trying to
join it, so it works even when that network is out of range; at boot the Pi joins
whichever saved network it can see.

```
sudo nmcli connection add type wifi ifname wlan0 con-name <SSID> ssid <SSID> \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk '<password>' connection.autoconnect yes
nmcli connection show            # both networks listed, type wifi
```

If both are in range, the higher `connection.autoconnect-priority` wins (default 0). For a
hidden SSID, add `802-11-wireless.hidden yes`. A 5 GHz network needs the Wi-Fi country set
(§1, or `sudo raspi-config` → Localisation → WLAN Country).

## 3. The touchscreen

### 3a. Waveshare-class 800×480 panel

The panel stays black until the Pi is told it is there:

```
sudo nano /boot/firmware/config.txt
```

Under the existing `dtoverlay=vc4-kms-v3d` line (it is there by default — do not add a
second one), add:

```
dtoverlay=vc4-kms-dsi-7inch
```

(`vc4-kms-dsi-7inch` is correct for Waveshare's **5"** 800×480 panel — it shares the 7"
timing. If the ribbon is on the **other** display connector, use
`dtoverlay=vc4-kms-dsi-7inch,dsi0`.) Save, `sudo reboot`. Touch needs no driver.

Do not confuse it with the **5" DSI LCD (C)**, which is 1024×600 and takes a different
overlay — check the label on the board.

### 3b. Official Touch Display 2, 5"

Nothing to add to `config.txt`; a Pi 5 detects it. It boots **portrait**. On the Pi's
desktop: **Control Centre → Screens → right-click DSI-1 → Orientation → Left** (or
**Right**, whichever puts the ribbon where your mount wants it). Touch rotates with it.

### 3c. Picture upside down (either panel)

Mounting often leaves the panel 180° out. Fix it in the same place, **Control Centre →
Screens → right-click DSI-1 → Orientation**: on the 800×480 panel pick **Inverted**; on
Touch Display 2 pick the *other* of Left/Right from the one chosen in 3b. Click **Apply**,
keep it when asked. kanshi re-applies it at every boot, and the kiosk inherits it — set it
only here, not also in `config.txt` or `cmdline.txt`, or taps land mirrored (§9).

### Check

```
wlr-randr          # run on the Pi's desktop terminal: lists the DSI output and its mode
```

**Pass:** the desktop fills the panel in landscape and a tap lands where your finger is.

## 4. Copy the code to the Pi

The repo is not under git yet (README, *Your calls* item 3), so copy the parts the Pi
runs. From the PC — these lines work the same in PowerShell and in Git Bash:

```
cd ~/Box/Projects/Finger_Light
tar czf fl.tgz --exclude=__pycache__ app firmware tools data lightscore README.md
scp fl.tgz kevin@fingerlight.local:
ssh kevin@fingerlight.local "mkdir -p ~/Finger_Light && tar xzf fl.tgz -C ~/Finger_Light && rm fl.tgz"
rm fl.tgz
```

(It asks for the Pi's password twice. Do **not** shorten it to `tar czf - … | ssh …`
in Windows PowerShell 5.1: that shell re-encodes binary data passed through a pipe, and the
Pi answers `gzip: stdin: not in gzip format`. The pipe is fine in Git Bash or PowerShell 7.4+.)

That is about 1 MB; `sim/`, `hardware/` (108 MB of CAD) and `scoreforge/` stay on the PC.
Run the same lines again whenever the code changes — it overwrites in place and leaves your
settings, history and scores alone (they live in `~/.fingerlight`, not in the repo).

## 5. System setup

On the Pi:

```
cd ~/Finger_Light
bash tools/hw/setup_pi.sh            # dry run: prints every change it would make
bash tools/hw/setup_pi.sh --apply
sudo reboot
python3 tools/hw/preflight.py        # must be all PASS
```

It installs the packages (LED driver, MIDI, numpy, Chromium), enables SPI on GPIO10, raises
the SPI buffer, and adds you to the `spi`, `gpio` and `audio` groups.

> **Changed 2026-09-30:** Trixie has **no `python3-sounddevice` package**, and naming one
> apt cannot find fails the entire install. The script now installs `libportaudio2` from
> apt and the `sounddevice` wheel with `pip3 install --user --break-system-packages`
> (PEP 668's opt-in; it writes only to `~/.local`).

## 6. Run the app once, by hand

```
python3 app/server.py --host 0.0.0.0
```

It prints `Finger_Light app on http://127.0.0.1:8080/`. Open that in Chromium on the
panel, or `http://fingerlight.local:8080/` on a phone on the same Wi-Fi. The pills at the
top right say what it can hear: **USB-MIDI** (green) once the piano is plugged into a USB
port and switched on, **no piano** if MIDI works but nothing is attached.

With the strip not wired yet the LED output still runs (SPI with nothing on the end is
harmless). To be explicit about it: `--leds none`. `Ctrl+C` to stop.

## 7. Make it the kiosk

```
bash app/deploy/install_app.sh          # dry run
bash app/deploy/install_app.sh --apply
```

It does three things, all printed before they happen:

1. installs `fingerlight.service`, which starts the server at boot and restarts it if it
   dies;
2. adds `app/deploy/kiosk.sh` to `~/.config/labwc/autostart` — it waits for the server,
   then opens Chromium full screen with its own profile (no "restore pages?" bubble). The
   first time, it seeds that file from `/etc/xdg/labwc/autostart` so the panel and the
   screen-rotation service keep starting;
3. creates `~/.fingerlight/scores`.

Then, **from a terminal on the Pi's desktop** (it edits the session it finds running):

```
sudo raspi-config nonint do_blanking 1      # never blank the screen
sudo reboot
```

**Pass:** after the reboot the panel goes straight to the Library, full screen, and taps
work.

| You want to… | |
|---|---|
| see the server's log | `journalctl -u fingerlight -f` |
| restart it after copying new code | `sudo systemctl restart fingerlight` |
| get out of the kiosk | `Alt+F4` on a keyboard; or over SSH `pkill -f kiosk.sh; pkill chromium` |
| stop it starting at all | `sudo systemctl disable --now fingerlight`, and delete the kiosk line from `~/.config/labwc/autostart` |

## 8. Scores

The Pi plays **light-scores** (`*.lightscore.json`); compiling sheet music into one is a
PC-side step (D-23). On the PC:

```
python tools/compile_score.py piece.musicxml      # or .mxl; writes piece.lightscore.json
scp piece.lightscore.json kevin@fingerlight.local:~/.fingerlight/scores/
```

then **Rescan** in the Library. A `.musicxml`, `.mid` or `.pdf` copied into that folder by
mistake shows up marked *not compiled* rather than disappearing. Two compiled demo pieces
and the two contract examples are always listed, so a fresh Pi has something to play.

## 9. When something is wrong

| Symptom | Likely cause |
|---|---|
| panel black, Pi boots (SSH works) | 3a's overlay missing, ribbon reversed, or a Pi 4 cable |
| desktop portrait on Touch Display 2 | 3b not done; or labwc autostart lost `kanshi` — compare yours with `/etc/xdg/labwc/autostart` |
| taps land in the wrong place | rotation set in a different layer than the display — set it only in Control Centre |
| kiosk shows "can't reach this page" | server not running: `systemctl status fingerlight`, then the journal |
| pill says **no MIDI** | `mido`/`rtmidi` missing — rerun `setup_pi.sh --apply` |
| pill says **no piano** | piano off or not on USB TO HOST; `python3 tools/hw/midi_test.py ports` |
| a red toast "LED output failed" | SPI not enabled or not in the `spi` group — `preflight.py` names which |
| screen goes dark after a few minutes | §7's `do_blanking 1` not run, or run while no desktop session was up — rerun it from the Pi's desktop |

## 10. On the PC: preview without a Pi

```
python app/server.py --preview
```

then open **http://127.0.0.1:8080/preview** — the real app at 800×480, plus the 176-LED
bar and all 88 keys drawn to scale from `data/led_map.json`. Click keys or use the
computer keyboard (`A`…`K` = C4…C5, `Space` plays the lit notes). It is the same server and
the same firmware state machine as the Pi; only MIDI, mic and SPI are switched off.

## Verified vs. not

- **Verified on the PC, 2026-09-30:** every app screen renders at 800×480 (and Play also
  at 1280×720) with no element outside the panel and no touch target under 52 px; the app's 27 tests and
  the firmware's 123 pass; `install_app.sh` and `setup_pi.sh` parse and dry-run.
- **From primary sources, not yet on this hardware:** OS version, package names, both
  panels' setup, Imager's customisation tab, the blanking command.
- **Not verified at all:** anything that needs the Pi in hand — the service under systemd,
  the labwc autostart, Chromium kiosk on the panel, MIDI from the PX-S1100, the mic path,
  SPI to the strip. Write down what differs in [log](../log.md).

## See also

[build](build.md) · [app](app.md) · [hardware](hardware.md) §6 · [firmware](firmware.md) · [score](score.md) · [decisions](decisions.md) D-27, D-28, D-30

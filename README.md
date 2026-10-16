# Inkstant Camera

An instant camera that never runs out of film. Each photo "develops" on a colour e-paper print that slides out of the camera to keep. Every photo is also saved at full size, so you can view and download them from your phone.

## How it works

- **Shoot:** press the shutter. The photo is saved and develops on a 3.6" six-colour e-paper print in about 15 seconds.
- **Keep:** slide the print out to keep it. E-paper holds the image with no power. Slide the same print or a fresh one back in.
- **Share:** press the share button and the print shows a QR code. It opens the photo gallery in your phone's browser, over your home Wi-Fi or the camera's own Wi-Fi. No app needed.
- **Idle:** a slideshow wakes the camera on a timer to show the next photo, even while it's off.

## Hardware

- Raspberry Pi 3A+ (the design also targets the Pi 3B, 3B+, 4B and 5)
- Raspberry Pi camera module with the IMX708 sensor
- Waveshare 3.6" e-Paper HAT+ (E): Spectra 6, 600 × 400
- PiSugar 3 Plus battery: 5000 mAh, real-time clock, power and share buttons
- 12 mm metal push button with an LED ring, used as the shutter and status light
- A 10-pin magnetic pogo connector, so the print slides in and out
- 3D-printed case

## Software

The full blueprint is [SPEC.md](SPEC.md); parts, wiring and assembly are in [HARDWARE.md](HARDWARE.md).

- **Camera app** (`src/inkstant/camera_app`): Picamera2, the e-paper driver, the status light, sleep and battery saver, the slideshow, share mode. Runs as `inkstant.service`.
- **Gallery** (`src/inkstant/gallery`): a small web app for viewing, downloading and deleting photos, and for settings. Runs only while sharing.
- **Setup script** (`setup.sh`, which downloads and runs `install/`): turns a fresh Raspberry Pi OS Lite card into a camera, and can update, reconfigure or uninstall it.
- **Tools** (`tools/`): a desktop simulator, bench tests for real hardware, the palette tuner, and the release builder.

## Building one

1. **Assemble the hardware** as [HARDWARE.md](HARDWARE.md) describes. Switch the PiSugar on, and put a display module in if you have one.
2. **Flash** Raspberry Pi OS Lite (64-bit) with Raspberry Pi Imager. In Imager's settings, set the host name, user and password, your home Wi-Fi and its country, the time zone, and turn on SSH.
3. **First boot:** wait about two minutes, then `ssh <user>@<hostname>.local`.
4. **Run the setup script** on the Pi, in one command. It downloads the latest release (or, before the first release, the latest code on `main`), checks it, and installs it. It takes 15 to 25 minutes on a 3A+, asks a few questions (press Enter for the defaults), and reboots at the end.
   ```bash
   curl -fsSL https://raw.githubusercontent.com/EnriqueNeyra/inkstant-camera/main/setup.sh | sudo bash -s -- --dev
   ```
   `--dev` turns on developer mode, so the camera keeps joining your home Wi-Fi and SSH keeps working after the reboot. Leave it off for everyday use (`… | sudo bash`), or turn it off later in the gallery (Settings → Advanced). Re-running it is always safe.
   - `--ref <branch or tag>` installs that branch or tag instead of the latest release.
   - From a checkout of this repository on the Pi, `sudo ./setup.sh --dev` installs the checkout.
   - Every other option goes to the installer: `sudo ./setup.sh --help`, and `install/install.sh --help` for the full list.
5. **After the reboot:** the status light blinks while the camera starts, then stays on. Press the shutter: the photo develops on the print. Tap BTN: the print shows a QR code. Open the gallery from it, then Settings → Advanced → Setup check: every row should pass.

If something fails, the script stops with the step and the last lines of its log (`/var/log/inkstant-install.log`); [SPEC.md §15.10](SPEC.md) lists the usual causes. `inkstant-ctl selftest` runs the setup check from SSH, and `journalctl -u inkstant` shows the camera app's log.

### Bench tests on the real hardware

`tools/bench/` has one script per hardware test (SPEC.md §19.5). Stop the camera app first, run them with `sudo python3`, and start it again afterwards:

```bash
sudo systemctl stop inkstant
```
```bash
sudo python3 /opt/inkstant/current/tools/bench/camera_fullres.py
```
```bash
sudo systemctl start inkstant
```

| Script | Checks |
|---|---|
| `camera_fullres.py` | Full-resolution capture, camera memory, JPEG and screen-image times, sleep and wake |
| `shutter_latency.py` | Shutter delay at 5, 10 and 14 fps (`--auto` without a button); suggests the frame rate |
| `settle.py` | Focus, exposure and colour settling, with and without the tuning override |
| `epd_show.py` | The display: test chart, step timings, no module, pull-out mid-update |
| `inputs.py` | Shutter, module detect and BUSY edges; `--light` steps through every light pattern |
| `pisugar_probe.py` | PiSugar socket, settings, firmware, clock, alarm, BTN taps, watchdog |
| `poweroff_test.sh`, `wake_cycle.sh`, `boot_timing.sh` | Delayed power cut, slideshow wake from the clock, boot-to-ready time |
| `share_e2e.md` | A checklist for share mode on an iPhone and an Android phone |

Results are saved in `/var/lib/inkstant/bench`. `tools/palette_tuner.py` tunes the dithering colours on the real panel.

## Development

```bash
uv venv --python 3.13 .venv && uv pip install --python .venv -r requirements-dev.txt
```
```bash
.venv/bin/python -m pytest
```
```bash
.venv/bin/python tools/sim.py --port 8080
```

The simulator runs the real camera app with fake hardware: type `s` for the shutter, `b` / `d` for BTN taps, `m` to slide a module in or out, and open `http://localhost:8080` for the gallery. The screen is written to `sim/display.png`.

## Status

Software written and tested against fake hardware and a fake system; next come the bench tests on the assembled camera (SPEC.md §19.6), then tuning the frame rate and palette.

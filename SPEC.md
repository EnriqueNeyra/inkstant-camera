# Inkstant Camera: software specification

An instant digital camera. Each photo "develops" on a colour e-paper display module that pops off the back of the camera to keep. There's no film and no limit on shots, and every photo is also saved at full size for your phone.

This is the complete blueprint for the camera's software. It covers:

- what the camera does, and every rule it follows;
- the exact hardware sequences;
- files, processes and system configuration;
- the phone web app;
- the setup script, the tests and the build order.

Building the software needs nothing else. Parts and assembly are in the build guide in [README.md](README.md); since 2026-10-17 the build has no wiring and no soldering. The high-level summary lives in [Notion](https://app.notion.com/p/3d175cc61a5c81b98561d361f34d53cb).

**Legend:** ✅ confirmed in a primary source (linked) · 🧪 needs a bench or phone test · 📐 our own design choice or estimate. Decisions carry the date they were made.

**Verified 2026-10-15 against:** Raspberry Pi OS Lite 2026-10-06 (Trixie, Linux 6.18.50), Picamera2 0.3.38, libcamera 0.7.2 (Raspberry Pi build `v0.7.2+rpt20261009`), pisugar-server 2.3.5, NetworkManager 1.52.1, Python 3.13.5.

This document replaces DESIGN.md, SOFTWARE.md, GALLERY.md and SETUP.md (merged 2026-10-15). It's the design record; the README is the public build guide (§2.1, 2026-10-17).

## Contents

1. [Product](#1-product): what it does, controls, status light, display modules, power, sharing, gallery, settings, photos
2. [Decisions](#2-decisions)
3. [Research findings](#3-research-findings)
4. [Architecture](#4-architecture): processes, repository, threads, boot, memory
5. [Files, settings and state](#5-files-settings-and-state)
6. [Hardware layer](#6-hardware-layer): GPIO, light, e-paper, camera, PiSugar, watchdog, clock, network
7. [Image pipeline](#7-image-pipeline)
8. [Camera app behaviour](#8-camera-app-behaviour): states, events, timers, sleep, battery saver, slideshow, shutdown
9. [Share mode](#9-share-mode)
10. [Gallery server](#10-gallery-server)
11. [Gallery web app](#11-gallery-web-app)
12. [Control socket](#12-control-socket)
13. [System configuration](#13-system-configuration)
14. [Power and battery life](#14-power-and-battery-life)
15. [Setup script and supported models](#15-setup-script-and-supported-models)
16. [Update and developer mode](#16-update-and-developer-mode)
17. [Diagnostics](#17-diagnostics)
18. [Security](#18-security)
19. [Testing](#19-testing)
20. [Build plan](#20-build-plan)
21. [Open questions](#21-open-questions)

## 1. Product

What the camera does and the rules it follows, from the user's side. Sections 4 to 20 say how the software carries each rule out.

### 1.1 What it does

- **Shoot:** tap the shutter, the battery board's BTN. The photo is saved at full size and "develops" on the colour e-paper display module on the back of the camera. The panel's 15 s colour refresh is the developing.
- **Keep:** pull the module off to keep the photo. E-paper holds its image with no power, and optional magnets in its own case let a module go on a fridge. Put the same module or a fresh one back on.
- **No film:** with no module on, photos are still saved. The newest one develops on the next module put on.
- **Every photo on your phone:** hold the shutter for 2 s and the module shows a QR code. It opens the photo gallery in the phone's browser, over the home Wi-Fi or the camera's own Wi-Fi. No app, no internet, no account.
- **Slideshow:** while the camera is off, it wakes on a timer to put the next photo on the module in the camera (on by default, hourly). Whenever it won't wake itself again, it turns the screen white as it powers off. Take the module off first to keep its photo.
- **Battery:** the camera sleeps after 5 minutes without use and turns off after 10. A battery saver turns itself on at 20%.

### 1.2 Controls

No wiring (decided 2026-10-17): the battery board's two side buttons are the only controls, and the Pi's own green activity LED, seen through a small opening in the case, is the only light. **BTN is the shutter** and also turns sharing on and off; PWR turns the camera on and off. Until 2026-10-17 the shutter was a wired button with an LED ring, and BTN only shared. The parts and how they fit: README.md (What you need, How it fits together).

| Control | Press | What happens |
|---|---|---|
| **BTN** (PiSugar side button: the shutter) | Tap | Take a photo. While asleep, it wakes the camera and takes the photo about 1 s later (§1.5). The PiSugar reports a tap only once its double-tap window has passed, so the photo is taken a moment after the tap (§6.6) |
| | Double tap | Take one photo, as a tap does (decided 2026-10-17) |
| | Double tap while sharing | Switch between home Wi-Fi and the camera's own (decided 2026-10-17, replacing the 2026-10-15 double tap that started sharing on the camera's own Wi-Fi) |
| | Hold for 2 s | Share mode on or off (§1.6; a hold since 2026-10-17, so brushing BTN can't start it). The PiSugar reports a hold when BTN is let go (§6.6) |
| **PWR** (PiSugar side button) | Short press, then press and hold | Turn on (the PiSugar's "anti-mistaken touch", on by default) ✅ |
| | Long press while on | Turn off: any screen update finishes first, then the camera powers off |

**Rules (decided 2026-10-14 and 2026-10-15; sharing revised 2026-10-17):**

- **While a photo develops** (from the tap until the photo is fully shown, about 15 to 20 s), BTN is ignored. The moment the photo is shown it works again. The panel takes about 2.4 s more to power down; a new screen update simply waits for that.
- **PWR during a screen update** is held until the update finishes, then the camera turns off.
- **PWR during a software update** is held until the update finishes; the camera then turns off instead of restarting. 📐
- **While starting:** taps are ignored until the light goes solid. Taps aren't queued.
- **With no display module:** shots can be taken as fast as they save, about 1 to 2 s each. The newest is the undeveloped one (§1.4).
- **During share mode** (decided 2026-10-17, replacing "shooting works"): while sharing starts, is on, or stops, a tap takes no photo, so one can't be taken by accident while the module shows the QR code. The light blinks slowly, and the share screen says to hold the shutter to stop sharing and take photos. Taps take photos again once the module shows the photo again. A photo saved just before sharing started develops when sharing ends.
- **During a slideshow wake:** a BTN tap, double tap or hold, or PWR, switches the camera to normal use. A tap starts the camera but doesn't take a photo; a hold also starts sharing.
- **Storage full:** a tap does nothing (§1.9).
- **No wink means no photo** (decided 2026-10-17): a refused tap gets no wink. Either the light is blinking slowly (wait), or the gallery's banners say why (storage full, a camera problem).

### 1.3 Status light

The Pi's own green activity LED (ACT), seen through a small opening in the case, is the only light (decided 2026-10-17, replacing the ring around the wired shutter). It can only be on or off. Since 2026-10-17 it has just four looks, built around one question, "will a tap take a photo?" (replacing the 2026-10-15 scheme of rhythm families, cues, and the share, asleep and problem patterns):

- **Solid:** yes.
- **Slow blink:** wait, it won't.
- **A wink:** it just did.
- **Off:** the camera is off.

| Light | Meaning |
|---|---|
| Off | The camera is off. Also off during a slideshow wake once the camera knows it's one (decided 2026-10-15); until then it blinks slowly while the Pi starts. At the end of a shutdown, off means it's safe to take the module off |
| Solid | Ready: a tap takes a photo. Also solid while asleep, since a tap still shoots (§1.5). With storage full or a camera problem it stays solid too, but a tap gets no wink (§1.2) |
| Slow blink: 0.5 s on, 0.5 s off | Wait, a tap won't take a photo: starting up; saving and developing a photo; waking to take a photo; sharing, from the moment it starts until it has stopped; a screen update during shutdown, including the white screen; a software update |
| Off for 0.4 s, a "wink" | A tap was accepted: the photo is being taken |

**Everything else shows elsewhere** (decided 2026-10-17): sharing shows its QR code on the module; problems, storage full and the battery level show in the gallery's banners (§11.4.8); the PiSugar's own four LEDs show the charge when PWR is pressed.

**From power-on:** the slow blink starts in early kernel boot, a few seconds after power-on (§6.2). 🧪 Not yet seen on the bench. Before that the firmware drives the LED; 🧪 what it shows in those first seconds on each board.

**Which pattern shows:** starting > wait > ready (§8.6). The wink comes first, then the steady pattern.

The settings page has a "What the light means" sheet with this table (§11.4.6).

### 1.4 Display modules

Each module (the "print" in the README and on the camera's screens) is a complete Waveshare 3.6" e-Paper HAT+ (E): six colours (black, white, yellow, red, blue, green), 600 × 400. Its 40-pin socket pushes straight onto the Pi's header, and it pulls off to keep (decided 2026-10-17): no connector, no jumper wires and no soldering. The case guides it on straight, since a module one pin or one row out joins the Pi's 5 V pins to the wrong pins. The camera tells a module is on from the module's own ID chip (§6.1).

**Swapping while the camera is on** is allowed, as before. 🧪 Not yet known to be electrically safe: the header's pins meet in no set order (5 V, 3.3 V, GND and signals), so check it doesn't reset the Pi or upset the PiSugar (§19.6).

**Undeveloped photos (decided 2026-10-15).** The camera can't read what a module shows: the interface has no data line back to the Pi, and the panel controller's memory is lost when it's switched off ✅. So the camera keeps its own note:

- **At most one undeveloped photo, always the newest.** Taking a photo makes it the undeveloped one. An older photo still waiting counts as developed: it's skipped, but stays in the gallery and the slideshow.
- **Developed** when its screen update finishes on a module.
- **Interrupted:** pulling the module off mid-update keeps the photo undeveloped, until it's developed or a newer photo replaces it.
- **Effect:** a module put on gets the undeveloped photo, if there is one. Otherwise the module is left alone, so a kept photo isn't overwritten and a blank module stays blank.
- **Example:** take five photos with no module on, then put one on. Only the fifth develops.

| Camera state | Module removed | Module inserted |
|---|---|---|
| **Off** | Nothing happens (no power); the module keeps its image | Nothing happens; checked at the next power-on |
| **Starting** | Checked once the app starts | Checked once the app starts; the undeveloped photo, if any, develops |
| **Ready** | Shooting still works, and the newest photo is the undeveloped one | The undeveloped photo, if any, develops; otherwise the module keeps what it shows |
| **Asleep** | Stays asleep | The camera wakes, since a shot is likely. The undeveloped photo, if any, develops 📐 |
| **Developing** | The screen update stops at once and the module is switched off. It may show a half-developed image, like pulling an instant photo early. The photo stays undeveloped, and the buttons unlock | Can't happen: a module is already on |
| **Share mode** | Sharing carries on; the gallery doesn't need the display | The share screen (QR codes) goes on it. When sharing ends, the current photo goes back on |
| **Slideshow wake** | The screen update stops; the camera still schedules the next wake and powers off | Inserted before the update starts: it gets the slideshow photo, or the undeveloped one if there is one |
| **Shutting down** | The screen update stops; shutdown carries on; the photo stays undeveloped | Ignored; handled at the next power-on |

**In every state:**

- Inserting or removing a module counts as use: it restarts the sleep and turn-off timers.
- A normal power-on with nothing undeveloped doesn't update the screen. That keeps start-up fast and doesn't overwrite a kept photo. The one exception: if the module still shows the share screen (left by a restart or a power cut while sharing), its QR codes no longer work, so the current photo goes back on, or a white screen if there's none.
- A slideshow wake shows an undeveloped photo first, if there is one, before going back to the slideshow order.
- **Current photo:** the camera remembers the last photo it put on a module: the newest shot, a slideshow photo, or one chosen with "Show on camera". That's what goes back on when sharing ends.

**Waveshare's rules for the panel** ✅ [Waveshare wiki](https://www.waveshare.com/wiki/3.6inch_e-Paper_HAT%2B_%28E%29_Manual):

- A full refresh takes 15 s and flashes while it runs.
- **At least 180 s between refreshes.** Not enforced (decided 2026-10-14): the next photo can be taken and shown as soon as the previous refresh ends. This is an accepted risk to panel life.
- **Refresh at least once every 24 h**, or the image can burn in. The slideshow covers this for the module in the camera. Whenever the camera won't wake itself again, it turns the screen white as it powers off instead (decided 2026-10-15, replacing a daily redraw). That happens when the slideshow is off, the clock isn't set, there are no photos, or the battery is too low. Waveshare advises storing panels white.
- **Kept modules break these rules** (accepted, decided 2026-10-14). A module on the fridge is never refreshed, and Waveshare warns a long-held image "may burn out and be difficult to repair". Its FAQ says to store panels white, for at most 6 months.
- After each refresh, put the panel to sleep or power it off; leaving it powered can damage it permanently. Our driver does both.
- Rated for about 1,000,000 refreshes. Use between 0 and 50 °C by the spec table, or 0 and 40 °C by the FAQ; cold can cause a colour cast. Long direct sunlight or UV can damage the panel for good. ✅ [Waveshare wiki](https://www.waveshare.com/wiki/3.6inch_e-Paper_HAT%2B_%28E%29_Manual) The README passes all of this on to users.

### 1.5 Power, sleep and battery saver

- **Turning on:** PWR short press, then press and hold. The light blinks slowly while starting, then goes solid when ready: about 12 to 19 s once tuned (20 to 30 s before) 📐 (§4.6). Plugging in to charge doesn't turn the camera on.
- **Turning off:** a long PWR press. Any screen update finishes first. The screen turns white if no slideshow wake is scheduled, then the camera powers off. About 10 s later the PiSugar cuts power completely, so the Pi draws nothing (§6.6, §13.4).
- **Sleep (decided 2026-10-15):**
  - After the "Sleep after" time without camera use (default 5 min), the camera stops its image sensor. That's the largest single draw, about 250 mA of roughly 600 mA (§14).
  - The Pi stays on, and sharing and the gallery keep working.
  - The light stays solid, since a tap still shoots (decided 2026-10-17, replacing a short flash every 4 s).
  - Tapping the shutter wakes the camera and takes the photo about 1 s later, once focus and exposure have caught up (decided 2026-10-15). Putting a module on also wakes it. 📐 🧪
- **Turn off after** (default 10 min) without any use, counted from the last use. With the defaults, that's 5 minutes after it fell asleep (decided 2026-10-15).
  - Paused during share mode (which has its own timeout) and during a software update.
  - In developer mode it never turns off while charging.
- **What counts as use:**
  - For turning off: any BTN tap, double tap or hold (a photo taken or not), putting a module on or taking it off, using the gallery, and any `inkstant-ctl` command except `status`. Using the gallery means taps, page loads and downloads, but not its background status checks.
  - For sleep: only the shutter and modules, since the gallery doesn't need the image sensor.
- **Battery saver (decided 2026-10-15):** off by default; the settings page turns it on and off.
  - **Turns itself on at 20%** on battery (setting "Turn on at 20%", on by default). When it turned on by itself, it turns off once the camera is plugged in. Turned on by hand, it stays on until turned off.
  - **While it's on:**
    - The camera sleeps after 1 min and turns off after 5 min, or after your own times if they're shorter.
    - The camera runs at its lowest frame rate, 5 fps. Focus takes about 1 s to follow a new subject instead of about 0.5 s. The bench chose 5 fps as the normal rate too (`CAMERA_FPS`, §6.4), so for now this changes nothing.
    - Share mode stops after 5 min without use, or your own time if that's shorter. On battery only, like the normal timeout.
- **Low battery (decided 2026-10-15):**
  - **Warning at 15%:** a gallery banner (since 2026-10-17 the light has no battery cue).
  - **Shutdown at 5%** (setting, 4 to 20%). The slideshow alarm is switched off and the screen turns white first, since nothing would refresh it.
  - **Backup:** the PiSugar's own shutdown at 3%.
  - The warning and the battery saver's automatic level move up to 5 points above the shutdown level, if that's higher. 📐
  - The slideshow resumes the next time the camera is turned on with enough charge.
- **Charging (decided 2026-10-15):**
  - Plugging in only charges.
  - While the camera is on, sleep and turn-off still apply.
  - Share mode stays on while plugged in; its timeout applies on battery only.
  - A 2022 report says the PiSugar's timed power-on may not fire while charging. If that's confirmed, the slideshow skips while charging 🧪 (§6.5).
- **Slideshow:**
  - On by default (decided 2026-10-14).
  - Every hour by default (5 min to 24 h); shuffled, or newest first.
  - Optional quiet hours with no changes (decided 2026-10-15).
  - The light stays off. With no module in, a wake skips the refresh. With the clock unset, the slideshow pauses.
- **Clock:** the PiSugar's clock keeps time while the camera is off.
  - It's lost only when the battery is fully drained or removed.
  - It's set from internet time whenever sharing at home, or from a phone on the settings page.
  - While it's unset: a banner, no slideshow, and photos are named by their number only (§6.7).
- **Hang recovery:** if the software freezes, the camera restarts itself within a minute (§6.6).

### 1.6 Getting photos off

Hold BTN, the shutter, for 2 s (BTN decided 2026-10-14; a hold since 2026-10-17). At home the camera joins the home Wi-Fi; away, it makes its own. Either way the phone's normal browser shows the photos: no app, no internet. Details in §9.

1. **Start:** hold BTN for 2 s. The camera first looks for a saved home network for a few seconds. The light blinks slowly, and taps take no photos until sharing has stopped (§1.2).
   - **At home:** the module shows one QR code with the camera's address on the home network (`http://192.168.1.23`, say). The phone is already on that network, so scanning it opens the gallery; a laptop on the same network can open it too.
   - **Away:** the camera starts its own Wi-Fi. The module shows two QR codes, one to join the Wi-Fi and one to open the gallery at `http://10.42.0.1`, plus the network name and password in text.
2. **Save one photo:** tap it, then press and hold the full-size photo. That's "Save to Photos" on iPhone and "Download image" on Android.
3. **Save many:** download the selected photos, a whole day, or everything, as one ZIP.
4. **Stop:** hold BTN again, tap "Stop sharing" in the gallery, or leave it. On battery, sharing stops by itself after 10 minutes without gallery use. The module goes back to the current photo, and taps take photos again once it has.

**With no module on** (decided 2026-10-15), share mode still starts. Join the camera's Wi-Fi with the name and password set during install, then open `http://10.42.0.1`, or `http://<hostname>.local` at home.

**Double tap while sharing** (decided 2026-10-17) to switch from home Wi-Fi to the camera's own, and again to go back. That's for a guest whose phone isn't on your Wi-Fi, or a network that stops devices reaching each other. The share screen says so.

### 1.7 Gallery

The phone web app, specified in full in §11.

- **No login** (decided 2026-10-15). Away, only phones with the camera's Wi-Fi password can reach it. At home, anyone on the home network can use it while sharing is on, including deleting photos and changing settings. That's accepted, since sharing only runs when asked and times out.
- **Layout:** newest first, grouped by day, square thumbnails. Tap one for the full photo, and press and hold to save it.
- **Select mode:** pick photos or whole days to download as one ZIP, or to delete. Everything can also be downloaded at once. Nothing tracks what was downloaded before (decided 2026-10-15).
- **Show on camera** puts a chosen photo on the module in the camera (decided 2026-10-15). It's also how to fill a blank module or redo a kept one. It counts as developed and becomes the current photo.
- **Photo or print:** the photo viewer switches between the photo and the print as the module shows it, in its six colours (added 2026-10-17). The grid keeps one tile per photo.
- **Delete**, with a confirmation. It removes all four files. If the photo was the undeveloped one, nothing is waiting any more.
- **Banners:**
  - Storage low (below 1 GB free) and storage full (below 500 MB).
  - Battery low, clock unset, camera or display problems, a failed setup check, and a software update in progress.
- **Settings page:** §1.8.

### 1.8 Settings

On the gallery's settings page (decided 2026-10-14; sleep and battery saver added 2026-10-15). Defaults are product decisions; the ranges are 📐.

| Setting | Default | Options |
|---|---|---|
| Slideshow | On | On / off |
| Change picture every | 1 hour | 5, 10, 15, 30 min; 1, 2, 3, 6, 12, 24 h |
| Slideshow order | Shuffle | Shuffle · Newest first |
| Quiet hours | Off | From and to times, which can cross midnight |
| Sleep after | 5 min | 1, 2, 3, 5, 10, 15 min, or Never |
| Turn off after | 10 min | 1, 2, 5, 10, 15, 20, 30, 45, 60 min |
| Battery saver | Off | On / off |
| Turn on battery saver at 20% | On | On / off |
| Stop sharing after | 10 min | 5, 10, 15, 20, 30, 45, 60 min without use; on battery only |
| Turn off at (low battery) | 5% | 4, 5, 8, 10, 15, 20% |
| Limit charging to about 80% | Off | On / off. PiSugar says it gives about 8 times the charge cycles ✅; each charge runs about a fifth shorter |
| Home networks | The one set up in Imager | Up to five: add or forget (decided 2026-10-15) |
| Camera Wi-Fi name and password | Set during install | Change; applies the next time sharing starts |
| Date and time | The PiSugar's clock | "Use this phone's time", which also takes the phone's time zone (decided 2026-10-15) |
| Developer mode (Advanced) | Off | On: joins home Wi-Fi at every start so SSH works, and never turns off while charging (decided 2026-10-15) |

- When "Sleep after" is the same as or longer than "Turn off after", the camera turns off without sleeping, and the page says so.
- **Also shown:**
  - The battery level and whether it's charging; whether the battery saver is on, and why.
  - The display module, camera and sharing state; storage used and free.
  - The software version and updates, the setup check, a logs download, and "What the light means".

### 1.9 Photos

- **Four files per photo:**
  - the full 4608 × 2592 JPEG (quality 90, about 3 to 5 MB);
  - a 480 × 270 thumbnail for the gallery;
  - a 600 × 400 PNG of the print as the module shows it, for the gallery's Print view (added 2026-10-17);
  - the ready-to-send screen image for the module.
- **Saving:** they're saved so that a power cut never leaves half a photo (decided 2026-10-15; §5).
- **Filling the screen (decided 2026-10-14):** the screen version is the centre 3:2 of the 16:9 frame, resized to 600 × 400 and dithered to the panel's six colours. The saved original stays the full frame.
- **Names** are a six-digit counter plus the date and time, for example `000123_20261015-143022`. Downloads are `inkstant_<name>.jpg`, and a print `inkstant_<name>_print.png`. Photos sort by the counter, so a wrong clock never breaks the order.
- **Date taken:** each JPEG's EXIF records the shutter press time, so photos saved to a phone sort by when they were taken. With the clock unset, there's no date.
- **Orientation:** there's no tilt sensor, so every photo is landscape relative to the camera body. Camera and display mounting (upright or upside down) are set once per build.
- **Storage:**
  - "Storage low" below 1 GB free, which is about 200 to 300 photos.
  - "Storage full" below 500 MB: the shutter stops. A tap gets no wink, and the gallery's banner says why (decided 2026-10-15; the light's problem pattern for it, added the same day, went on 2026-10-17).
  - A 32 GB card holds several thousand photos.

## 2. Decisions

Every product decision, with the date it was made. The user decides product behaviour; implementation details are ours and are marked 📐 where they appear. Hardware decisions are listed here too; the build itself is in the README, and every part's measurements are in HARDWARE.md. Implementation decisions are in §4.3.

### 2.1 Product

| Date | Area | Decision | § |
|---|---|---|---|
| 2026-10-13 | Power | PiSugar 3 Plus battery board (replaces the PiSugar S Plus) | 1.5, 6.5 |
| 2026-10-14 | Boards | Raspberry Pi 3A+ is the main target; the Pi 3B, 3B+, 4B and 5 are also supported (2026-10-17: the build and its case are for the 3A+ only; the software still supports all five) | 15.1 |
| 2026-10-14 | Display | Waveshare 3.6" e-Paper HAT+ (E) in a slide-out module (2026-10-17: pushed straight onto the header instead) | 1.4 |
| 2026-10-14 | Controls | Shutter with an LED ring on the Pi; power and sharing on the PiSugar's side buttons (2026-10-17: BTN is the shutter, and the ring is gone) | 1.2 |
| 2026-10-14 | Locking | Shutter and BTN locked only while a photo develops; Waveshare's 3-minute spacing not enforced | 1.2, 1.4 |
| 2026-10-14 | Camera | Kept running while the camera is on, for a fast shutter (2026-10-15: sleeps after a set idle time) | 6.4, 8.8 |
| 2026-10-14 | Screen | The photo fills the screen: centre-crop 3:2 | 7.1 |
| 2026-10-14 | Light | Lit from early boot (2026-10-17: the ACT LED blinks from early kernel boot instead) | 1.3, 6.2 |
| 2026-10-14 | Kept modules | Burn-in on modules kept out of the camera is an accepted risk | 1.4 |
| 2026-10-14 | Slideshow | On by default; it and the other settings live on the gallery's settings page | 1.8 |
| 2026-10-14 | Sharing | Home Wi-Fi when in range, otherwise the camera's own; opened by QR code in the phone's browser | 1.6, 9 |
| 2026-10-14 | Setup | Stock Pi OS Lite plus one install script, no read-only filesystem; a prebuilt image later | 15 |
| 2026-10-15 | Display connector | A pre-wired 10-pin magnetic pogo connector (replacing the Mill-Max pair on cost). Dropped 2026-10-17: no connector at all | 2.2 |
| 2026-10-15 | Camera part | Arducam IMX708 75° autofocus (Camera Module 3 equivalent) | README.md (Parts) |
| 2026-10-15 | Modules | At most one undeveloped photo, always the newest; the insert and remove rules | 1.4 |
| 2026-10-15 | Files | One folder per file type; names from a counter plus the date and time | 5.1 |
| 2026-10-15 | Storage | "Low" banner below 1 GB free; "full" below 500 MB stops the shutter | 1.9, 8.15 |
| 2026-10-15 | Shutdown | Every shutdown lets a running screen update finish (up to 30 s) | 8.13 |
| 2026-10-15 | Power-off | Delayed PiSugar power cut armed by a shutdown hook | 13.4 |
| 2026-10-15 | Hangs | The PiSugar's watchdog restarts a frozen camera | 6.6 |
| 2026-10-15 | Battery | Low warning at 15%; shutdown at 5% with the screen turned white | 1.5, 8.10 |
| 2026-10-15 | Charging | Plugging in only charges; sharing stays on while plugged in | 1.5 |
| 2026-10-15 | Clock | Lost clock judged from the RTC; banner, no slideshow, counter-only names | 6.7 |
| 2026-10-15 | Shooting rules | While starting, with no module, during sharing, during a slideshow wake (2026-10-17: no shooting during sharing) | 1.2 |
| 2026-10-15 | Auto-off | Paused during share mode, which has its own timeout | 1.5 |
| 2026-10-15 | Web server | Flask 3.1.1 served by Waitress 3.0.2, both Debian packages | 10 |
| 2026-10-15 | pisugar-server | Web UI and TCP off; socket root-only | 13.5 |
| 2026-10-15 | Gallery | No password: the camera Wi-Fi's password is the only lock (replacing the 2026-10-14 gallery password) | 1.7, 18 |
| 2026-10-15 | Captive portal | No sign-in pop-up; the gallery opens from its QR code | 9.4 |
| 2026-10-15 | Sharing | Works with no module in; the share screen goes on a module slid in | 1.6 |
| 2026-10-15 | Gallery | Newest first by day; select mode; "Show on camera"; banners | 11 |
| 2026-10-15 | Settings | Quiet hours; up to five home networks; time and time zone from the phone; update button (at home, charging or above 30%) | 1.8, 16.1 |
| 2026-10-15 | Light, slideshow | Off during a slideshow wake once the camera knows it's one | 1.3 |
| 2026-10-15 | Downloads | Nothing tracks what was downloaded; download selected photos, a day or everything | 11.4.4 |
| 2026-10-15 | White screen | No timed redraw. The screen turns white at power-off whenever no wake is scheduled (slideshow off, clock unset, no photos, battery too low). Slide the module out first to keep its photo | 1.4, 8.13 |
| 2026-10-15 | Developer mode | Under Advanced, off by default: home Wi-Fi at every start for SSH, and no auto-off while charging | 16.2 |
| 2026-10-15 | BTN double tap | Share over the camera's own Wi-Fi even at home (left to us; adopted). 2026-10-17: a double tap takes a photo, and while sharing it switches Wi-Fi | 1.2, 9 |
| 2026-10-15 | Time zone | Setting the time from a phone also takes its time zone | 6.7 |
| 2026-10-15 | Panel power-down | Buttons are free during the panel's last 2.4 s; the next update waits | 1.2, 6.3 |
| 2026-10-15 | Sleep | Sleep after 5 idle minutes and turn off after 10 by default, both configurable | 1.5, 8.8 |
| 2026-10-15 | Asleep shutter | Pressing the shutter while asleep takes the photo about 1 s later | 1.5, 8.8 |
| 2026-10-15 | Battery saver | On at 20% automatically, or by hand. Shorter sleep and turn-off times (1 and 5 min), the lowest frame rate, and sharing stops after 5 min | 1.5, 8.9 |
| 2026-10-15 | Light | Revised scheme: rhythm families, a 0.4 s wink, cues for no module and low battery, an asleep pattern, storage full on the light, slow blink while starting, during shutdown screen updates and during software updates (2026-10-17: four looks instead) | 1.3, 6.2, 8.6 |
| 2026-10-17 | Build | No wiring and no soldering. The display module (the "print") pushes straight onto the Pi's 40-pin header and pulls off to keep, replacing the magnetic pogo connector and its jumper wires. Print detect moves from pin 15 (GPIO22, not connected on the HAT) to ID_SD (GPIO0, pin 27) | 1.4, 6.1 |
| 2026-10-17 | Shutter | The PiSugar's BTN is the shutter, replacing the wired uxcell button on GPIO27: a tap takes a photo, and so does a double tap unless sharing. A 2 s hold turns sharing on or off; a double tap while sharing switches Wi-Fi. The camera reads BTN itself every 20 ms; the PiSugar's double-tap wait before a single tap was judged acceptable on the bench | 1.2, 1.6, 6.6 |
| 2026-10-17 | Light | The Pi's own green ACT LED through a light pipe (later the same day: through a small opening in the case, with no light pipe), replacing the shutter's LED ring on GPIO26. Four looks: solid (a tap takes a photo), slow blink (it won't), the wink (it did), off. The share, asleep and problem patterns and both cues are gone; sharing shows on the module, problems and the battery in the gallery | 1.3, 6.2, 8.6 |
| 2026-10-17 | Docs | HARDWARE.md retired: this spec is the design record, and the README is the public build guide. HARDWARE.md held the parts list, the wired build (wiring, the connector and button assembly), the case requirements and the hardware bench tests. The facts still needed are now in §1.4, §3, §6 and §19.6 | — |
| 2026-10-17 | Sharing | No photos while sharing starts, is on, or stops; the light blinks slowly, and the share screen says to hold the shutter to stop sharing and take photos | 1.2, 9 |
| 2026-10-17 | Docs | A new HARDWARE.md, written from scratch as the hardware reference for designing the case: every part's dimensions in one coordinate frame, the stack-up, what the case has to do, the measurements still needed, and links to the drawings, schematics and 3D models. SPEC.md and HARDWARE.md are published in the repo, and the README links to both as design notes | — |
| 2026-10-17 | Boards | The build is for the Pi 3A+ only: the README's build guide, HARDWARE.md and the case cover just that board. It's the cheapest and smallest, uses the least power, has the PiSugar 3 Plus's outline (which leaves the print's 22 mm overhang free for the camera), and stays in production until at least January 2030 ✅ [product page](https://www.raspberrypi.com/products/raspberry-pi-3-model-a-plus/). The software still supports the 3B, 3B+, 4B and 5, so anyone can design a case for another board, and the bench camera stays a Pi 4 | 15.1 |
| 2026-10-17 | Case | Each print has its own printed enclosure, separate from the camera body, so it pops out like a piece of film, with pockets for optional magnets so a kept print can go on a fridge. Every part is 3D-printed, in as few parts as possible, without excessive supports. It holds together with no extra hardware: no screws, glue or inserts beyond the screws that come with the PiSugar. No light pipe or second material: the ACT LED shines out through a small opening in the case. The display isn't shaded or recessed against sunlight; that small risk is accepted. Real 3D models are used where they exist | HARDWARE.md |

### 2.2 Rejected

| Option | Why not |
|---|---|
| One button wired to the PiSugar's power pads, with its power bit read over I2C as the shutter | Undocumented; needs fast polling; a slightly long press could shut the camera down; a double-press for sharing would add shutter lag. (BTN itself became the shutter on 2026-10-17, read through its documented tap register, with that lag accepted: §6.6) |
| A wired shutter button with an LED ring on the header (the plan until 2026-10-17) | Needs soldering or crimping at the button; a blue ring is dim at 3.3 V. Not plug-and-play for a beginner |
| A connector between the camera and each module, fed by jumper wires (the plan until 2026-10-17) | Every module needs its cable cut, joined and insulated, and the camera half needs ten jumper wires. The HAT's own socket on the header needs none |
| Always-on Wi-Fi | Phones can drop a network with no internet, and a phone that joined once may auto-join the camera instead of home Wi-Fi. Canon advises turning off auto-join for a camera network and turning off Wi-Fi Assist on iPhone ✅ [Canon community](https://community.usa.canon.com/t5/EOS-DSLR-Mirrorless-Cameras/EOS-Rebel-T6-Won-t-Connect-to-iPhone/m-p/496393/highlight/true). An iPhone can keep using mobile data while on a camera's Wi-Fi ✅ [Apple community](https://discussions-cn-prz.apple.com/thread/251425604) |
| HTTPS | §9.7 |
| Captive-portal pop-up | §9.7 |
| Bluetooth photo transfer | Web Bluetooth isn't supported in Safari or any iOS browser ✅ [caniuse](https://caniuse.com/web-bluetooth) |
| Read-only filesystem (`enable_overlayfs`) | Photos live on the main partition and must survive power-off; with it, everything written after setup is lost at reboot ✅ [TinyPilot](https://tinypilotkvm.com/faq/read-only-filesystem). Safe saving and clean shutdowns protect the card instead |
| Pulling the SD card to get photos | Not readable on Windows with stock Pi OS (ext4) ✅ [The Pi Hut](https://support.thepihut.com/hc/en-us/articles/360017831558-SD-Card-shows-incorrect-capacity-in-Windows); it would need a custom image with a photos partition |
| The 3A+ as a USB drive | Its port is host-only by default; gadget mode needs a non-standard A-to-A cable ✅ [Nerves rpi3a docs](https://hexdocs.pm/nerves_system_rpi3a/readme.html) |
| A photos partition with stock Pi OS | Pi OS grows the root partition to fill the card on first boot, before any script runs, and a mounted ext4 partition can be grown but not shrunk ✅ [Proxmox list](https://lists.proxmox.com/pipermail/pve-user/2012-July/004247.html) |
| `DefaultDependencies=no` for the app | The app could start before udev has created the SPI, I2C and camera devices; the defaults cost little (changed 2026-10-15) |
| SD-card overclocking | KittenLabs saw no gain, and it risks corruption ✅ [KittenLabs](https://kittenlabs.de/blog/2024/09/01/extreme-pi-boot-optimization/) |
| Speed-costing battery savings | §14.4 |

## 3. Research findings

Facts checked against source code, packaging and vendor docs while writing this spec. Each one changes or confirms how the software is built.

### 3.1 Findings that changed the design

1. **pisugar-server is wide open by default.** Its Debian package asks (debconf) for a listening address (default `0.0.0.0`), web UI (default on: HTTP 8421, WebSocket 8422), TCP (default on: 8423) and Unix socket (default on). Its post-install script rewrites `/etc/default/pisugar-server` from those answers on every install or upgrade. The TCP and socket interfaces take commands with no authentication (for example `set_battery_output false`). The web UI's default login is admin/admin, and the socket is created mode 666. In share mode at home, anyone on the home network could reach all of it. **So:** preseed web and TCP off, keep the socket, and add `--uds-mode 600` with a systemd drop-in. ✅ [templates](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/pisugar-server/debian/templates), [postinst](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/pisugar-server/debian/postinst), [main.rs](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/pisugar-server/src/main.rs), [README](https://github.com/PiSugar/pisugar-power-manager-rs#unix-domain-socket-websocket--tcp-api)
2. **One shell command serves both PWR and low battery.** pisugar-server runs `soft_poweroff_shell` when the PiSugar flags a soft power-off (PWR long press). It runs the same command when the battery stays under `auto_shutdown_level` for `auto_shutdown_delay` seconds, and then about once a second until the Pi goes down. **So:** `inkstant-ctl power-button` must be idempotent, and the camera app decides between "turn on" and "shut down" using the battery level as well as its mode. ✅ [main.rs](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/pisugar-server/src/main.rs), [lib.rs](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/pisugar-core/src/lib.rs)
3. **A race on the PiSugar's write lock.** pisugar-server unlocks register 0x0B before each of its own writes and locks it right after. Turning on the watchdog needs the same unlock, so either side's lock can cancel the other's write. Feeding the watchdog and clearing taps aren't write-protected. **So:** the app reads back every protected write and retries. The shutdown hook runs after pisugar-server has stopped, so it has no race. ✅ [pisugar3.rs](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/pisugar-core/src/pisugar3.rs), [PiSugar I2C docs](https://docs.pisugar.com/docs/product-wiki/battery/pisugar3/pisugar-3-i2c)
4. **pisugar-server has no watchdog command.** The app drives registers 0x06 and 0x07 itself with smbus2 (`python3-smbus2` 0.4.3 is already in Pi OS Lite). ✅ [cmds.rs](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/pisugar-server/src/cmds.rs)
5. **pisugar-server's config file isn't saved atomically** (truncate, then write). After a corrupt file it starts with defaults, which turn soft power-off off, so a long PWR press would cut power. **So:** the camera app checks and re-applies every PiSugar setting it depends on at each boot. ✅ [config.rs](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/pisugar-core/src/config.rs), [lib.rs](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/pisugar-core/src/lib.rs)
6. **How the alarm really works.** `rtc_alarm_set` takes an ISO 8601 time with a UTC offset plus a weekday mask. pisugar-server converts it to a UTC time of day, writes it to the PiSugar, and saves it in its config. It re-arms that saved alarm every time it starts, and refuses to set one while `auto_power_on` is on. ✅ [cmds.rs](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/pisugar-server/src/cmds.rs), [rtc.rs](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/pisugar-core/src/rtc.rs)
7. **The PiSugar 3 has a DS3231-compatible RTC at I2C address 0x68** that PiSugar documents for `dtoverlay=i2c-rtc,ds3231` and `hwclock`. The kernel sets the system clock as soon as `rtc0` registers (`RTC_HCTOSYS`, on by default), even when the driver is a module. ✅ [PiSugar RTC docs](https://docs.pisugar.com/docs/product-wiki/battery/pisugar3/pisugar-3-rtc), [class.c](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/drivers/rtc/class.c), [Kconfig](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/drivers/rtc/Kconfig)
8. **`rtc_web` does internet time in one command:** SNTP from `pool.ntp.org`, then it sets both the system clock and the PiSugar RTC. **So** `systemd-timesyncd` isn't needed. ✅ [lib.rs](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/pisugar-core/src/lib.rs), [cmds.rs](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/pisugar-server/src/cmds.rs)
9. **CMA defaults to 256 MB on every board.** `vc4-kms-v3d` includes the `cma` overlay, whose size is fixed at 256 MB "for backwards compatibility", so even the 512 MB 3A+ gets 256 MB. **So:** config.txt sets `cma-128` explicitly. ✅ [cma-overlay.dts](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm/boot/dts/overlays/cma-overlay.dts)
10. **Camera memory depends on a config file Picamera2 picks.** On Pi 4 and earlier, libcamera's default pipeline settings add two internal raw buffers and one internal main-size buffer. That is about 117 MB of CMA for our configuration. Picamera2's `__init__.py` points `LIBCAMERA_RPI_CONFIG_FILE` at `rpi_apps.yaml` (raw and main marked mandatory), which drops them: about 69 MB. **So:** the install script checks that `/usr/share/libcamera/pipeline/rpi/vc4/rpi_apps.yaml` exists (it comes in Raspberry Pi's `libcamera-ipa` package ✅ [archive contents](https://archive.raspberrypi.com/debian/dists/trixie/main/Contents-arm64.gz)), and the app logs libcamera's "Using configuration file" line. ✅ [vc4.cpp](https://github.com/raspberrypi/libcamera/blob/main/src/libcamera/pipeline/rpi/vc4/vc4.cpp), [rpi_apps.yaml](https://github.com/raspberrypi/libcamera/blob/main/src/libcamera/pipeline/rpi/vc4/data/rpi_apps.yaml), [picamera2/\_\_init\_\_.py](https://github.com/raspberrypi/picamera2/blob/v0.3.38/picamera2/__init__.py)
11. **Picamera2 already writes EXIF** on the YUV420 path: Make "Raspberry Pi", Model set to libcamera's long camera ID, and `DateTimeOriginal` set to the time of saving. Its `exif_data` argument replaces whole IFDs. **So** we pass our own `0th` and `Exif` IFDs. ✅ [request.py](https://github.com/raspberrypi/picamera2/blob/v0.3.38/picamera2/request.py)
12. **`capture_request(flush=…)`** takes `True` or a `CLOCK_MONOTONIC` timestamp in nanoseconds, and returns the first frame whose exposure started at or after it. **So** the app passes the timestamp taken as the BTN tap is handled (§8.4), not the time the capture call runs. ✅ [picamera2.py](https://github.com/raspberrypi/picamera2/blob/v0.3.38/picamera2/picamera2.py)
13. **IMX708 exposure modes** (Pi 4 and earlier tuning): Normal goes to 60 ms at 4× gain before 16× gain at 66.7 ms; Short stops at 20 ms and 6× gain before going longer. **So** the default is `AeExposureMode: Short`, to limit handheld blur indoors. 🧪 Compare both. ✅ [imx708.json](https://github.com/raspberrypi/libcamera/blob/main/src/ipa/rpi/vc4/data/imx708.json)
14. **Imager's Wi-Fi goes through netplan.** Imager writes cloud-init files on the boot partition, and cloud-init copies the network config into `/etc/netplan/`. NetworkManager generates runtime profiles from it under `/run/NetworkManager/system-connections/`, but leaves native keyfiles in `/etc/NetworkManager/system-connections/` alone. **So** the install script copies the home network into our own keyfile and retires the netplan one. ✅ [Raspberry Pi news](https://www.raspberrypi.com/news/cloud-init-on-raspberry-pi-os/)
15. **NetworkManager hotspots.** NetworkManager 1.52 builds a WPA2 profile (RSN, CCMP, WPA-PSK) for a hotspot. Its shared-mode dnsmasq reads extra options from `/etc/NetworkManager/dnsmasq-shared.d/`. `nmcli --offline` prints a keyfile without touching the daemon, which handles SSID escaping for us. ✅ [devices.c](https://gitlab.freedesktop.org/NetworkManager/NetworkManager/-/blob/1.52.0/src/nmcli/devices.c), [nm-dnsmasq-manager.c](https://gitlab.freedesktop.org/NetworkManager/NetworkManager/-/blob/1.52.0/src/core/dnsmasq/nm-dnsmasq-manager.c)
16. **Waitress buffers up to 16 MB** of pending output per connection before it pauses a streaming response, and spills anything over 1 MB to a temp file. ✅ [Waitress arguments](https://docs.pylonsproject.org/projects/waitress/en/stable/arguments.html) Debian 13 stores `/tmp` in RAM (up to half of it). ✅ [Debian release notes, section 5.1.6](https://www.debian.org/releases/trixie/release-notes/issues.en.html) **So** the gallery pauses streaming at 4 MiB instead, and its temp files go to a private `/tmp` (🧪 confirm Pi OS keeps Debian's tmpfs with `findmnt /tmp`).
17. **`DefaultDependencies=no` isn't safe here:** the app would start before udev has created `/dev/spidev0.0`, `/dev/i2c-1` and the camera devices. Default dependencies cost little, because nothing waits for the network. 📐
18. **The current kernel is 6.18.50.** Every overlay used here reads the same in the 6.18 overlay docs as in 6.12 (checked by diff), and every kernel link here points at `rpi-6.18.y`. ✅ [release notes](https://downloads.raspberrypi.com/raspios_lite_arm64/release_notes.txt), [overlay README](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm/boot/dts/overlays/README)
19. **Since the 2026-04-13 image, sudo asks for a password by default.** The builder instructions say so. ✅ [release notes](https://downloads.raspberrypi.com/raspios_lite_arm64/release_notes.txt)
20. **libcamera's exposure, colour and focus loops count frames, not seconds.**
    - **Their settings assume about 30 fps:**
      - Exposure moves 15% of the way to its target each frame (`speed` 0.15 in imx708.json).
      - White balance moves 5% a frame (default `speed` 0.05) and is recalculated every 10 frames.
      - Continuous autofocus moves the lens only after 4 frames agree on the direction (fixed in the code). It moves at most 1.5 dioptres a frame, and not at all for 5 frames after each camera start (`skip_frames`).
    - **At 5 fps, all of that takes six times as long.** Exposure takes about 1.6 s to settle after a big change of light, colour 5 to 10 s, and refocusing about 1 s. 📐
    - Their only purpose is a smooth live picture, and nobody watches our stream.
    - Neither loop has a fast start-up phase after a restart in the same process; both start over only when the process opens the camera.

    **So:** a tuning override rescales the counts for our frame rate, and M1 picks the frame rate partly on how fast focus settles (§6.4). ✅ [agc_channel.cpp](https://github.com/raspberrypi/libcamera/blob/main/src/ipa/rpi/controller/rpi/agc_channel.cpp), [awb.cpp](https://github.com/raspberrypi/libcamera/blob/main/src/ipa/rpi/controller/rpi/awb.cpp), [af.cpp](https://github.com/raspberrypi/libcamera/blob/main/src/ipa/rpi/controller/rpi/af.cpp), [imx708.json](https://github.com/raspberrypi/libcamera/blob/main/src/ipa/rpi/vc4/data/imx708.json)
21. **The first frames after the camera opens aren't settled, and Picamera2 doesn't skip them.** libcamera flags the first 6 frames as `FrameStartup`, because exposure and colour are still converging. Picamera2 0.3.38 never reads that flag, so `flush` could return one of them. **So:** the app runs the first 10 frames at 14 fps before reporting ready (§6.4). ✅ [ipa_base.cpp](https://github.com/raspberrypi/libcamera/blob/main/src/ipa/rpi/common/ipa_base.cpp), [pipeline_base.cpp](https://github.com/raspberrypi/libcamera/blob/main/src/libcamera/pipeline/rpi/common/pipeline_base.cpp), [picamera2.py](https://github.com/raspberrypi/picamera2/blob/v0.3.38/picamera2/picamera2.py)
22. **Full-resolution readout takes 68 ms, so the sensor tops out at 14.35 fps.** The driver gives 2,592 lines of 15,648 pixel clocks at 595.2 MHz, plus 58 blanking lines; binned 2304 × 1296 reads out in 17 ms. After the stream stops, the driver powers the sensor down 5 s later. ✅ [imx708.c](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/drivers/media/i2c/imx708.c)
23. **pisugar-server reads BTN only every 500 ms** (checked 2026-10-17). Its main loop ticks every 100 ms, but its PiSugar 3 poll returns early unless 500 ms have passed. Each poll reads register 0x08, where bits 0–1 hold the last tap (1 single, 2 double, 3 long), and clears a tap by writing the register back with those two bits cleared. That's too slow for a shutter. **So:** the camera reads 0x08 itself every 20 ms, and drops the copy when both readers get the same tap (§6.6). ✅ [pisugar3.rs](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/pisugar-core/src/pisugar3.rs) (`read_tap`, `reset_tap`, `poll`), [lib.rs](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/pisugar-core/src/lib.rs) (`I2C_READ_INTERVAL`)
24. **The module's ID chip can detect it, with no wire.** The HAT+ (E) doesn't connect the old detect pin (§2.1, 2026-10-17). It does have an ID chip on ID_SD and ID_SC (GPIO0 and 1, pins 27 and 28) with its own pull-ups, which hold ID_SD high even against the Pi's internal pull-down; with no HAT on, the pull-down reads low. ✅ bench Pi 4 (2026-10-17: `pinctrl` reads high with `pd`, and the chip answers at 0x50 on i2c-0 with "R-Pi"). `force_eeprom_read=0`, already in config.txt for boot speed, stops the firmware reading that chip at power-up ✅ [config.txt boot docs](https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/config_txt/boot.adoc). **So:** GPIO0 is the detect pin, read as a plain input (§6.1).
25. **The Pi's own ACT LED can be the status light.** Every supported board's device tree has an `ACT` LED whose default trigger the `act_led_trigger` parameter sets; the Pi 3B drives it through the firmware's virtual GPIO. ✅ [Pi 3B](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm/boot/dts/broadcom/bcm2710-rpi-3-b.dts), [Pi 3B+](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm/boot/dts/broadcom/bcm2710-rpi-3-b-plus.dts), [Pi 4B](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm/boot/dts/broadcom/bcm2711-rpi-4-b.dts), [Pi 5](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm64/boot/dts/broadcom/bcm2712-rpi-5-b.dts). On the bench Pi 4, `/sys/class/leds/ACT` lists the `timer` and `pattern` triggers ✅ (2026-10-17). **So:** `dtparam=act_led_trigger=timer` replaces the `gpio-led` overlay, and nothing on the header is used for a light (§6.2).
26. **NetworkManager's `wpa-psk` already means WPA2 or WPA3, except when it auto-connects** (checked 2026-10-17).
    - Its docs define `wpa-psk` as "WPA2 + WPA3 personal" and `sae` as "WPA3 personal only". ✅ [nm-settings-nmcli](https://networkmanager.dev/docs/api/latest/nm-settings-nmcli.html)
    - Since 1.32, a `wpa-psk` profile hands wpa_supplicant `SAE` (WPA3) beside `WPA-PSK` whenever the Wi-Fi interface reports SAE, PMF and BIP support, so it uses WPA3 when the network offers it. `sae` offers WPA3 alone and makes PMF required. ✅ [nm-supplicant-config.c](https://gitlab.freedesktop.org/NetworkManager/NetworkManager/-/blob/1.52.1/src/core/supplicant/nm-supplicant-config.c), [commit f5d78c2d](https://gitlab.freedesktop.org/NetworkManager/NetworkManager/-/commit/f5d78c2d289c9e4a4c247d2520c7c3e2baf537c8)
    - But NetworkManager's check of which access points suit a profile matches `wpa-psk` only to networks that offer WPA1 or WPA2, so auto-connect never picks a `wpa-psk` profile for a WPA3-only network. A manual `nmcli connection up` skips that check and lets wpa_supplicant find the network, as it does for hidden ones. The fix is only on NetworkManager's main branch (2026-07-28): 1.58 and older all have the gap. ✅ [nm-setting-wireless.c](https://gitlab.freedesktop.org/NetworkManager/NetworkManager/-/blob/1.52.1/src/libnm-core-impl/nm-setting-wireless.c), [nm-device-wifi.c](https://gitlab.freedesktop.org/NetworkManager/NetworkManager/-/blob/1.52.1/src/core/devices/wifi/nm-device-wifi.c) (`check_connection_available`, `act_stage1_prepare`), [commit 6b56139c](https://gitlab.freedesktop.org/NetworkManager/NetworkManager/-/commit/6b56139cf8a5fbdffbad6d22b48a9c8a42ffe5bf)
    - `nmcli device wifi list` names each access point's security: `WPA2` for WPA2-Personal (or 802.1X), `WPA3` for SAE, `WPA1` for the old WPA. A WPA2/WPA3 transition network shows `WPA2 WPA3`; a WPA3-only one shows `WPA3`. ✅ [devices.c](https://gitlab.freedesktop.org/NetworkManager/NetworkManager/-/blob/1.52.1/src/nmcli/devices.c)
    - On the bench Pi 4 (2026-10-17), the installed `wpa-psk` profile for a WPA2/WPA3 router got `key_mgmt` `WPA-PSK WPA-PSK-SHA256 FT-PSK SAE FT-SAE`, and wpa_supplicant joined with `SAE`. ✅ bench

    **So:** networks are saved as `wpa-psk`, which joins WPA2 and transition networks on any chip. When sharing starts, the scan switches a WPA3-only network to `sae`, so every path joins it, developer mode's auto-connect included (§6.8, §9.5).
27. **The 3A+'s Wi-Fi chip does WPA3 with today's firmware, but didn't in 2023.**
    - The 3A+, 3B+, 4 and 5 load the same CYW43455 firmware: Pi OS's `firmware-brcm80211` links each board's `brcmfmac43455-sdio.raspberrypi,…bin` to `cypress/cyfmac43455-sdio.bin`, which picks the "standard" build over the "minimal" one. The 3B loads the CYW43438's instead. ✅ bench Pi 4 (2026-10-17, package 1:20260519-1~bpo13+1+rpt1)
    - The firmware Pi OS shipped in 2023, 7.45.241, had no SAE. 7.45.265 (2023-09) lists `extsae`, SAE done by the host's supplicant, but the kernel then didn't use it. In January 2024 Raspberry Pi got WPA3 working only through iwd, not wpa_supplicant. ✅ [firmware-nonfree #41](https://github.com/RPi-Distro/firmware-nonfree/issues/41)
    - The 6.18 kernel's brcmfmac turns `extsae` into SAE support for wpa_supplicant. ✅ [feature.c](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/drivers/net/wireless/broadcom/brcm80211/brcmfmac/feature.c), [cfg80211.c](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/drivers/net/wireless/broadcom/brcm80211/brcmfmac/cfg80211.c) On the bench Pi 4 with firmware 7.45.265, kernel 6.18.50 and wpa_supplicant 2.10, `iw` reports "Device supports SAE with AUTHENTICATE command", wlan0 lists `sae`, and it joins a WPA2/WPA3 router with SAE. ✅ bench (2026-10-17)
    - 🧪 Not yet tried: a real 3A+, a WPA3-only network, the minimal firmware, and the 3B's chip.

    **So:** a current install can join WPA3-only networks. Where the chip has no SAE, a WPA3-only network can't be joined either way, and WPA2 and transition networks still join through `wpa-psk`.

### 3.2 Confirmed facts this spec relies on

| Fact | Source |
|---|---|
| Pi OS Lite 2026-10-06 already has network-manager 1.52.1, dnsmasq-base, avahi-daemon, systemd-timesyncd, rpi-swap (zram), cloud-init, netplan.io, python3-gpiozero 2.0.1, python3-lgpio, python3-spidev, python3-smbus2 and Python 3.13.5. It lacks Picamera2, Pillow, numpy, Flask, Waitress, qrcode, piexif, i2c-tools and hwclock (util-linux-extra) | ✅ [image package list](https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-10-06/2026-10-06-raspios-trixie-arm64-lite.info) |
| Versions: python3-picamera2 0.3.38, python3-libcamera 0.7.2, python3-simplejpeg 1.8.1 (Raspberry Pi archive); Flask 3.1.1, Werkzeug 3.1.3, Waitress 3.0.2, qrcode 8.2, piexif 1.1.3, Pillow 11.1.0, numpy 2.2.4, i2c-tools 4.4 (Debian Trixie) | ✅ [Raspberry Pi archive](http://archive.raspberrypi.com/debian/dists/trixie/main/binary-arm64/), [Debian packages](https://packages.debian.org/trixie/python3-flask) |
| Waveshare driver: chip select is hardware CE0 (its software CS line is commented out); SPI mode 0 at 4 MHz; native frame 400 × 600 (portrait), and it rotates landscape images 90°; each BUSY release adds 200 ms; after deep sleep it waits 2 s before switching PWR off | ✅ [epd3in6e.py](https://github.com/waveshareteam/e-Paper/blob/master/E-paper_Separate_Program/3.6inch_e-Paper_E/RaspberryPi_JetsonNano/python/lib/waveshare_epd/epd3in6e.py), [epdconfig.py](https://github.com/waveshareteam/e-Paper/blob/master/E-paper_Separate_Program/3.6inch_e-Paper_E/RaspberryPi_JetsonNano/python/lib/waveshare_epd/epdconfig.py) |
| pisugar-server pushes button taps as bare lines (`single`, `double`, `long`) on every client connection, mixed in with `command: value` replies; each socket client is handled concurrently | ✅ [stream.rs](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/pisugar-server/src/stream.rs) |
| Charge limit (about 80%) is `set_battery_input_protect` (register 0x20 bit 7). The command doesn't save it; the config's `bat_protect` is applied at start | ✅ [pisugar3.rs](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/pisugar-core/src/pisugar3.rs) |
| Delayed power cut: unlock 0x0B, write 0x09, clear bit 5 of 0x02, lock | ✅ [PiSugar script](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/scripts/PiSugar3_DelayedShutdown.sh) |
| LED pattern trigger: brightness/duration pairs; an instant step needs a zero-length entry; it's a module (`CONFIG_LEDS_TRIGGER_PATTERN=m`) | ✅ [pattern format](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/Documentation/devicetree/bindings/leds/leds-trigger-pattern.txt), [defconfig](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm64/configs/bcm2711_defconfig) |
| `gpu_mem` defaults to 64 MB on boards with under 1 GB; libcamera uses CMA, not `gpu_mem` | ✅ [Pi docs](https://github.com/raspberrypi/documentation/blob/develop/documentation/asciidoc/computers/legacy_config_txt/memory.adoc) |
| `boot_delay` already defaults to 0 | ✅ [Pi docs](https://github.com/raspberrypi/documentation/blob/develop/documentation/asciidoc/computers/legacy_config_txt/boot.adoc) |
| The firmware maps `vc4-kms-v3d` and `disable-bt` to their Pi 4 / Pi 5 versions | ✅ [overlay map](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm/boot/dts/overlays/overlay_map.dts) |
| gpiozero's lgpio factory opens `gpiochip4` on a Pi 5 if it exists, otherwise `gpiochip0` | ✅ [lgpio.py](https://github.com/gpiozero/gpiozero/blob/master/gpiozero/pins/lgpio.py) |
| Every supported model's kernel has the IMX708, LED pattern trigger, DS1307/DS3231, spidev and i2c-dev drivers; the timer, heartbeat, one-shot and default-on LED triggers are built in | ✅ §15.1.1, [Pi 3/4 config](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm64/configs/bcm2711_defconfig), [Pi 5 config](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm64/configs/bcm2712_defconfig) |

### 3.3 Still open

- **Android with mobile data on:** whether Chrome reaches `http://10.42.0.1` while the phone treats the camera's Wi-Fi as having no internet. Nothing I could reach documents how Android routes this. 🧪 Bench test (§19.6), with fallbacks in §9.4.
- **Hotspot with Protected Management Frames:** forum reports of clients failing to join NetworkManager hotspots that use PMF. The profile turns PMF off; 🧪 test iPhone, Android and a Mac.
- **A WPA3-only home network:** the code and NetworkManager's source say it joins (finding 26), and the bench Pi does WPA3 with a transition router (finding 27). 🧪 Not tried with a router or phone hotspot set to WPA3 only (§19.6).

## 4. Architecture

### 4.1 What runs on the Pi

| Piece | What it is | Runs as | When |
|---|---|---|---|
| **Camera app** (`inkstant.service`) | Python 3.13 asyncio program. Owns the camera, display, shutter, status light, photo store, state, power and share mode | root | Every boot, slideshow wakes included |
| **Gallery** (`inkstant-gallery.service`) | Flask app served by Waitress on port 80 | `inkstant-web` (no privileges) | Share mode only; the camera app starts and stops it |
| **Control command** (`inkstant-ctl`) | Small command-line tool. `pisugar-server` runs `inkstant-ctl power-button` on a PWR long press; it also has status, self-test and developer commands | root | On demand |
| **Shutdown hook** (`inkstant-poweroff`) | POSIX shell script in `/usr/lib/systemd/system-shutdown/`; arms the PiSugar's delayed power cut | root | The last moment of every power-off |
| **pisugar-server 2.3.5** | Vendor daemon: battery, RTC alarm, PWR, and BTN taps (a slower second reader beside the camera app's own, §6.6) | root | Every boot; locked down to its Unix socket |
| **Updater** (`inkstant-update`) | Transient systemd unit that runs a new release's install script | root | When the user taps Update |

### 4.2 How they connect

```text
                ┌──────────────────── Raspberry Pi OS Lite (Trixie) ─────────────────────┐
                │  inkstant.service (camera app, root)                                    │
detect  GPIO0  ─►    ├─ state machine: asyncio loop, one event queue                      │
e-paper SPI0 ◄──►    ├─ display worker thread ── our epd driver ── spidev + gpiozero      │
camera CSI   ◄──►    ├─ camera service ── Picamera2 ── libcamera                          │
ACT LED      ◄───    ├─ LED service ── /sys/class/leds/ACT                                │
                │    ├─ photo store ── /var/lib/inkstant                                  │
                │    ├─ PiSugar client ── /tmp/pisugar-server.sock ──► pisugar-server ─┐  │
                │    ├─ watchdog, BTN taps ── smbus2 ───────────────► I2C 0x57 ◄───────┘  │
                │    ├─ network ── nmcli ──► NetworkManager ── wlan0                      │
                │    └─ control socket /run/inkstant/control.sock ◄─────────┐             │
                │                                                           │             │
                │  inkstant-gallery.service (inkstant-web, port 80) ────────┤             │
                │     reads photos/ and thumbs/ directly, changes via socket│             │
                │  inkstant-ctl power-button ◄── run by pisugar-server ─────┘             │
                │  inkstant-poweroff (system-shutdown hook) ── i2cset ──► I2C 0x57        │
                └─────────────────────────────────────────────────────────────────────────┘
```

### 4.3 Key implementation decisions

| Area | Decision | Status |
|---|---|---|
| Process model | One camera process on every boot. Picamera2, numpy and Pillow are imported lazily, so a slideshow wake never loads them | 📐 |
| Concurrency | One asyncio loop owns all state; hardware work runs in threads that report back as events | 📐 |
| Display driver | Our own port of Waveshare's `epd3in6e` (same commands and timings) on spidev and gpiozero, with BUSY waits that can be aborted | ✅ sequence · 📐 port |
| Camera | Picamera2 still configuration: 4608 × 2592 YUV420 main, 1152 × 648 lores, 2 buffers, continuous autofocus, and a tuning override for the low frame rate. 5 fps to start; M1 picks 5, 10 or 14. Camera memory (CMA) about 69 MB | ✅ · 🧪 on the 3A+ |
| CMA size | 128 MB in config.txt (`vc4-kms-v3d,cma-128`). The overlay's default is 256 MB on every board, the 512 MB 3A+ included | ✅ |
| pisugar-server | Reachable only through its Unix socket, mode 0600. Its defaults also open TCP 8423 with no authentication, and a web UI with the login admin/admin | ✅ |
| Clock | Kernel driver for the PiSugar's DS3231-compatible RTC on Pi 3 and 4; `rtc_rtc2pi` as the fallback and on the Pi 5. Internet time through pisugar-server's `rtc_web` | ✅ · 🧪 |
| Wi-Fi | Our own NetworkManager keyfiles (home networks and the camera's Wi-Fi) replace the netplan profile Imager creates | ✅ |
| Gallery | Unprivileged and sandboxed. It reads photos directly; deletes and settings go through the camera app's control socket | 📐 |
| Photo store | Three sibling folders (`photos/`, `thumbs/`, `epd/`); IDs sort by a sequence number, so a wrong clock never breaks the order | 📐 |
| ZIP downloads | Our own streaming writer: stored, no data descriptors, exact `Content-Length` so the phone shows progress | 📐 · 🧪 iOS and Android |
| systemd | Units keep default dependencies and never wait for the network | 📐 |
| Sleep | Sleep stops the Picamera2 stream but keeps its buffers allocated; waking restarts it at 14 fps and captures once focus has settled | 📐 · 🧪 |

### 4.4 Repository layout and packaging

```text
inkstant-camera/
├── README.md, LICENSE             # the build guide and the license
├── SPEC.md, HARDWARE.md           # this design record, and the hardware reference for the case
├── mockups/gallery.html          # visual mockup of the gallery screens
├── setup.sh                        # one-command install: downloads a release (or main), runs install/install.sh
├── VERSION                         # e.g. 1.0.0; also baked into the build as BUILD_EPOCH
├── pyproject.toml                  # dev tooling only (pytest, ruff); not used on the Pi
├── requirements-dev.txt            # pip pins that match Trixie's apt versions
├── src/inkstant/
│   ├── __main__.py                 # python3 -m inkstant {camera|gallery|ctl}
│   ├── version.py                  # VERSION and BUILD_EPOCH
│   ├── paths.py, device.py         # fixed paths; /etc/inkstant/device.toml loader
│   ├── settings.py, state.py       # user settings and runtime state (schemas, atomic save)
│   ├── store.py                    # photo store: IDs, commit, list, delete, free space
│   ├── atomic.py                   # atomic_write(), fsync helpers
│   ├── ipc.py                      # control-socket protocol: server, client, messages
│   ├── selftest.py                 # the setup check (§17): standard library only
│   ├── hw/
│   │   ├── gpio.py                 # pins via gpiozero (lgpio backend); fake for tests
│   │   ├── led.py                  # status light (the ACT LED) through sysfs: steady pattern, wink; fake
│   │   ├── epd.py                  # e-paper driver (our epd3in6e port); fake writes PNGs
│   │   ├── camera.py               # Picamera2 wrapper, imported lazily; fake
│   │   ├── pisugar.py              # pisugar-server socket client; fake
│   │   ├── pisugar_i2c.py          # watchdog and BTN tap registers through smbus2; fake
│   │   ├── network.py              # nmcli wrapper and keyfile generation; fake
│   │   └── system.py               # poweroff, clock_settime, hwclock, systemctl
│   ├── imaging/
│   │   ├── epdfile.py              # .epd read/write/rotate: standard library only
│   │   ├── exifinfo.py             # exposure and ISO for the gallery's info sheet (piexif only)
│   │   ├── palette.py              # panel colours and codes
│   │   ├── pipeline.py             # lores → screen image; thumbnail; print; EXIF
│   │   └── share_screen.py         # QR codes and text for the share screen
│   ├── camera_app/
│   │   ├── main.py                 # starts the app on the real hardware (inkstant.service)
│   │   ├── app.py                  # CameraApp: lifecycle, events, share mode, timers, sleep, battery, clock, shutdown
│   │   ├── machine.py              # the rules as pure functions: light, shutter, timers, battery saver
│   │   ├── display.py              # display jobs, worker thread, abort
│   │   ├── capture.py              # shutter → files
│   │   ├── slideshow.py            # next photo, next wake time (pure)
│   │   └── update.py               # checking for and fetching a release (§16.1)
│   ├── gallery/
│   │   ├── app.py                  # Flask app factory and routes
│   │   ├── zipstream.py            # streaming ZIP writer
│   │   ├── templates/, static/     # HTML, CSS, vanilla JS; no CDN (§11.8)
│   └── ctl.py                      # inkstant-ctl
├── bin/inkstant-ctl                # #!/usr/bin/python3 wrapper
├── system/                         # files installed into the OS (see §13)
│   ├── systemd/inkstant.service, inkstant-gallery.service, inkstant-failsafe.service
│   ├── systemd/pisugar-server.service.d/inkstant.conf
│   ├── system-shutdown/inkstant-poweroff
│   ├── journald.conf.d/inkstant.conf, system.conf.d/inkstant.conf
│   ├── modules-load.d/inkstant.conf
│   └── NetworkManager/dnsmasq-shared.d/inkstant.conf
├── install/
│   ├── install.sh                  # bootstrap: checks, apt, pisugar-server, then configure.py
│   ├── configure.py                # idempotent system setup (standard library only)
│   ├── update.sh                   # run by the updater
│   ├── uninstall.sh                # §15.9
│   └── pisugar-server.sha256       # pinned .deb checksum
├── tools/
│   ├── bench/                      # one script per bench test (§19.5)
│   ├── palette_tuner.py            # show test charts on the panel; fit the panel's colours (§7.7)
│   ├── build_release.py            # dist/inkstant.tar.gz + SHA256SUMS (§16.1)
│   └── sim.py                      # desktop simulator (§19.2)
├── tests/                          # pytest, runs on any machine
└── .github/workflows/ci.yml        # lint, shellcheck, tests; a v<VERSION> tag publishes the release
```

**Rules:**

- **Target:** Python 3.13 (Trixie). The Pi installs apt packages only: no pip, no virtualenv.
- **Packages on the Pi:** `python3-picamera2` (installed with `--no-install-recommends`, which skips its Qt and OpenGL preview packages), `python3-pil`, `python3-numpy`, `python3-gpiozero`, `python3-lgpio`, `python3-spidev`, `python3-smbus2`, `python3-flask`, `python3-waitress`, `python3-qrcode`, `python3-piexif`, `fonts-dejavu-core`, `i2c-tools`, `util-linux-extra`. Not `python3-systemd`: the app sends its few `sd_notify` messages itself over `NOTIFY_SOCKET`. ✅ all in Trixie or the Raspberry Pi archive (see §3).
- **Dev machine:** a virtualenv with the same versions from pip (`requirements-dev.txt`: Pillow 11.1.0, numpy 2.2.4, Flask 3.1.1, Werkzeug 3.1.3, waitress 3.0.2, qrcode 8.2, piexif 1.1.3, gpiozero 2.0.1, smbus2 0.4.3) plus pytest and ruff. Picamera2 and libcamera exist only on the Pi; tests use fakes.
- **No third-party web assets:** the gallery works with no internet, so its CSS, JS and icons ship in the release.
- **Precompiled bytecode:** the install runs `python3 -m compileall` on the release, so the first boot doesn't compile.

**Where things go on the Pi:**

| Path | What |
|---|---|
| `/opt/inkstant/releases/<version>/` | A release: `src/`, `bin/`, `system/`, `install/` (read-only after install) |
| `/opt/inkstant/current` | Symlink to the active release, switched atomically on update |
| `/usr/local/bin/inkstant-ctl` | Symlink to `/opt/inkstant/current/bin/inkstant-ctl` |
| `/etc/inkstant/device.toml` | Per-build hardware settings (§5.3) |
| `/var/lib/inkstant/` | Settings, state and photos (§5) |
| `/run/inkstant/control.sock` | Control socket (§12) |
| `/etc/NetworkManager/system-connections/inkstant-*.nmconnection` | Wi-Fi profiles (§6.8) |

### 4.5 Inside the camera app

- **One event loop:** the main thread runs asyncio. All state lives in one `CameraApp` object and changes only on the loop, so the state machine needs no locks.
- **Events reach the loop as calls:** GPIO callbacks (gpiozero threads), the BTN reader and the display worker hand each event to the loop with `loop.call_soon_threadsafe`; the PiSugar socket reader, the control socket server and the timers already run on it. Each handler runs to completion on the loop, in order, and asks `machine.py`'s pure rules what's allowed (§8).
- **Worker threads:**
  - **BTN reader:** one dedicated thread that reads the PiSugar's tap register every 20 ms and posts each tap to the loop (§6.6).
  - **Display worker:** one dedicated thread, one job at a time; any job can be aborted (§6.3).
  - **Capture worker:** a one-thread executor for the capture and the encode (§8.4).
  - **Blocking jobs:** a two-thread executor for nmcli, systemctl and file work.
  - Every worker reports back by posting an event; none touches state directly.
- **Lazy imports:** `camera_app/app.py` imports only the standard library, `gpiozero`, `spidev` and our own standard-library modules at the top. Picamera2, numpy and Pillow load in the capture worker the first time the camera starts (§8.11).
- **Liveness:** a loop task feeds the PiSugar watchdog and sends systemd `WATCHDOG=1` every 10 s. If the loop stalls, both stop, so a hung app gets restarted (systemd, at 30 s) or the Pi gets power-cycled (PiSugar, at 60 s).

### 4.6 Boot timeline

📐 Estimates for the 3A+, to be measured in milestone M8 (§20).

| Step | Normal boot | Slideshow wake |
|---|---|---|
| Power on → kernel (firmware) | 4 to 5 s | 4 to 5 s |
| Kernel → `inkstant.service` starts | 4 to 7 s | 4 to 7 s |
| App start: Python, socket to pisugar-server, wake decision | ~1 s | ~1 s |
| Picamera2 + numpy + Pillow imports, camera start | 3 to 6 s 🧪 | not loaded |
| **Ready to shoot** (light solid) | **~12 to 19 s** | — |
| Screen update | only if a photo is undeveloped | ~20 s |
| Shutdown → halted → PiSugar cuts power | — | ~5 s + 10 s |

### 4.7 Memory budget (Pi 3A+, 512 MB)

📐 To be measured in M1 and M8 (§20).

| User | Size | Notes |
|---|---|---|
| Firmware (`gpu_mem`) | 64 MB | Default under 1 GB ✅; leaves 448 MB for Linux |
| CMA reserve | 128 MB | Set in config.txt. Ordinary movable pages can use it while the camera doesn't |
| Camera buffers (from CMA) | ~69 MB | 2 × (raw 14.9 + main 17.9 + lores 1.1) MB, plus one internal lores-size buffer ✅ |
| Kernel, systemd, NetworkManager, pisugar-server, journald | ~60 to 80 MB | 🧪 |
| Camera app | ~100 to 160 MB RSS | Picamera2 also loads PyAV (FFmpeg) through its encoder imports ✅; much of that is shared, clean library pages 🧪 |
| Gallery | ~30 to 40 MB | Share mode only; capped by `MemoryMax=96M` |
| zram swap | rpi-swap default | ✅ present in Lite |

## 5. Files, settings and state

### 5.1 Photo store

```text
/var/lib/inkstant/            root:root 0755
├── photos/   <id>.jpg        root:inkstant-web, dir 0750, files 0640  (full size)
├── thumbs/   <id>.jpg        root:inkstant-web, dir 0750, files 0640  (480 × 270)
│             <id>.png        root:inkstant-web, files 0640             (the print, 600 × 400)
├── epd/      <id>.epd        root:root, dir 0700, files 0600          (screen image)
├── tmp/                      root:root 0700 (same filesystem, for atomic renames)
├── settings.json             root 0600
├── state.json                root 0600
└── slideshow.json            root 0600
```

- **Photo ID:** `NNNNNN_YYYYMMDD-HHMMSS` (six-digit sequence number, local time of the shutter press), or just `NNNNNN` when the clock is unset. Example: `000123_20261015-143022`. 📐
  - IDs sort by sequence number, so the order stays right even if the clock was wrong.
  - The gallery groups by the date in the ID; photos without one go under "Unknown date".
  - Validated everywhere with `^\d{6}(_\d{8}-\d{6})?$`, which also blocks path tricks.
- **Download file names:** `inkstant_<id>.jpg`, and `inkstant_<id>_print.png` for a print.
- **Sequence number:** `state.next_seq`, saved once the photo is committed, so a failed save uses no number (§8.4). At start the app also scans `photos/` and uses `max(existing) + 1` if that's higher, so a lost state file never reuses a number.
- **Free space:** `os.statvfs("/var/lib/inkstant")`, `f_bavail × f_frsize`. "Storage low" below 1 GiB, "storage full" below 500 MiB (§1.9, §8.15).
- **One folder per file type:** the gallery lists one folder, and it can't read the screen images it doesn't need. 📐

### 5.2 Atomic writes and recovery

`atomic_write(final_path, data)`:

1. Write to `tmp/<name>.<pid>.tmp`, opened with `O_CREAT | O_EXCL`.
2. `flush`, `os.fsync(fd)`, close.
3. `os.chmod` to the final mode, and `os.chown` for the group.
4. `os.rename` to the final path (same filesystem, so it's atomic).
5. `os.fsync` on the final folder.

**Committing a photo:** `epd/<id>.epd`, then `thumbs/<id>.jpg`, then `thumbs/<id>.png`, then `photos/<id>.jpg`. The JPEG goes last: a photo exists only once its full-size file is in place.

**At start (recovery):**

- Delete everything in `tmp/`.
- Delete any `epd/` or `thumbs/` file with no matching JPEG (left over from a power cut).
- For any JPEG missing its thumbnail, print or screen image, or with a screen image from an older palette (§7.7), queue a rebuild. The rebuild runs in the background when the camera is idle and uses Pillow's JPEG draft mode for a fast reduced decode.

**Settings and state** are written with the same `atomic_write`, with a schema version, and with a `.bak` copy of the previous version. A file that fails to parse falls back to `.bak`, then to defaults, and the error is logged.

### 5.3 Per-build hardware settings: `/etc/inkstant/device.toml`

Written by the install script (§15.6, step 9), read with `tomllib`, and changed by hand only when the build changes (§1.9: mounting is set once per build). 📐

```toml
model = "Raspberry Pi 3 Model A Plus Rev 1.1"   # from /proc/device-tree/model
family = "pi3"                # pi3, pi4 or pi5 (§15.1.2)
wifi_5ghz = true              # false on the Pi 3B
camera_hflip = false
camera_vflip = false          # "camera upside down" sets both flips (a 180° rotation)
display_rotation = 0          # 0 or 180
camera_port = "cam1"          # Pi 5 only: "cam0" or "cam1"
# camera_fps = 5              # optional override of the release's frame rate (§6.4), for experiments
```

### 5.4 User settings: `settings.json`

Every field is validated on load and on every change. Defaults and options come from §1.8; the gallery offers exactly the listed options, and the server accepts any value in the range. 📐

| Key | Type | Default | Range |
|---|---|---|---|
| `version` | int | 1 | — |
| `slideshow.enabled` | bool | true | |
| `slideshow.interval_min` | int | 60 | 5 to 1440 |
| `slideshow.order` | str | `"shuffle"` | `shuffle`, `newest` |
| `slideshow.quiet_hours` | object or null | null | `{"start": "HH:MM", "end": "HH:MM"}`, start ≠ end, can cross midnight |
| `sleep_min` | int or null | 5 | 1 to 15; null is "Never" |
| `auto_off_min` | int | 10 | 1 to 60 |
| `battery_saver.enabled` | bool | false | Turned on by hand |
| `battery_saver.auto` | bool | true | Turn on by itself at 20% (§8.9) |
| `share_timeout_min` | int | 10 | 5 to 60 (battery only) |
| `low_battery_shutdown_pct` | int | 5 | 4 to 20 (the PiSugar's backup is 3%) |
| `charge_limit` | bool | false | |
| `hotspot.ssid` | str | set at install | 1 to 32 UTF-8 bytes |
| `hotspot.password` | str | set at install | 8 to 63 printable ASCII |
| `dev_mode` | bool | false | §16.2 |

Home Wi-Fi passwords are not kept here. NetworkManager's root-only keyfiles hold them (§6.8), and the gallery only ever sees network names.

### 5.5 Runtime state: `state.json` and `slideshow.json`

`state.json` 📐:

| Key | Meaning |
|---|---|
| `next_seq` | Next photo sequence number |
| `undeveloped` | ID of the one undeveloped photo, or null (§1.4) |
| `current` | ID of the photo last put on a display module, or null |
| `display_content` | `photo`, `share`, `blank` or `unknown`: what the inserted module was last given |
| `next_wake_utc` | Unix time of the armed wake, or null |
| `last_shutdown` | `{reason, at}`, for diagnostics |
| `saver_auto_active` | true while the battery saver is on because the battery reached its automatic level; cleared when plugged in (§8.9) |
| `saver_auto_dismissed` | true after the user turned an automatic saver off; cleared when plugged in (§8.9) |

`slideshow.json`: `{"bag": [seq, …], "last": seq}`. A shuffle bag: the next photo is drawn from the bag, and when the bag is empty it's refilled with every photo except the last one shown. New photos go into the bag at a random position; deleted ones are skipped when drawn. Kept apart from `state.json` because it can hold thousands of numbers. With the order set to newest first, the bag isn't used: the next photo is the newest one older than `last`, wrapping round to the newest after the oldest.

## 6. Hardware layer

Every module in `hw/` has a real version and a fake with the same interface. The tests and the simulator (`tools/sim.py`, §19.2) build the app with the fakes.

### 6.1 GPIO

gpiozero 2.0.1 with the lgpio pin factory (`GPIOZERO_PIN_FACTORY=lgpio` in the unit), the same on every supported board. lgpio is gpiozero's first choice and the only factory that works on the Pi 5. Tests use gpiozero's `MockFactory`. ✅ [gpiozero pins](https://gpiozero.readthedocs.io/en/stable/api_pins.html)

| GPIO (pin) | gpiozero device | Settings | Role |
|---|---|---|---|
| 0 (27, ID_SD) | `DigitalInputDevice` | `pull_up=False` (pull-down), active high, `bounce_time=0.05` | Module detect: the module's ID-chip pull-up holds it high, so high means a module is on (§3 finding 24). ✅ bench Pi 4 (2026-10-17); 🧪 the 3A+, 3B, 3B+ and Pi 5 |
| 24 (18) | `DigitalInputDevice` | `pull_up=False` (pull-down), active high | E-paper BUSY: high = idle, low = busy (or no module) |
| 17 (11) | `DigitalOutputDevice` | `initial_value=False` | E-paper RST |
| 25 (22) | `DigitalOutputDevice` | `initial_value=False` | E-paper DC |
| 18 (12) | `DigitalOutputDevice` | `initial_value=False` | E-paper PWR (low = module switched off) |
| 8, 10, 11 | (kernel spidev) | `/dev/spidev0.0` | CE0, MOSI, SCLK |
| 2, 3 | (kernel I2C) | `/dev/i2c-1` | PiSugar: battery, clock, watchdog and BTN taps (§6.5, §6.6) |

- **Nothing on the header is wired** (decided 2026-10-17). The module is the HAT on the header, on the same pins as Waveshare's own Pi wiring, so the driver needs no change ✅ [Waveshare wiki](https://www.waveshare.com/wiki/3.6inch_e-Paper_HAT%2B_%28E%29_Manual). The shutter is BTN, read over I2C (§6.6), and the light is the board's own ACT LED, `/sys/class/leds/ACT` (§6.2). No header pin is used for a button or a light.
- The SPI driver claims GPIO 7 to 11 and I2C claims 2 and 3, so the app never opens those through gpiozero.
- **GPIO 0 is only read as an input.** Nothing uses the ID chip's I2C bus, and GPIO 1 (ID_SC, pin 28) isn't opened.
- **Module detect:** removal is acted on at once (it aborts a refresh). Putting a module on counts only after the line has stayed high for 300 ms, to ride out contact bounce while the socket slides down the pins. 📐
- **Idle outputs:** with no refresh running, RST, DC and PWR stay low, and the SPI device is closed.

### 6.2 Status light

The light is the Pi's own green ACT LED, `/sys/class/leds/ACT`, seen through a small opening in the case (decided 2026-10-17, replacing an LED ring on the header; §3 finding 25). The patterns and their meanings are in §1.3; which one shows is decided in §8.6.

**From power-on:**

1. While the firmware loads, it drives the ACT LED itself, as on any Pi. 🧪 What it shows then on each board; there's no longer a config.txt line that holds the light on this early.
2. From early kernel boot, `dtparam=act_led_trigger=timer` in config.txt (§13.1) gives the LED the built-in `timer` trigger. With no delays given, the timer trigger blinks 500 ms on and 500 ms off. ✅ [led-core.c](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/drivers/leds/led-core.c), [Pi 4B device tree](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm/boot/dts/broadcom/bcm2711-rpi-4-b.dts). ✅ Bench Pi 4 (2026-10-17): `ACT` has the `timer` and `pattern` triggers on kernel 6.18. 🧪 The boot blink itself isn't seen yet: it needs a reinstall with this config.txt
3. So the camera shows the slow "wait" blink with no software running. The app takes over by writing to `/sys/class/leds/ACT/` (root).

Before the software is installed, the ACT LED keeps Pi OS's default trigger (`mmc0`), so it only flickers with card activity; the README says so.

- **Why not `heartbeat`:** the kernel's heartbeat is a double pulse every 0.7 to 1.3 s, depending on load, which doesn't read as "wait". ✅ [ledtrig-heartbeat.c](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/drivers/leds/trigger/ledtrig-heartbeat.c)
- **On or off only:** the ACT LED is a plain GPIO LED (on the Pi 3B, through the firmware's virtual GPIO), so there's no dimming or fading. ✅ device trees (§3 finding 25)
- **Boards:** every supported board has the `ACT` LED and the `act_led_trigger` parameter (§3 finding 25). 🧪 Only the Pi 4's triggers are tested: check the early blink on every board, the Pi 4 included; the pattern trigger and the light through the case on the 3A+, 3B and Pi 5; and the virtual GPIO's timing on the 3B. On the Pi 5, the green LED shares one bicolour package with the red power LED.

| Pattern (§1.3) | How it's set |
|---|---|
| Starting | Leave the kernel's `timer` trigger running |
| Ready | `trigger` ← `none`, `brightness` ← `1` |
| Off | `trigger` ← `none`, `brightness` ← `0` |
| Wink | `trigger` ← `none`, `brightness` ← `0` for 400 ms, then the steady pattern |
| Wait (0.5 s on, 0.5 s off) | `trigger` ← `pattern`, `pattern` ← `1 500 1 0 0 500 0 0`, `repeat` ← `-1` |

- **Pattern format:** pairs of brightness and milliseconds. Each value is held, then a zero-length entry steps to the next value instantly, as the kernel's format requires for on/off steps. Write `pattern`, then `repeat`. ✅ [pattern format](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/Documentation/devicetree/bindings/leds/leds-trigger-pattern.txt), [sysfs ABI](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/Documentation/ABI/testing/sysfs-class-led-trigger-pattern)
- **`hw/led.py`** keeps one steady pattern and the wink:
  - `set_steady(p)` writes `p` at once, unless the wink is playing; then `p` is written when it ends.
  - `wink()` shows the wink, then the steady pattern.
  - The end of the wink is timed by the event loop, not read back from the kernel.
- **Module:** `ledtrig-pattern` loads at boot from `/etc/modules-load.d/inkstant.conf`. If `pattern` isn't among the triggers listed in `trigger`, `led.py` falls back to blinking from an asyncio task with the same timings.
- **Kernel triggers:** the Pi kernel builds in the timer, heartbeat, one-shot and default-on triggers; the pattern trigger is a module (`CONFIG_LEDS_TRIGGER_PATTERN=m`). ✅ [kernel config](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm64/configs/bcm2711_defconfig)
- **At halt:** the ACT LED is a `gpio-leds` LED, which the kernel switches off when the system shuts down unless its device-tree node sets `retain-state-shutdown`. 📐 🧪 Check that it stays dark from the app's "off" to the power cut, and whether the firmware flashes it at halt.

### 6.3 E-paper driver

Our port of Waveshare's `epd3in6e.py`. It keeps Waveshare's commands, data and delays exactly. It adds BUSY timeouts, abort, PWR held low when idle, and sending a ready-made `.epd` frame. ✅ [epd3in6e.py](https://github.com/waveshareteam/e-Paper/blob/master/E-paper_Separate_Program/3.6inch_e-Paper_E/RaspberryPi_JetsonNano/python/lib/waveshare_epd/epd3in6e.py)

**Bus:** `spidev.SpiDev().open(0, 0)`, `mode = 0`, `max_speed_hz = 4_000_000`. Chip select is hardware CE0, as in Waveshare's driver. Commands are one byte with DC low; each parameter byte is its own one-byte transfer with DC high, exactly as Waveshare's `send_data` does; the 120,000-byte frame goes through `writebytes2`, which splits it into transfers of spidev's buffer size (4096 bytes by default), as Waveshare's does. ✅

**`show(frame: bytes, abort: threading.Event)`**, frame = 120,000 bytes in the panel's native order (§7.2):

| Step | Action | Wait | Timeout |
|---|---|---|---|
| 1 Power | PWR ← 1, open SPI | | |
| 2 Reset | RST 1 (20 ms) → 0 (2 ms) → 1 (20 ms) | BUSY high + 200 ms | 2 s → `ModuleMissing` |
| 3 | sleep 30 ms | | |
| 4 Init | `AA 49 55 20 08 09 18` · `01 3F` · `00 5F 69` · `05 40 1F 1F 2C` · `08 6F 1F 1F 22` · `06 6F 1F 17 17` · `03 00 54 00 44` · `60 02 00` · `30 08` · `50 3F` · `61 01 90 02 58` · `E3 2F` · `84 01` | BUSY high + 200 ms | 5 s |
| 5 Frame | command `10`, then the 120,000 bytes | | |
| 6 Power on | command `04` | BUSY high + 200 ms | 5 s |
| 7 Booster | `06 6F 1F 17 27`, sleep 200 ms | | |
| 8 Refresh | `12 00` | BUSY high + 200 ms → **image shown** | 45 s |
| 9 Power off | `02 00` | BUSY high + 200 ms | 5 s |
| 10 Sleep | `07 A5`, sleep 2 s (Waveshare's delay) | | |
| 11 Off | close SPI, RST ← 0, DC ← 0, PWR ← 0 | | |

(First byte of each group is the command, the rest its data. Command `61`'s data, `01 90 02 58`, is 400 × 600.)

- **Waiting for BUSY:** a `threading.Event` that the BUSY input's `when_activated` callback sets, and that the abort also sets. The wait checks the line's level first, so an edge that already happened isn't missed. No polling.
- **Abort** (module pulled out, or shutdown can't wait any longer): checked at every wait and between steps. It skips straight to step 11 and raises `Aborted`. Sending no sleep command is fine: the module is losing power anyway.
- **A timeout with the module still detected** raises `DisplayTimeout`. The app reports a display error (in the gallery; errors don't show on the light, §8.14) until the next screen update succeeds or the module is reinserted.
- **"Image shown"** is the end of step 8: the driver calls back then, and the app unlocks the buttons. Steps 9 to 11 (about 2.4 s) run as a tail with the buttons free; a new screen update simply waits for the tail to finish. 📐
- **Timing:** about 20 to 22 s per screen update, the image appearing at about 17 to 19 s. 🧪 Measure.
- **No module, or an unpowered one:** BUSY reads low through the pull-down, so step 2 times out after 2 s → `ModuleMissing`. ✅ That backs up the detect pin (§6.1).
- **Waveshare's 3-minute minimum between refreshes is not enforced** (§1.4).

### 6.4 Camera

`hw/camera.py` wraps Picamera2. It's imported in the capture worker the first time the camera starts.

```python
from picamera2 import Picamera2
from libcamera import Transform, controls

picam2 = Picamera2(tuning=tuning)        # tuning: the frame-rate override (below)
config = picam2.create_still_configuration(
    main={"size": (4608, 2592), "format": "YUV420"},
    lores={"size": (1152, 648), "format": "YUV420"},
    # raw: left at its default, the main size, which selects the full 10-bit sensor mode
    buffer_count=2,
    transform=Transform(hflip=dev.camera_hflip, vflip=dev.camera_vflip),
    controls={
        "FrameDurationLimits": (70_000, 70_000),            # 14 fps to settle; then the running rate (start-up, below)
        "AfMode": controls.AfModeEnum.Continuous,
        "AeExposureMode": controls.AeExposureModeEnum.Short,  # 🧪 against Normal
    },
)
picam2.options["quality"] = 90
picam2.configure(config)
picam2.start()
```

- **Still configuration defaults:** the `Sycc` colour space (full-range BT.601, the same as JPEG and Pillow's `YCbCr`) and high-quality noise reduction. ✅ [picamera2.py](https://github.com/raspberrypi/picamera2/blob/v0.3.38/picamera2/picamera2.py)
- **Capture:** `request = picam2.capture_request(flush=press_ns)`, run in the capture worker. Hold the request only while copying the lores frame and encoding the JPEG (§8.4), then `request.release()`.
- **Shutter delay**, in two parts (at 5 fps), timed from the moment the app handles the press. The figures below were measured on 2026-10-16, before BTN was the shutter. With BTN (2026-10-17) that moment comes later than the finger: the PiSugar reports a single tap only once its double-tap window has passed, and the camera's reader adds up to 20 ms (§6.6). ✅ Bench, 2026-10-17: the user judged the delay acceptable. 🧪 Measure the window.
  - **Press to the moment captured: 0 to 200 ms, 100 ms on average.** A new exposure starts every 200 ms, and `flush` takes the first one that starts after the press (§3 finding 12). ✅ Measured on a Pi 4 (2026-10-16): median 132 ms, p90 191 ms.
  - **Press to frame in hand: median 586 ms, p90 735 ms** on a Pi 4 (2026-10-16). ✅ It decides when saving starts, not what the photo shows. The earlier estimate of 0.15 to 0.4 s left out the ISP: at full resolution the Pi 4's ISP passes only about 5 frames a second (one every ~0.2 s, measured), so 10 and 14 fps were slower still (p90 862 and 895 ms), with frames queuing for the ISP. The press-to-photo-saved time through the app is 0.9 s.
- **Frame rate and responsiveness.** Exposure, colour and focus adjust once per frame (§3 finding 20), so a low frame rate makes photos wrong after a change, not late:
  - **Exposure and colour:** the tuning override (below) makes each frame's step bigger.
  - **Focus:** the override can't fully fix it, because the 4-frame direction check is in the code. Refocusing from far to near needs about 4 to 6 frames: about 1 s at 5 fps, 0.5 s at 10 fps, and 0.35 s at 14 fps. 📐
  - **M1 measures current at 5, 10 and 14 fps.** It ships the lowest-current rate where focus settles within 1 s of re-aiming. 🧪 The 1 s target is provisional. The old shutter-delay target (frame in hand ≤ 0.5 s) can't be met at full resolution on a Pi 4 at any rate (above), and 5 fps gave the lowest delay, so it no longer decides the rate.
- **Tuning override (always shipped).** `Picamera2.load_tuning_file("imx708.json")`, then edit the sections returned by `Picamera2.find_tuning_algo`. ✅ API, 📐 values, 🧪 check.
  - **How it's applied** (fixed 2026-10-16 after the Pi 4 bring-up): libcamera reads the tuning file when it first lists the camera, not when Picamera2 opens it. `open()` lists the camera first (to learn its model), so passing `Picamera2(tuning=dict)` afterwards was silently ignored. Picamera2 also deletes its temporary file but leaves `LIBCAMERA_RPI_TUNING_FILE` set. The next listing then fails (`Failed to register camera imx708: -22`) and the camera looks absent. So `open()`:
    1. clears the variable;
    2. lists the camera;
    3. writes the override to its own file and points the variable at it;
    4. lists the camera again (`Picamera2._cm.reset()`) so it registers with the override;
    5. opens it with `Picamera2(tuning=<that path>)`.

    `close()` clears the variable and deletes the file. ✅ On the Pi, libcamera logs `Using tuning file /tmp/inkstant-tuning-….json`.
  - The values below are for 5 fps (six times slower than 30), the slowest rate the camera runs (battery saver).
  - The same tuning is used at every rate. At 10 or 14 fps each step simply happens sooner.
  - The tuning is loaded when the camera opens, so it can't follow the frame rate at run time.
  - **Pi 5:** `load_tuning_file` reads the PiSP version of `imx708.json` ✅ [picamera2.py](https://github.com/raspberrypi/picamera2/blob/v0.3.38/picamera2/picamera2.py). Its exposure channels leave `speed` at the code default of 0.2, and focus has the same values as below. The override writes the same absolute values on both platforms, into every `rpi.agc` channel. ✅ [pisp imx708.json](https://github.com/raspberrypi/libcamera/blob/main/src/ipa/rpi/pisp/data/imx708.json)

  | Section | Setting | imx708.json | Ours at 5 fps | Why |
  |---|---|---|---|---|
  | `rpi.agc`, each channel | `speed` | 0.15 | 0.62 | 1 − 0.85⁶: six frames' worth per frame |
  | `rpi.awb` | `speed` | 0.05 (default) | 0.26 | 1 − 0.95⁶ |
  | `rpi.awb` | `frame_period` | 10 (default) | 2 | Recalculate about as often in seconds |
  | `rpi.af` | `skip_frames` | 5 | 1 | Focus may move one frame after a start |
  | `rpi.af` → `speeds` → `normal` | `step_frames`, `dropout_frames`, `retrigger_delay` | 5, 6, 10 | 2, 2, 2 | Scan steps and fallbacks in similar time |
  | `rpi.af` → `speeds` → `normal` | `max_slew` | 1.5 | 3.0 🧪 | More lens travel per frame; raise further only if the bench test shows no hunting |

  The exposure and colour steps are safe to raise. Each frame computes a fresh target from what that frame measured, so a bigger step can't overshoot the way a running total would. ✅ [agc_channel.cpp](https://github.com/raspberrypi/libcamera/blob/main/src/ipa/rpi/controller/rpi/agc_channel.cpp)
- **Exposure:** Short mode keeps exposures at 20 ms or less until gain reaches 6× ✅. If bench tests still show handheld blur, the same override also caps the last stage of the exposure mode at 33 ms in the `rpi.agc` section. ✅ API, 🧪 need.
- **Start-up:**
  1. Start at 14 fps (frame duration 70,000 µs).
  2. Wait for 10 frames, which covers the 6 frames libcamera flags as unsettled and white balance's 10-frame fast start (§3 finding 21). The first frame may take up to 10 s, each later one up to 2 s. On a Pi 4 at full resolution the first frame comes ~1.6 s after `start()`, then one every ~0.2 s, so the 10 take ~3.4 s, and the camera is ready 3.5 to 4.1 s after it opens. ✅ (2026-10-16. A 3 s total timeout failed every start.)
  3. Set `FrameDurationLimits` to the running rate (below), and report ready.
  4. Log the sensor mode and `CmaFree` from `/proc/meminfo`.
- **Power:** the camera is the biggest draw while on (about 250 mA typical ✅ [Pi power docs](https://github.com/raspberrypi/documentation/blob/develop/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc)). In M1, measure the current:
  - at 5, 10 and 14 fps;
  - with continuous autofocus against `AfMode: Auto`, which triggers a scan and then rests.

  Ship the lowest-current combination that meets the shutter-delay and focus targets above and gives sharp photos (§14.3). 🧪
- **Running rate:**
  - `CAMERA_FPS` in `hw/camera.py` is set from M1's result. `camera_fps` in `device.toml` overrides it for experiments.
  - With the battery saver on, the rate is 5 fps (§8.9).
  - Changing it while running is a `set_controls({"FrameDurationLimits": (d, d)})` with `d = 1_000_000 // fps`. It takes effect within a few frames, with no restart.
- **Sleep** (§8.8): `picam2.stop()`.
  - Stopping keeps the configuration and its buffers. Picamera2's stop path frees nothing, so waking needs no new camera memory. ✅ [picamera2.py](https://github.com/raspberrypi/picamera2/blob/v0.3.38/picamera2/picamera2.py)
  - 5 s after the stream stops, the sensor driver powers the sensor down (runtime PM autosuspend of 5000 ms). ✅ [imx708.c](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/drivers/media/i2c/imx708.c)
- **Waking:**
  1. `set_controls` 14 fps, then `picam2.start()`.
  2. libcamera treats this as a restart, not a first start. There are no start-up frames; exposure and colour carry on from their last values at the tuned speeds; focus skips `skip_frames` (1 with the override). ✅ [ipa_base.cpp](https://github.com/raspberrypi/libcamera/blob/main/src/ipa/rpi/common/ipa_base.cpp), [af.cpp](https://github.com/raspberrypi/libcamera/blob/main/src/ipa/rpi/controller/rpi/af.cpp)
  3. **Waking to shoot:** read each frame's metadata until `AfState` is `Focused` or `Failed` and at least 4 frames have arrived, or 1.5 s has passed. A frame wait that runs past the 1.5 s ends the wait rather than failing. Then `capture_request(flush=time.monotonic_ns())`, then set the running rate. Target: the frame in hand within 1 s of the press. 📐 🧪
     - Measured on a Pi 4 (2026-10-16): after a restart the first frame comes in 0.4 to 0.6 s, and the 4th at 1.0 to 1.5 s. Pressing while asleep saved the photo 2.1 s after the press, with the camera aimed at a blank ceiling, where focus never reports `Focused` and the full 1.5 s runs out. 🧪 Re-measure against a real scene.
  4. **Waking for a module insertion:** start, wait for 4 frames, then set the running rate. No capture.
- **Failure ladder:**
  1. `start()` fails (CMA allocation error) → retry with `buffer_count=1`.
  2. Fails again → fallback mode: a 1152 × 648 preview configuration, with `switch_mode_and_capture_request(full_config)` at each shutter press. Slower, but it works.
  3. Fails again → camera error (in the gallery, §8.14), no shooting; retry the full ladder every 60 s.
- **Pi 5:** the same code. Picamera2 chooses the PiSP pipeline itself, and camera buffers don't come from CMA there. ✅ Picamera2 manual

### 6.5 PiSugar client

Talks to `pisugar-server` over `/tmp/pisugar-server.sock`. The camera app's unit doesn't use `PrivateTmp=`, so it can see that path.

- **Two connections:**
  - **Events:** opened at start and never sent anything. Each line that is exactly `single`, `double` or `long` is handed to the app as a tap; any other line is ignored. Since 2026-10-17 this is the slower of two BTN readers: most taps reach the app through its own (§6.6).
  - **Commands:** one request at a time (an `asyncio.Lock`). Reply lines that are tap words are skipped, not acted on: pisugar-server sends every tap on every connection, so the events connection already has it. (Fixed 2026-10-17: a tap that arrived while a command waited for its reply was acted on twice.) The reply must start with `<command>:`. 2 s timeout; reconnect and retry once.
- **Reply format:** `<command>: <value>`. Setters answer `done`; anything else from a setter is an error message. Booleans are `true`/`false`. ✅ [cmds.rs](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/pisugar-server/src/cmds.rs)
- **I2C errors come back as the value:** `rtc_alarm_time: I/O error: Remote I/O error (os error 121)`. The PiSugar 3 gives one now and then: 1 read in 20 straight after an alarm write, on the Pi 4 (2026-10-16). Taken as a value, a boolean like `battery_power_plugged` read as false. Any reply starting with `I/O error` is retried after 0.1 s, up to 3 tries, then raised as an error. The setup checks' blocking reader does the same. ✅ 0 bad reads in 60 after the change.
- **The alarm time reads back with a placeholder date** (`1999-12-31T23:16:58.000-07:00`): only the time of day is stored. ✅
- **pisugar-server may still be starting** (its unit is `Type=simple`): connect with a backoff for up to 10 s. If it still isn't there, carry on degraded: no battery, alarm or watchdog, and a gallery error. BTN still works, through the app's own reader (§6.6).

| Command | Used for | When |
|---|---|---|
| `get model`, `get firmware_version` | Self-test; install requires firmware 1.2.4 or later | Install, self-test |
| `get battery`, `get battery_power_plugged` | Battery level and charging | Start, then every 60 s |
| `get rtc_time` | Clock check when there's no kernel RTC | Start |
| `rtc_rtc2pi` | Set the system clock from the PiSugar (fallback, and on the Pi 5) | Start |
| `rtc_pi2rtc` | Save the system clock to the PiSugar after the time is set from a phone | Time set |
| `rtc_web` | Internet time (SNTP) for the system clock and the PiSugar | Share mode at home |
| `rtc_alarm_set <ISO 8601> 127` | Arm the slideshow wake (any weekday) | Shutdown |
| `rtc_alarm_disable` | No wake | Shutdown |
| `get rtc_alarm_enabled`, `get rtc_alarm_time` | Read back the alarm | Shutdown |
| `get`/`set_soft_poweroff` (true) | PWR long press runs our command instead of cutting power | Start (check and fix) |
| `get`/`set_soft_poweroff_shell` (`/usr/local/bin/inkstant-ctl power-button`) | | Start (check and fix) |
| `get`/`set_auto_power_on` (false) | Plugging in only charges | Start (check and fix) |
| `get`/`set_anti_mistouch` (true) | | Start (check and fix) |
| `get`/`set_safe_shutdown_level` (3), `set_safe_shutdown_delay` (10) | Backup low-battery shutdown | Start (check and fix) |
| `get battery_input_protect_enabled` / `set_battery_input_protect` | Charge limit setting | Start, setting changed |

### 6.6 PiSugar watchdog and BTN taps (I2C)

`hw/pisugar_i2c.py`: smbus2, bus 1, address 0x57. ✅ [PiSugar I2C docs](https://docs.pisugar.com/docs/product-wiki/battery/pisugar3/pisugar-3-i2c)

**BTN taps** (decided 2026-10-17: BTN is the shutter). pisugar-server reads the tap register only every 500 ms (§3 finding 23), too slow for a shutter, so `TapReader` reads it too:

- **Reading:** a thread of its own reads 0x08 every 20 ms. Bits 0–1 hold the last tap: 1 single, 2 double, 3 long. ✅ [pisugar3.rs](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/pisugar-core/src/pisugar3.rs)
- **Clearing:** a tap counts only once it's cleared, by writing 0x08 back with bits 0–1 cleared (`value & 0xFC`). That's the same write pisugar-server makes, and it isn't write-protected (§3 finding 3). So a failed write can't report one tap twice.
- **Two readers:** whichever gets to a tap first clears it, so most taps arrive here and the rest through pisugar-server's socket (§6.5). Reading and clearing aren't one step, so now and then both get the same tap. The app (`on_tap`) drops a tap from one reader when the other reported the same tap within the last 0.5 s, and logs when a tap reached pisugar-server first. ✅ Bench Pi 4 (2026-10-17): all four test taps came through the camera's reader, none through pisugar-server first, and none twice.
- **Errors:** the PiSugar 3 fails a read now and then (§6.5), so the reader drops to once a second only after 10 failures in a row, and logs when it's reading again.
- **The tap delay:** the PiSugar firmware reports a single tap only once its double-tap window has passed, and doesn't document the window. A hold is reported when BTN is let go, not while it's held (noted with the 2026-10-17 code). ✅ Bench 2026-10-17: the user found the tap delay acceptable. 🧪 Measure the window and the hold time; the README tells users to hold for 2 s.
- **What a tap does** is §8.2: a tap, or a double tap while share mode is off, is a shutter press.

**Watchdog:**

- **Enable** (once per boot, as soon as the PiSugar client is up):
  1. `0x0B ← 0x29` (unlock)
  2. `0x07 ← 30` (30 × 2 s = 60 s)
  3. `0x06 ← read(0x06) | 0x80`
  4. `0x0B ← 0x00` (lock)
  5. Read back: `0x07 == 30` and `0x06 & 0x80`. If not, pisugar-server relocked in between (§3, finding 3): wait 50 ms and repeat, up to 5 tries, then report a PiSugar error.
- **Feed** (every 10 s, from the event loop): `0x06 ← read(0x06) | 0x20`. Not write-protected. ✅
- **Disable** (the app's last step before powering off, §8.13 step 7, with the same read-back and retry): unlock, `0x06 ← read(0x06) & 0x7F`, lock. The shutdown hook turns it off again later, with no race (§13.4).
- **Why the app disables it too:** after the app exits, nothing feeds the watchdog. If the rest of the shutdown took longer than 60 s, the PiSugar would power-cycle the Pi into a reboot instead of switching it off.
- **Boot watchdog** (0x06 bits 4 and 3): not used in v1. It power-cycles after about 90 s without a feed, which a slow `fsck` could hit. 📐
- **PiSugar facts:**
  - On expiry the PiSugar briefly cuts power, then restores it, so the Pi restarts.
  - The watchdog is off by default, and turns off again whenever the PiSugar restarts.
  - Feeding works through write protection; turning it on needs the unlock.
  - With soft shutdown on, PiSugar documents no hold-to-force-off, which is why the watchdog is the only way out of a freeze. ✅ [PiSugar I2C docs](https://docs.pisugar.com/docs/product-wiki/battery/pisugar3/pisugar-3-i2c)

### 6.7 Clock

- **Pi 3 and 4:** `dtoverlay=i2c-rtc,ds3231` makes the PiSugar's RTC `/dev/rtc0`, and the kernel sets the system time when the driver loads, before the app starts. ✅ (§3, finding 7)
- **Pi 5:** its own RTC is `rtc0`, and it loses time whenever the PiSugar cuts power. So no overlay there; the app uses `rtc_rtc2pi`.
- **At start**, the app (`CameraApp.check_clock`):
  1. Reads the RTC: `/sys/class/rtc/rtc0/since_epoch` if `/sys/class/rtc/rtc0/name` names the DS3231 driver, otherwise `get rtc_time`.
  2. **Valid** if the RTC reads at or after `BUILD_EPOCH` (the release's build time). Otherwise the clock counts as unset (§1.5).
  3. If valid and the system clock is more than 2 s away from it, sets the system clock (`time.clock_settime(CLOCK_REALTIME, …)`, or `rtc_rtc2pi`).
  4. Records `boot_utc = rtc_now − uptime` (`/proc/uptime`) for the slideshow-wake check (§8.11).
- **Validity is judged from the RTC, never from the system clock.** systemd can move the system clock forward to its build date or to the last saved time, which would hide a lost clock.
- **Setting the time:**
  - **Share mode at home:** `rtc_web` (fire and forget), then read `get rtc_time` again after 5 s to confirm.
  - **From a phone** (settings page): the gallery sends the phone's time and IANA time zone. The app sets the zone with `timedatectl set-timezone` if it exists in `/usr/share/zoneinfo`, sets the clock with `clock_settime`, then runs `rtc_pi2rtc`.
- **`systemd-timesyncd` is disabled.** Offline, it backs off to long retry intervals and would rarely run during a short share session; `rtc_web` covers internet time. 📐
- 🧪 Bench: `/dev/rtc0` appears, the time is right at boot, and the slideshow alarm still fires with the overlay loaded (the kernel's DS3231 driver touches alarm-related bits when it starts).
- **When the clock is lost (checked 2026-10-15):**
  - The PiSugar's clock is emulated by its microcontroller and runs on the main battery, with no separate backup cell. PiSugar says it keeps time for more than a year while off, and stops if the battery is removed. ✅ [PiSugar 3 docs](https://docs.pisugar.com/docs/product-wiki/battery/pisugar3/pisugar-3-series)
  - A normal low-battery shutdown leaves about 5%, roughly 250 mAh. "More than one year" from a full 5000 mAh means the off-state draw averages at most about 0.57 mA. So that 5% keeps the clock running for at least about 2.5 weeks, probably much longer. 📐
  - So in practice it's lost only by a camera left flat for weeks, or a battery swap. 🧪 Measure the PiSugar's hibernate current, and what its clock reads after the battery is reconnected, to confirm the `BUILD_EPOCH` check catches it.

### 6.8 Network (NetworkManager)

`hw/network.py` runs `nmcli` with `subprocess.run(timeout=…)` and parses terse output (`-t`, with `\:` escapes handled).

**Profiles** (root-only keyfiles in `/etc/NetworkManager/system-connections/`):

- **Generated with `nmcli --offline connection add …`**, which prints a keyfile (it handles SSID and password escaping), written with `atomic_write` (mode 0600), then `nmcli connection load <file>`. ✅ (§3, finding 15)
- **`inkstant-hotspot`:**
  ```text
  type wifi, ifname wlan0, connection.autoconnect no, ssid <hotspot.ssid>,
  802-11-wireless.mode ap, 802-11-wireless.band bg,
  ipv4.method shared, ipv4.addresses 10.42.0.1/24, ipv6.method disabled,
  wifi-sec.key-mgmt wpa-psk, wifi-sec.proto rsn, wifi-sec.pairwise ccmp,
  wifi-sec.group ccmp, wifi-sec.pmf disable, wifi-sec.psk <hotspot.password>
  ```
  WPA2 only; PMF off (§3, still open); 2.4 GHz for the widest phone support. 📐 🧪
- **`inkstant-home-1` … `inkstant-home-5`:**
  ```text
  type wifi, ifname wlan0, connection.autoconnect no (yes for home-1 in dev mode),
  ssid <ssid>, wifi-sec.key-mgmt <wpa-psk|sae>, wifi-sec.psk <psk>,
  ipv4.method auto, ipv6.method auto
  ```
  A network with no password has no `wifi-sec` settings.
- **WPA2 or WPA3** (§3, findings 26 and 27):
  - **Saved as `wpa-psk`**, from the gallery and by the install script. It joins WPA2 and WPA2/WPA3 networks on any chip, using WPA3 where the chip has SAE. ✅ The install script keeps `sae` when Imager's network had it.
  - **When sharing starts, the scan decides.** Its `SECURITY` words, merged over every access point with that name: any `WPA1` or `WPA2` means `wpa-psk`; `WPA3` alone means `sae`. When the saved profile differs, `nmcli connection modify` switches it before joining. The password stays in the keyfile and is never passed again. Open networks, and profiles saved with no password, are left alone. ✅ tests 🧪 WPA3 only
  - **Why not `sae` for transition networks too:** `sae` never joins WPA2, so it fails on a chip without SAE, or if the router goes back to WPA2.

**Commands:**

| Step | Command | Timeout |
|---|---|---|
| Radio on/off | `nmcli radio wifi on` / `off` (NetworkManager remembers it across reboots ✅) | 5 s |
| Wait for wlan0 | Poll `nmcli -t -f DEVICE,STATE device` until wlan0 is `disconnected` | 10 s |
| Scan | `nmcli -t -f SSID,SIGNAL,SECURITY device wifi list ifname wlan0 --rescan yes` → `Home:70:WPA2 WPA3` | 12 s |
| Match security | `nmcli connection modify id inkstant-home-N wifi-sec.key-mgmt sae` (or `wpa-psk`), only when it differs | 5 s |
| Join home | `nmcli --wait 20 connection up id inkstant-home-N ifname wlan0` | 25 s |
| Address | `nmcli -g IP4.ADDRESS device show wlan0` → `192.168.1.23/24` | 5 s |
| Hotspot | `nmcli --wait 15 connection up id inkstant-hotspot` | 20 s |
| Down | `nmcli connection down id <active profile>` | 10 s |

## 7. Image pipeline

All of this runs in the capture worker; it's the only code that imports numpy and Pillow.

### 7.1 Screen image

From the lores frame (1152 × 648 YUV420; same view as the full frame):

1. **Split the planes.** `request.make_array("lores")` is a copy of shape (972, stride) ✅. Y is rows 0 to 647; U and V are the next two quarter-size planes, 576 × 324 each. Use the same reshape Picamera2 uses in `request.save`, so any row padding is handled.
2. **Crop to 3:2 in YUV:** Y columns 90 to 1061 (972 wide), U and V columns 45 to 530. That drops 90 px from each side (§1.9): the same view as 3888 × 2592 of the full frame.
3. **To RGB:** Y as an `L` image; U and V as `L` images scaled up to 972 × 648 (bilinear); `Image.merge("YCbCr", …).convert("RGB")`. Pillow's YCbCr is full-range BT.601, the same as the camera's `Sycc` stills colour space. ✅
4. **Resize** to 600 × 400 (`Image.Resampling.LANCZOS`).
5. **Tone for e-paper:** a mild saturation and contrast lift (`ImageEnhance`, 1.2 and 1.1), tuned together with the palette 📐. Then map 0 to 255 onto the palette's own black to white, channel by channel: the panel's white is a light grey, so without this everything brighter dithers to solid white and the highlights blow out (added 2026-10-17).
6. **To native orientation:** `transpose(Image.Transpose.ROTATE_90)`, giving 400 × 600, as Waveshare's `getbuffer` does. The display rotation setting (0° or 180°) is applied when sending (§7.2), so the files never depend on it.
7. **Dither:** `img.quantize(palette=PANEL, dither=Image.Dither.FLOYDSTEINBERG)`. ✅
   - `PANEL` is a `P` image holding the panel's six real, muted colours at indices 0 to 5, in order black, white, yellow, red, blue, green. Dithering against them, instead of pure RGB, matches how the panel really looks. Waveshare recommends Floyd–Steinberg for its colour panels, and its driver uses this same Pillow call with pure colours. ✅ [driver source](https://github.com/waveshareteam/e-Paper/blob/master/E-paper_Separate_Program/3.6inch_e-Paper_E/RaspberryPi_JetsonNano/python/lib/waveshare_epd/epd3in6e.py), [Pillow docs](https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.quantize)
   - **Values (palette version 4, 2026-10-17):** measured on our panel, the median of each swatch in a phone photo of the palette tuner's chart: black 24,21,38 · white 212,223,225 · yellow 229,214,67 · red 114,33,29 · blue 48,92,170 · green 85,112,103. Black, red, yellow and blue fall within published Spectra 6 measurements; our green is lighter. Before version 3 they were Pimoroni's measured colours for its Spectra 6 panels (black 0,0,0 · white 161,164,165 · yellow 208,190,71 · red 156,72,75 · blue 61,59,94 · green 58,91,70 ✅ [inky_e640.py](https://github.com/pimoroni/inky/blob/main/inky/inky_e640.py)).
   - The other 250 palette entries repeat the palette's own black, as Waveshare pads its palette. A plain 0,0,0 would sit nearer to dark pixels than the measured black, and Pillow would dither towards a colour the panel doesn't have.
8. **Map and pack (numpy):**
   - `codes = LUT[indices]`, where `LUT` is 256 entries: `[0, 1, 2, 3, 5, 6]`, then black (0) for the rest. Any padding index maps to black, so it can never send the panel an undefined code. A unit test asserts the indices stay at 0 to 5.
   - Then `(codes[0::2] << 4) | codes[1::2]` over the flattened rows → 120,000 bytes. This replaces the driver's per-pixel Python loop over 240,000 pixels.

### 7.2 `.epd` file format

📐 16-byte header + 120,000-byte frame = 120,016 bytes.

| Offset | Size | Field |
|---|---|---|
| 0 | 4 | Magic `IKEP` |
| 4 | 1 | Format version, 1 |
| 5 | 1 | Flags, 0 |
| 6 | 2 | Palette version (little-endian), so files from an older palette can be found and rebuilt |
| 8 | 2 | Width, 400 |
| 10 | 2 | Height, 600 |
| 12 | 4 | Reserved, 0 |
| 16 | 120,000 | Frame: rows of 400 px, 600 rows, two pixels per byte, first pixel in the high nibble; codes black 0, white 1, yellow 2, red 3, blue 5, green 6 ✅ |

- **Rotating 180° when sending:** reverse the frame bytes, then swap the two nibbles of every byte (`bytes.translate` with a 256-entry table). Standard library only, about a millisecond.
- **Readers** reject a wrong magic or size. A file from an older palette version still displays, and gets queued for a rebuild.
- `imaging/epdfile.py` uses the standard library only; it's the only image code the slideshow wake loads.

### 7.3 Thumbnail

The full lores frame (16:9), converted as in step 3 but without the crop, resized to 480 × 270 and saved as JPEG at quality 80 with Pillow.

**The print** (`thumbs/<id>.png`, added 2026-10-17): the screen image's codes drawn as a 600 × 400 PNG, in the measured colours scaled so the panel's white shows as paper white (`palette.DISPLAY_RGB`). It's made from the `.epd`, so it's rebuilt with it.

### 7.4 Full photo and EXIF

- `request.save("main", buf, format="jpeg", exif_data=exif)` into an `io.BytesIO`. Picamera2 encodes YUV420 straight to JPEG with simplejpeg, at `options["quality"] = 90`. ✅ The bytes are then written with `atomic_write`.
- **Our EXIF**, passed as whole IFDs, so it replaces Picamera2's:
  - `0th`: Make `Inkstant`, Model `Inkstant Camera`, Software `inkstant <version>`, DateTime.
  - `Exif`: DateTimeOriginal (the shutter press time), ExposureTime and ISOSpeedRatings (from the request's metadata, as Picamera2 computes them).
  - With the clock unset, the date fields are left out.
- No Orientation tag: the camera flip is applied by the sensor (`Transform`), so the pixels are already upright.

### 7.5 Share screen

Rendered at 600 × 400 in pure black and white, so no dithering is needed; then rotated and packed as in steps 6 and 8.

- **Away (hotspot):**
  - Two QR codes side by side, about 200 px each (about 25 mm on the panel).
  - Left: "1. Join Wi-Fi", encoding `WIFI:T:WPA;S:<ssid>;P:<password>;H:false;;`, with `\ ; , : "` escaped by a backslash. ✅ [ZXing Wi-Fi format](https://github.com/zxing/zxing/wiki/Barcode-Contents#wi-fi-network-config-android-ios-11)
  - Right: "2. Open photos", encoding `http://10.42.0.1/`.
  - Below: the network name, the password and the address in text (DejaVu Sans, from `fonts-dejavu-core`).
  - Hints: "If your phone asks, choose to stay connected." (§9.4); "Hold the shutter button to stop sharing and take photos."; and, when a double tap chose the camera's Wi-Fi and a home network is saved, "Double-tap the shutter button to go back to home Wi-Fi."
- **At home:** one large QR code with `http://<IP>/`, the address in text, and "On <home network name>". Then: "Not on this Wi-Fi? Double-tap the shutter button for the camera's own." and "Hold the shutter button to stop sharing and take photos." (§2.1, 2026-10-17: the shutter is off while sharing, and the share screen calls BTN "the shutter button")
- **QR settings:** python3-qrcode's matrix (`QRCode(error_correction=M, border=4).get_matrix()`), drawn as whole pixels per module at the largest scale that fits. 🧪 Scan from 15 to 30 cm with an iPhone and an Android phone.

### 7.6 Timing targets on the 3A+

📐 🧪 Measured in M2 and M8 (§20).

| Step | Target |
|---|---|
| Lores → `.epd` (steps 1 to 8) | ≤ 0.6 s |
| Thumbnail | ≤ 0.3 s |
| Full JPEG encode (12 MP) | ≤ 2 s |
| Shutter press → screen update starts | ≤ 3 s |

The screen image and thumbnail run at the same time as the JPEG encode, on a second thread; Pillow and simplejpeg do their heavy work in C. 🧪

### 7.7 Palette tuning and rebuilding

- `tools/palette_tuner.py` shows calibration screens on the real panel: the six pure colours, ramps, and a few reference photos dithered with candidate palettes. The winner goes into `imaging/palette.py`, and `PALETTE_VERSION` goes up by one.
- **Rebuilding** (`inkstant-ctl regen-epd`, and automatically after a palette change, in the background while idle): decode each JPEG with `Image.draft("RGB", (1152, 648))`, which is libjpeg's exact quarter-size decode of 4608 × 2592, then run steps 2 to 8, and remake the print from the new screen image (§7.3).

## 8. Camera app behaviour

This section carries out the product rules in §1. `machine.py` holds the rules as pure functions (what the light shows, whether a tap is accepted, the timers, the battery levels and the saver), and `CameraApp` in `app.py` holds the state and makes the transitions, asking those functions at each step. That makes every rule unit-testable with no hardware.

### 8.1 States

**Lifecycle** (one at a time):

| State | Meaning |
|---|---|
| `STARTING` | The app is initialising; the kernel's slow blink is still showing |
| `WAKE` | Slideshow wake: no camera, no Wi-Fi |
| `ACTIVE` | Normal use |
| `SHUTTING_DOWN` | Shutdown sequence running (§8.13) |

**Side state** (alongside the lifecycle):

| Name | Values |
|---|---|
| `module_present` | bool |
| `camera` | `off`, `starting`, `ready`, `fallback` (§6.4 failure ladder), `asleep`, `waking`, `failed` |
| `capture_busy` | bool: from an accepted shutter press until the photo is committed or has failed |
| `display` | `idle`, `refreshing(job)`, `tail(job)` |
| `share` | `off`, `starting`, `on(home or hotspot, url)`, `stopping` |
| `battery` | level (median, §8.10), `plugged`, `low` |
| `saver` | `manual` (setting), `auto_active` (state, §8.9), and `on` = either |
| `storage` | `ok`, `low`, `full` |
| `clock_valid` | bool |
| `errors` | code → message, since (§8.14) |
| `update_running` | bool: a software update is in progress (§16.1) |
| `power_after_update` | bool: PWR was pressed during the update |
| `power_button_pending` | bool: a PWR press during `STARTING` |

**Derived rules:**

- `refresh_locked` = `display` is `refreshing`. The tail after "image shown" doesn't lock.
- **Shutter accepted** when all of these hold:
  - the lifecycle is `ACTIVE`;
  - the camera is `ready`, `fallback`, `asleep` (the press wakes it to shoot) or `waking` for a module insertion (the press waits for the wake), §8.8;
  - nothing is `capture_busy` or `refresh_locked`;
  - storage isn't `full`, and no update is running;
  - `share` is `off` (decided 2026-10-17: no shooting while sharing starts, is on, or stops).
- **A shutter press** is a BTN tap, or a double tap while `share` is `off` (§6.6).
- **A BTN hold, or a double tap while sharing, is accepted** when `ACTIVE`, not `refresh_locked` and no update is running; a hold also at any time during `WAKE`.

### 8.2 Events and responses

| Event | State | Response |
|---|---|---|
| **Shutter press**: a BTN tap, or a double tap while share mode is off (timestamped as it's handled, §8.4) | `STARTING`, `SHUTTING_DOWN` | Ignore; taps aren't queued (§1.2) |
| | `WAKE` | Switch to `ACTIVE` (§8.11); no photo |
| | `ACTIVE`, accepted, camera awake | Wink, start the capture (§8.4), count as use |
| | `ACTIVE`, accepted, camera `asleep` | Wink, wake to shoot (§8.8), count as use |
| | `ACTIVE`, refused | Count as use only; no wink (for example storage full, while developing, or while sharing) |
| **BTN hold** (`long`) | `STARTING`, `SHUTTING_DOWN` | Ignore |
| | `WAKE` | Switch to `ACTIVE` and start share mode |
| | `ACTIVE`, accepted | Share mode off → start it; on → stop it (§9). Starting or stopping → ignore. Count as use |
| **BTN double tap while sharing** | `ACTIVE`, accepted, share mode on | Switch between home Wi-Fi and the camera's own (§9.3); count as use. Starting or stopping → ignore |
| **PWR** (`power.button` over the control socket) | `STARTING` | Set `power_button_pending`; once the mode is known, treat it as below |
| | `WAKE` | If the battery is at or under the backup level + 1% and not plugged in → shut down (`low_battery`). Otherwise switch to `ACTIVE` ("turn on") |
| | `ACTIVE`, update running | Set `power_after_update`; the light keeps the wait pattern (§16.1) |
| | `ACTIVE` | Shut down (`power_button`), letting a running screen update finish first |
| | `SHUTTING_DOWN` | Ignore (pisugar-server may repeat it every second) |
| **Module removed** | any | Abort the running display job, drop the pending one, `display_content = unknown`. Count as use, for both timers |
| **Module inserted** (stable 300 ms) | `STARTING` | Remember; act on it once started |
| | `WAKE` | If the wake's screen update hasn't started, start it now (§8.11) |
| | `ACTIVE` | Camera `asleep` → wake it (no capture, §8.8). Then: share on → share screen; else an undeveloped photo → develop it; else leave the module alone (§1.4). Count as use, for both timers |
| | `SHUTTING_DOWN` | Ignore |
| **Photo committed** | `ACTIVE` | `undeveloped = id`, replacing any older one. If a module is on and share mode is off → `DEVELOP` it; otherwise it waits (§1.4) |
| **Capture failed** | `ACTIVE` | Camera error; restart the camera (§6.4) |
| **Image shown** | any | Unlock (display → `tail`) |
| **Display job finished** | any | Apply its effects (§8.3), start the pending job. In `WAKE`, schedule and shut down (§8.11) |
| **Display job aborted** | any | Nothing becomes developed or current |
| **Display timeout** | any | Module detected → display error. Not detected → treat as removed |
| **Camera ready** | `ACTIVE` | The shutter is live, and the light goes solid unless something else needs the wait blink (§8.6) |
| **Camera asleep / woken** | `ACTIVE` | Update the light (§8.6) |
| **Camera failed** | `ACTIVE` | Camera error; retry ladder (§6.4) |
| **Battery reading** | any | Update; battery saver (§8.9); maybe shut down (§8.10) |
| **Settings changed** | `ACTIVE` | Re-apply timers, the battery saver and the frame rate (§8.7, §8.9) |
| **Sleep timer** | `ACTIVE` | Camera `ready` or `fallback`, not `capture_busy`, idle long enough → sleep (§8.8) |
| **Auto-off timer** | `ACTIVE` | Shut down (`auto_off`) when idle long enough, with share mode off, no display job running, and no update running |
| **Share timeout** | `ACTIVE` | Share mode off (§9.3) |
| **Wake timeout** (120 s) | `WAKE` | Shut down (`wake_timeout`) |
| **Update started / failed** | `ACTIVE` | Set or clear `update_running`; a failure also clears `power_after_update` and, if it was set, shuts down (`power_button`) |
| **Control socket request** | per §12 | Per §12 |
| **SIGTERM** (systemd stopping the app) | any | Run the shutdown sequence (reason `system`) if it isn't running, then exit |

### 8.3 Display jobs

One job runs at a time, with one pending slot; a newer job replaces the pending one. A job only starts with a module in and the lifecycle not `SHUTTING_DOWN` (a job already running when shutdown starts may finish).

| Job | Content | When it completes |
|---|---|---|
| `DEVELOP(id)` | `epd/<id>.epd` | `current = id`; if `undeveloped == id`, it becomes null; `display_content = photo` |
| `SHOW(id, why)` (why: `show_on_camera`, `restore`, `slideshow`) | `epd/<id>.epd` | `current = id`; if `undeveloped == id`, it becomes null ("Show on camera" counts as developed); `display_content = photo` |
| `SHARE_SCREEN(info)` | Rendered (§7.5) | `display_content = share` |
| `BLANK` | All white | `current = null`; `display_content = blank` |

- **When share mode ends:**
  - An undeveloped photo → `DEVELOP` it.
  - Otherwise, if the module shows the share screen → `SHOW(current, restore)`, or `BLANK` when there's no current photo.
- **"Show on camera" during share mode** replaces the share screen with that photo, the point being to see it on the module. When share mode ends, the photo is already showing, so nothing is restored. 📐
- **"Show on camera" while the camera is asleep** works without waking it: the display doesn't need the camera.
- **Missing or unreadable `.epd`** (deleted, or corrupt) → skip the job, log it, and queue a rebuild.

### 8.4 Taking a photo

1. **Tap:** the BTN reader (or pisugar-server's socket) reports a tap (§6.6), and the event loop records `press_ns = time.monotonic_ns()` as it handles it. That's after the PiSugar's double-tap window, not when the finger went down (§6.4).
2. **Accept:** the state machine checks the rules (§8.1).
   - Accepted → `capture_busy = true`, then the wink, then the wait pattern. The tap counts as use.
   - If the camera is asleep, wake it first (§8.8). The capture then uses the settled frame instead of `press_ns`.
3. **Capture:** `request = capture_request(flush=press_ns)` in the capture worker. 5 s timeout → capture error.
4. **Copy and encode, in parallel:**
   - Copy the lores array, and start screen image + thumbnail (§7.1, §7.3) on a second thread.
   - Encode the full JPEG with our EXIF (§7.4), then `request.release()`.
5. **Commit:**
   - The ID is taken from `next_seq` (or one past the highest number on the card, if that's higher) and the press time.
   - Write `.epd` → thumbnail → print → JPEG with `atomic_write` (§5.2).
   - Then the incremented `next_seq` is saved in state.
6. **Report:** the photo-committed event; the gallery sees it straight away.
7. **Develop:** the state machine makes it the undeveloped photo and queues `DEVELOP` (§8.2), then `capture_busy = false`.
8. **Failures:**
   - An I/O error while saving → storage error. Temp files are removed; no photo, no sequence number used up.
   - `ENOSPC` → storage full (§8.15).

**Space check:** free space is checked before accepting a press (storage full refuses it), and again after each commit and delete, to update the banners.

### 8.5 Inserting and removing modules (putting a print on and taking it off)

The table in §1.4 is carried out by the module events in §8.2 and the jobs in §8.3. Details left to the software:

- **Pulled during a screen update:** the driver aborts within one BUSY wait. RST, DC and PWR go low and the SPI device closes. The photo stays undeveloped.
- **During shutdown:** a removal aborts the screen update and shutdown carries on; an insertion is ignored.
- **Reinserted within the 300 ms settle time:** counts as one removal plus one insertion.

### 8.6 Status light logic

`machine.led_steady(view)`, re-evaluated after every event, picks the steady pattern (§1.3, §6.2). The first rule that matches wins (four looks since 2026-10-17):

1. `STARTING` → **starting** (the kernel's timer blink, left alone).
2. `WAKE` → **off** (§2.1, decided 2026-10-15). 📐
3. `SHUTTING_DOWN` → **wait** while step 2 or 3 of §8.13 is running a screen update (including the white screen); otherwise **off**. Off at the end means it's safe to take the module off.
4. Any of these → **wait**:
   - `display` refreshing (not the tail), a `DEVELOP` job pending, or `capture_busy`;
   - camera `starting` or `waking`;
   - `update_running`;
   - `share` anything but `off` (starting, on or stopping).
5. Otherwise → **ready** (solid). That includes a camera that's asleep, since a tap still shoots. It also includes a camera that has failed or is retrying, and storage full: a tap then gets no wink, and the gallery says why.

**The wink** plays on every accepted shutter press, before the next steady pattern (§6.2). There are no cues (removed 2026-10-17). Errors, low battery, the clock and the PiSugar show only in the gallery.

### 8.7 Timers and activity

| Timer | Period | What it does |
|---|---|---|
| Watchdog feed | 10 s | PiSugar watchdog feed + systemd `WATCHDOG=1`, from the loop |
| Battery poll | 60 s, plus at start and when the plug state changes | `get battery`, `get battery_power_plugged`; then §8.9 and §8.10 |
| Sleep | Checked every second | No camera use for the effective sleep time → sleep (§8.8) |
| Auto-off | Checked every second | No use for the effective turn-off time → shut down (§8.2) |
| Share timeout | Checked every second | No gallery use for the effective share timeout while on battery → share mode off |
| Wake timeout | 120 s after `WAKE` starts | Shut down |
| Camera retry | 60 s | After a camera failure |
| Shutdown wait | 30 s | Longest wait for a running screen update before aborting it |
| Storage check | After each commit and delete, and every 10 min | Banners, and whether the shutter is refused |
| BTN reader | 20 ms, in its own thread (once a second after 10 failed reads in a row) | Reads the PiSugar's tap register (§6.6) |

**Effective times** (the battery saver applies its shorter time, or the user's if that's shorter; §8.9):

| Timer | Saver off | Saver on |
|---|---|---|
| Sleep | `sleep_min` (none if "Never") | `min(sleep_min, 1)`, or 1 if "Never" |
| Turn off | `auto_off_min` | `min(auto_off_min, 5)` |
| Share timeout | `share_timeout_min` | `min(share_timeout_min, 5)` |

If the effective sleep time is at or above the effective turn-off time, the camera never sleeps; it just turns off.

**Use** is tracked with two timestamps (monotonic):

- **`last_use`** (turn-off) is updated by:
  - any BTN tap, double tap or hold, whether it takes a photo or not (a tap that wakes the camera from sleep included);
  - inserting or removing a module;
  - any control-socket command, from the gallery or `inkstant-ctl`, except the ones that only read: `status`, `share.activity`, `settings.get`, `wifi.list`, `selftest.get` and `update.report`;
  - sharing stopping, and a software update failing.
- **`last_camera_use`** (sleep) is updated by a shutter press, inserting or removing a module, and the camera waking up. The gallery doesn't keep the image sensor running.
- **Paused:**
  - Turn-off is paused while share mode is on or starting, and while an update runs.
  - In developer mode it's also paused while plugged in.
  - Sleep is never paused, but it waits for `capture_busy` to clear.
  - Both restart from the moment they're resumed.

### 8.8 Sleep

Decided 2026-10-15 (§1.5). 📐 🧪

- **Falling asleep** (sleep timer, camera `ready` or `fallback`, not `capture_busy`, lifecycle `ACTIVE`):
  1. The capture worker runs `picam2.stop()` (§6.4). The buffers stay allocated.
  2. `camera = asleep`; the light stays solid unless something needs the wait blink (§8.6).
  3. Everything else carries on: share mode, the gallery, display jobs (including "Show on camera"), battery, the watchdog and the turn-off timer.
- **Waking to shoot** (an accepted shutter press while asleep):
  1. `capture_busy = true`, `camera = waking`, the wink, then the wait pattern. `press_ns` is kept for the photo's ID and EXIF time.
  2. The capture worker restarts the camera at 14 fps, waits for focus to settle (at least 4 frames, `AfState` focused or failed, at most 1.5 s), then captures the next frame (§6.4).
  3. It sets the running frame rate, and `camera = ready`.
  4. From there it's §8.4 steps 4 to 8. The photo is taken about 1 s after the press; that's the cost of sleep (§14.4).
- **Waking without shooting:** a module inserted while asleep restarts the camera without capturing. A shutter press during that wake is accepted and waits for it to finish.
- **Failures:** if the restart fails, it's a camera error and the §6.4 failure ladder runs. The press is dropped, with no photo and no sequence number used.
- **Not in `WAKE`:** a slideshow wake never starts the camera, so there's nothing to sleep.

### 8.9 Battery saver

Decided 2026-10-15 (§1.5). 📐

- **On** when `settings.battery_saver.enabled` (by hand) or `state.saver_auto_active` (automatic).
- **Automatic level:** 20%, or 5 points above `low_battery_shutdown_pct` if that's higher.
- **Turning on by itself:** on a battery reading (§8.10), when all of these hold → `saver_auto_active = true`, saved in `state.json`:
  - `battery_saver.auto` is on;
  - the camera isn't plugged in;
  - `saver_auto_dismissed` is false;
  - the median is at or under the automatic level.
- **Turning off by itself:**
  - `saver_auto_active` is cleared as soon as `plugged` is true, or when `battery_saver.auto` is turned off.
  - The gallery shows an automatic saver as "On because the battery is low", with a **Turn off** button. That clears `saver_auto_active` and sets `saver_auto_dismissed`, which keeps the automatic level from turning it on again until the camera is next plugged in.
  - Plugging in clears `saver_auto_dismissed` too. Since only plugging in clears the automatic saver, a reading that bounces around 20% can't toggle it.
- **What it changes, re-applied at once whenever it turns on or off:**
  - **Timers:** §8.7's effective times. A timer whose new time has already passed acts at its next 10 s check.
  - **Frame rate:** 5 fps while the camera is awake, or after its next start. When the saver turns off, back to `CAMERA_FPS` (§6.4).
  - **Share timeout:** §8.7.
- **Reported** in `/api/status` as `battery_saver: {"on": bool, "why": "manual" | "auto" | null}`.

### 8.10 Battery

- **Smoothing:** the median of the last 3 readings. The voltage sags during screen updates and Wi-Fi, so single readings jump.
- **Levels:**
  - **Warning:** 15%, or 5 points above `low_battery_shutdown_pct` if that's higher.
  - **Battery saver:** §8.9.
  - **Shutdown:** `low_battery_shutdown_pct` (5% by default).
  - **The PiSugar's backup:** 3%.
- **Battery low:** median at or under the warning level, and not plugged in → `battery.low` and the banner (the light shows nothing for it, §8.6).
- **Low-battery shutdown:** median at or under `low_battery_shutdown_pct`, not plugged in, on two readings in a row → shut down with reason `low_battery`, with the slideshow alarm off (§1.5).
- **In `WAKE`:** the battery is read first. At or under the shutdown level, skip the screen update, disable the alarm, and shut down.
- **Backup:** pisugar-server's `auto_shutdown_level` stays at 3% with a 10 s delay. It runs our power-button command (§3, finding 2).

### 8.11 Slideshow wake

**Telling a timed wake from a normal start** (the PiSugar 3 has no alarm flag ✅):

- `boot_utc = rtc_now − uptime` (§6.7). It's a timed wake when `next_wake_utc` is set and `next_wake_utc − 10 s ≤ boot_utc ≤ next_wake_utc + 60 s`. 📐
- `uptime` starts when the kernel starts, a few seconds after the PiSugar switches on. So a timed wake lands a few seconds after `next_wake_utc`; a manual power-on would have to happen within that window to be mistaken for one.
- After deciding, `next_wake_utc` is cleared and saved, so a crash and restart doesn't decide the same way twice.
- With the clock unset there are no wakes (§8.12), so this check never runs then.

**The wake path** (standard library + `epdfile` + display driver; no Picamera2, numpy or Pillow):

1. `WAKE`; light off (it has blinked since early kernel boot, §6.2); watchdog on; Wi-Fi stays off (NetworkManager remembers the radio as off).
2. Read the battery (§8.10).
3. Choose the photo:
   - The undeveloped one first (§1.4).
   - Otherwise, the next from the slideshow order (§5.5).
   - No photo → skip to step 5.
4. With a module in: `SHOW(id, slideshow)`, or `DEVELOP` for the undeveloped photo. With no module: skip, unless one is inserted before the wake timeout.
5. As soon as the image is shown, save state and arm the next wake (§8.12) while the panel finishes powering down (the 2.4 s tail, §6.3). Then shut down (reason `slideshow`). This saves 1 to 2 s per wake. 📐

**Switching to normal** (a BTN tap, double tap or hold, or PWR during `WAKE`):

- Lifecycle → `ACTIVE`, start the camera (the heavy imports happen now), and a hold also starts share mode. A tap takes no photo.
- A running screen update finishes normally.
- The turn-off and sleep timers start from that press.

### 8.12 Scheduling the next wake

`slideshow.next_wake(now, settings, battery_level, plugged, clock_valid, has_photos) → Unix time | None`, pure and unit-tested:

- **None** if the slideshow is off, the clock is unset, there are no photos, or the battery is at or under the shutdown level + 2% and not plugged in. In those cases, call `rtc_alarm_disable`, and the screen is turned white at power-off (§8.13).
- **Otherwise:** `t = now + interval`, but at least `now + 120 s`. The 120 s matters: the alarm has to fire after the PiSugar has cut power, or it's missed until the same time the next day. 📐
- **Quiet hours:** if `t` falls inside them (local time, possibly crossing midnight), move `t` to their end.
- **Arm it:** `rtc_alarm_set <t as local ISO 8601 with offset> 127`. Read back `get rtc_alarm_enabled` and `get rtc_alarm_time`, then save `next_wake_utc`.
- **The PiSugar alarm is a time of day only** ✅, so any interval up to 24 h works. Daylight-saving changes are handled because each alarm is computed fresh.

### 8.13 Shutdown

`CameraApp.request_shutdown(reason)` starts the shutdown once; later calls are ignored while it runs. Reasons: `power_button`, `auto_off`, `low_battery`, `slideshow`, `wake_timeout`, `system`, `update`.

1. Lifecycle → `SHUTTING_DOWN`. BTN is ignored. The light shows wait while steps 2 and 3 run a screen update, otherwise off (§8.6).
2. If a screen update is running, wait for it (up to 30 s from step 1), then abort it if needed. Drop the pending job.
3. **Work out the next wake** (§8.12; for `low_battery` there is none). **White screen** (§2.1), only when all of these hold:
   - there is no next wake: the slideshow is off, the clock is unset, there are no photos, or the battery is too low;
   - this is a power-off: not the update's reboot, not any other reboot, and not systemd stopping only the app (`systemctl restart inkstant`, an install, a crash restart). On a stop from outside (SIGTERM), the app reads `systemctl list-jobs`: `reboot.target` means a reboot; `poweroff.target`, `halt.target` or `shutdown.target` means a power-off; none of them means only the app is stopping. If it can't tell, it counts as a power-off. (Fixed 2026-10-16: on the Pi 4, a restart turned the screen white and took 18 s.)
   - a module is in.

   Run `BLANK` and wait for it (about 22 s). Removing the module aborts it and shutdown carries on; the driver's own timeouts bound it otherwise. Afterwards `current` is null and `display_content` is `blank`. This follows Waveshare's advice to store panels white when nothing will refresh them. 📐
4. Share mode off without restoring the display (§9.3), and stop and close the camera. Both run alongside step 3.
5. Arm the wake worked out in step 3 (`rtc_alarm_set`), or disable the alarm when there is none (`rtc_alarm_disable`).
6. Save state (`last_shutdown`), with fsync.
7. Disable the PiSugar watchdog (§6.6). The app has kept feeding it up to this point.
8. `systemctl poweroff --no-block`. systemd then stops the app (SIGTERM), which exits at once since the work is done.
   - For reason `update` (asked for by the updater, §16.1) it's `systemctl reboot --no-block`, which the shutdown hook ignores.
   - If `power_after_update` is set, the update's shutdown becomes a power-off: reason `power_button`, with the white-screen rule applied as for any power-off.

If shutdown started from outside (SIGTERM from `sudo poweroff`, say), the same steps run within the unit's 75 s stop timeout (up to 30 s waiting for a running screen update, plus about 22 s for the white screen). The shutdown hook (§13.4) arms the PiSugar's power cut last.

### 8.14 Errors

| Code | Cause | Gallery | Recovery |
|---|---|---|---|
| `camera` | Camera won't start, or a capture or wake timed out | "Camera problem: <detail>" | Retry ladder (§6.4) |
| `display` | BUSY timeout with a module detected | "Display module not responding" | Cleared by the next good screen update, or by putting the module back on |
| `storage` | Write failed (not out of space) | "Couldn't save the photo" | Next good save |
| `storage_full` | Below 500 MB free (§8.15) | "Storage full" banner | Deleting photos |
| `pisugar` | No socket, or watchdog setup failed | "Battery board not responding" | The battery poll (every 60 s) tries again; once pisugar-server answers, the PiSugar's settings, charge limit and watchdog are set up again (fixed 2026-10-17: before, a late pisugar-server left the watchdog off) |
| `wifi` | Share mode couldn't start any network | Logged | Share mode off |
| (setup check) | A critical setup-check item failed (§15.8) | "Setup check found a problem" banner, from `setup_check` in the status rather than an error code | Fix and run the check again |
| `clock` | RTC unset | "Set the time from your phone" | Time set |

Every error is also logged with its detail and kept in memory for `/api/status`. **None shows on the light** (decided 2026-10-17, replacing the problem pattern): a tap the error stops gets no wink, and the gallery says why.

### 8.15 Storage

- **Free space:** `os.statvfs("/var/lib/inkstant")`, `f_bavail × f_frsize`.
- **Thresholds (decided 2026-10-15):**
  - Below 1 GiB → `low`: a banner, about 200 to 300 photos left.
  - Below 500 MiB → `full`: the shutter is refused with no wink, and an error banner offers to select photos to delete.
- **Checked:** before accepting a press, after each commit and delete, and every 10 minutes.
- **Clears by itself:** deleting photos from the gallery frees space, and the next check clears the state.
- **"Photos left"** in `/api/status` is free space minus 500 MiB, divided by the average size of the last 20 photos (4 MB when there are none).

## 9. Share mode

### 9.1 Starting

1. `share = starting`; light → wait (slow blink). From here until sharing has stopped, taps take no photos (§1.2).
2. Start the gallery (`systemctl start inkstant-gallery`) alongside the Wi-Fi, since it listens on every address; it's ready once `http://127.0.0.1/healthz` answers (polled every 200 ms, up to 10 s).
3. `nmcli radio wifi on`; wait for wlan0 (§6.8).
4. **Home** (skipped when a double tap while sharing switches to the camera's Wi-Fi, §9.3, or for `inkstant-ctl share on --hotspot`): if wlan0 is already on an `inkstant-home-*` network (developer mode keeps it for SSH), use it, with no scan. Otherwise scan, and try each visible `inkstant-home-*` network, strongest first, after matching its saved security to the scan (§9.5). On success, get the IP; `url = http://<IP>/`; mode `home`; send `rtc_web` (§6.7).
5. **Away:** if no home network is visible or joining fails, bring up `inkstant-hotspot`; `url = http://10.42.0.1/`; mode `hotspot`. Then wait for the gallery from step 2.
6. `share = on(mode, url)`; with a module in, queue `SHARE_SCREEN`.
7. **If no network came up, or the gallery didn't start:** `wifi` error, then share mode off. The module goes back to its photo, developing one saved just before sharing started (fixed 2026-10-17).

**Timing:** home about 10 to 20 s (radio, scan, join, DHCP); hotspot about 5 to 10 s. 📐 🧪 The light blinks slowly from step 1, and the share screen is the sign that it worked.

### 9.2 While on

- **Activity:** the gallery reports it through `share.activity`, at most every 10 s.
  - Status polls (marked `X-Inkstant-Poll: 1`) don't count, so a tab left open doesn't keep the camera sharing forever on battery.
  - While a ZIP is streaming, the gallery reports activity every 30 s, so the timeout never cuts a download short (§11.5).
- **Timeout:** the effective share timeout (§8.7), only on battery. When the plug state changes, the timer restarts from the last activity.
- **Turn-off is paused** (§8.7). Sleep isn't: the image sensor can sleep while the gallery is in use.
- **No shooting** (decided 2026-10-17, replacing "shooting works"): a tap is refused with no wink, and the light blinks slowly. The share screen says to hold the shutter to stop sharing and take photos.
- **A double tap** switches Wi-Fi (§9.3).
- **Show on camera** works (§8.3).
- **A module put on** gets the share screen.

### 9.3 Stopping and switching

**Stopping.** Triggers: a BTN hold, the share timeout, the gallery's "Stop sharing" button, or shutdown.

1. `share = stopping`.
2. `systemctl stop inkstant-gallery`.
3. `nmcli connection down` the active profile; `nmcli radio wifi off` (unless `dev_mode`).
4. `share = off`. Restore the display (§8.3), except during shutdown. Taps take photos again once the restored photo is shown.

**Switching Wi-Fi** (a double tap while sharing, decided 2026-10-17): home Wi-Fi → the camera's own, or back.

- Stop sharing without restoring the display, then start it again on the other network (§9.1).
- With no home network saved, a double tap on the camera's Wi-Fi does nothing.
- If home Wi-Fi can't be found on the way back, sharing carries on over the camera's Wi-Fi.
- If the new start fails, the photo goes back on the module, so no QR code is left up that no longer works.

### 9.4 Hotspot profile, DHCP and DNS

- **Profile:** §6.8. NetworkManager's shared mode runs dnsmasq for DHCP and DNS on 10.42.0.1. ✅
- **`/etc/NetworkManager/dnsmasq-shared.d/inkstant.conf`** contains `no-resolv`. dnsmasq then never forwards (the camera has no upstream when away) and refuses outside names at once. The phone's connectivity check fails quickly and cleanly, and no captive-portal sheet appears (§9.7). 📐
- **Android fallback** (if the bench test shows Chrome can't reach 10.42.0.1 with mobile data on), in order:
  1. Share screen text: "If your phone asks, choose to stay connected."
  2. Test the DHCP option `dhcp-option=3` (no gateway), so phones keep using mobile data for the internet and reach 10.42.0.0/24 over Wi-Fi.
  3. Last resort: answer Android's connectivity check. That brings back a sign-in page on Android only, and needs a product decision.

  🧪 All three need testing on iPhone too.

### 9.5 Home networks

- **Up to five, in priority order** (`inkstant-home-1` to `-5`). The install script imports the one Imager set up.
- **Choosing:** the strongest visible network wins.
- **WPA2 and WPA3:** a network is saved as WPA2 (`wpa-psk`), which also joins WPA2/WPA3 routers and uses WPA3 when the chip can. The scan when sharing starts shows whether a network is WPA3 only; if so, its profile switches to `sae` before joining, and back if the router offers WPA2 again (§6.8).
  - So a WPA3-only network added in the gallery joins the first time sharing starts in range of it. Until then, developer mode can't auto-connect to it at boot (§3, finding 26).
  - The scan is the camera's own, so it only sees bands the camera can join: on a Pi 3B, just 2.4 GHz (§15.1.2).
  - 🧪 Not yet tried with a WPA3-only network (§3.3).
- **Wi-Fi with client isolation** (often guest networks): the phone can't reach the camera. A double tap while sharing switches to the hotspot (§9.3).

### 9.6 What the phone does

- **QR codes:**
  - **At home:** the QR code uses the camera's IP rather than `<hostname>.local`, so it works whether or not the phone resolves `.local` names. 🧪 Android
  - **Away, joining:** point the phone's camera at the Wi-Fi QR code and tap Join. That works on iPhone with iOS 11 and later, and on Android 10 and later. ✅ [Wi-Fi QR guide](https://moda.app/tools/wifi-qr-code)
  - **Away, opening:** the second QR code is `http://10.42.0.1/`. A plain IP address needs no name lookup, and the page opens in Safari or Chrome.
- **Saving one photo:**
  - iPhone: press and hold, then Save to Photos. ✅ [iPhone Life](https://iphonelife.com/node/280947)
  - Android Chrome: Download image, saved to the Downloads folder. ✅ [forum](https://forum.earlybird.club/goto/post?id=7822035)
  - The full-size photo must be a plain image, not a link: iOS has hidden Save Image on linked images. ✅ [BGR](https://www.bgr.com/tech/ios-10-update-save-image-missing/)
- **Saving many:**
  - iPhone: Safari saves the ZIP to Files → Downloads. Tap it to unzip, tap Select, select all, then save them to Photos. ✅ [Picdrop](https://www.picdrop.com/web/faq/how-do-i-download-images-to-an-ipad-or-iphone-ios)
  - Android: the Files app extracts it. 🧪
- **`camera.local` style names** work at home through Avahi's mDNS hostname. ✅ [Pi docs](https://www.raspberrypi.com/documentation/computers/configuration.html)
- **Hotspot command:** `nmcli device wifi hotspot` would also work ✅ [Digi-Key](https://www.digikey.com/en/maker/tutorials/2026/how-to-create-a-wireless-access-point-on-raspberry-pi). We save our own profile instead (§6.8), to control the security settings and the address.

### 9.7 Accepted limits

- **No one-tap "save all to Photos":** the share sheet (Web Share API) needs HTTPS. ✅ [MDN](https://developer.mozilla.org/en-US/docs/Web/API/Web_Share_API) The ZIP is the bulk path.
- **No HTTPS in v1.** It was considered, and it's possible, but not worth it:
  - **A trusted certificate per camera (the Plex model):** Plex gives every server its own wildcard certificate under `plex.direct`, issued by DigiCert, with a domain that resolves to the server's IP. ✅ [Filippo Valsorda](https://words.filippo.io/how-plex-is-doing-https-for-all-its-users/) For us that means owning a domain, and running a service that gets each camera a certificate.
    - Certificates also expire: Let's Encrypt's default is 90 days, dropping to 45 days by February 2028. ✅ [Let's Encrypt](https://letsencrypt.org/docs/cert-lifetimes/) A camera that doesn't get online before then loses HTTPS.
  - **One shared certificate for every camera** isn't allowed. Publishing a trusted certificate's private key makes it compromised, and CAs must revoke within 24 hours; a project that tried this saw its certificate revoked within nine hours. ✅ [Dev Genius](https://blog.devgenius.io/the-certificate-everyone-could-share-688f9dbac5c2)
  - **The camera's own certificate authority:** each phone must install a profile, then turn on full trust under Settings → General → About → Certificate Trust Settings. Apple aims this at system administrators. ✅ [Apple](https://support.apple.com/en-ca/102390)
  - **The payoff is small:** Safari's share-sheet "Save Image" for web pages has been missing or broken in several iOS versions. ✅ [WebKit bug 255544](https://bugs.webkit.org/show_bug.cgi?id=255544) Press-and-hold "Save to Photos" already works over `http://`.
- **No captive-portal pop-up (decided 2026-10-15).** The camera doesn't answer the phone's captive-portal check, so no "sign in to network" sheet appears, and the gallery is opened from its QR code instead. A pop-up was considered for opening the gallery automatically, but on iPhone it has three problems:
  - The sheet is a mini browser that can't download files. ✅ [The Zero Net](https://thezeronet.com/?p=49654), [SecureW2](https://securew2.com/blog/apple-captive-network-assistant-understanding-captive-portals-on-ios-devices)
  - There's no supported way to open Safari from it on current iOS. ✅ [Apple forums](https://developer.apple.com/forums/thread/834869)
  - Tapping Cancel disconnects the iPhone from the network. ✅ [Apple](https://support.apple.com/en-us/102554) Getting a "Done" button instead would make the iPhone treat the camera's Wi-Fi as online, and stop using mobile data.
- **Networks that block devices from reaching each other** (often guest networks): the phone can't reach the gallery at home. A double tap while sharing switches to the camera's own Wi-Fi.

## 10. Gallery server

The server side of the phone web app. What the phone shows and does is §11.

### 10.1 Service and server

- `python3 -m inkstant gallery`: a Flask app served by Waitress, `listen="0.0.0.0:80"`.
- **Runs as** `inkstant-web` with `CAP_NET_BIND_SERVICE` only, and systemd sandboxing (§13.3).
- **Waitress settings:**

| Setting | Value | Why |
|---|---|---|
| `threads` | 4 | Default ✅ |
| `connection_limit` | 24 | A few phones at once |
| `channel_timeout` | 120 | Default ✅ |
| `outbuf_high_watermark` | 4 MiB | Pause streaming earlier than the 16 MB default ✅ |
| `outbuf_overflow` | 1 MiB | Default; with `PrivateTmp=yes` the spill file stays in a private `/tmp` |
| `expose_tracebacks` | False | ✅ |
| `ident` | `inkstant` | |

- **Photo files** go through `flask.send_file(..., conditional=True, etag=True, max_age=31536000)`. Waitress streams them from disk (`wsgi.file_wrapper`), not memory. The response also gets `Cache-Control: public, max-age=31536000, immutable`, since an ID is never reused.
- **Listing:** `os.scandir(photos/)`, cached until the folder's `mtime_ns` changes.

### 10.2 Routes

| Method and path | Purpose | Response |
|---|---|---|
| `GET /` | Gallery page | HTML |
| `GET /p/<id>` | Photo page: the full-size image as a plain `<img>` (long-press saves it), previous/next, download, delete, show on camera | HTML |
| `GET /settings` | Settings page | HTML |
| `GET /static/<file>` | CSS, JS, icons; linked with `?v=<version>` and sent with `no-cache`, so after an update they revalidate (a cheap 304) | |
| `GET /photos/<id>.jpg` | Full photo; `?dl=1` adds `Content-Disposition: attachment; filename="inkstant_<id>.jpg"` | JPEG |
| `GET /thumbs/<id>.jpg` | Thumbnail | JPEG |
| `GET /prints/<id>.png` | The print as the module shows it; `?dl=1` names it `inkstant_<id>_print.png`. Sent with `no-cache`, since a palette change remakes it | PNG |
| `GET /api/photos?before=<seq>&limit=60`, or `?after=<seq>` for photos newer than the newest shown, and `&month=YYYY-MM` (or `none` for clock-unset photos) to jump the grid | Page of photos, newest first | `{"photos":[{"id","seq","day","time","size"}],"next_before":seq or null,"total":n,"offset":n}`; `offset` counts the photos before the page's first, so the viewer can say "Photo n of N" after a jump; `day` and `time` are null for photos taken with the clock unset |
| `GET /api/photos/<id>` | One photo's details for the info sheet (EXIF read with piexif) | `{"id","day","time","size","width","height","exposure_s","iso","current","undeveloped"}` |
| `GET /api/months` | Months with photo counts, for "Jump to month" | `[{"month":"2026-10","count","first_seq"}, …]`, plus one entry with `"month": null` for undated photos |
| `GET /api/status` | Banners and state, polled every 10 s by every page | §10.3 |
| `GET /api/selftest`, `POST /api/selftest` | Read the setup check results, or run it again (§15.8) | Results per check |
| `POST /api/photos/delete` | `{"ids":[…]}`, or `{"all": true}` for "Select all" → forwarded to the camera app | `{"deleted":[…],"missing":[…]}` |
| `POST /api/photos/<id>/show` | Show on camera; queued if the screen is busy (§8.3) | 202, 404 `not_found`, or 409 `no_module` or `not_ready` |
| `GET /download.zip?set=all` | ZIP of every photo ("Select all" also sends `set=all`) | Streamed ZIP (§10.4), with `Content-Disposition` naming it as in §11.4.4 |
| `POST /download.zip` | Form field `ids`: selected photos, or a whole day | Streamed ZIP |
| `GET`, `POST /api/settings` | Read, or change some settings (§5.4) | Settings, or 400 with a per-field message |
| `POST /api/time` | `{"epoch_ms":…, "tz":"Europe/Madrid"}` | 204 |
| `GET /api/wifi` | Home network names and the hotspot name | JSON, no home passwords |
| `POST /api/wifi/home` | `{"ssid","password"}`: add, or replace one with the same name | `{"n":…}`, the slot it's in, or 400 |
| `DELETE /api/wifi/home/<n>` | Forget a home network | 204 |
| `POST /api/share/stop` | Stop sharing | 204 |
| `GET`, `POST /api/update` | Check for a newer release; start the update (§16.1) | JSON |
| `GET /api/logs` | Download logs: `journalctl -b 0 -b -1`, at most 2 MB | Text |
| `GET /healthz` | For the camera app's start-up check; answers only on 127.0.0.1 | `ok` |

### 10.3 Status payload

`GET /api/status` (and the control socket's `status`), polled every 10 s by every page:

```json
{"battery":{"level":72,"plugged":false,"low":false},
 "battery_saver":{"on":false,"why":null},
 "storage":{"free_bytes":0,"total_bytes":0,"state":"ok","photos_left":0,"used_bytes":0},
 "clock_valid":true,"module_present":true,"camera":"ready",
 "share":{"mode":"hotspot","url":"http://10.42.0.1/","ssid":"Inkstant-4F2A","state":"on",
          "timeout_min":10,"on_battery":true},
 "errors":[{"code":"display","message":"…","since":"…"}],
 "undeveloped":"000123_20261015-143022","current":"000122_20261015-142901",
 "version":"1.0.0","update":{"state":"idle","step":null,"message":null},
 "newest_seq":124,"photo_count":124,"today":"2026-10-15","time":"2026-10-15T14:32:05+02:00",
 "tz":"Europe/Madrid","display":{"state":"idle","id":null},
 "wifi_5ghz":true,"setup_check":{"passed":true,"failed":[]},
 "lifecycle":"ACTIVE","turn_off_min":10,"dev_mode":false,"model":"Raspberry Pi 3 Model A Plus Rev 1.1"}
```

- **`camera`:** `off`, `starting`, `ready`, `fallback`, `asleep`, `waking` or `failed` (§8.1).
- **`storage.state`:** `ok`, `low` or `full` (§8.15).
- **`share.timeout_min`:** the effective timeout (§8.7). `on_battery` tells the page whether it applies.
- **`update.state`:** `idle`, `running` (with `step`: `downloading`, `installing` or `restarting`), `done` or `failed` (with `message`).
- **`share`** is null when sharing is off; its `state` is `starting`, `on` or `stopping`.
- **`lifecycle`**, **`turn_off_min`** (the effective turn-off time), **`dev_mode`** and **`model`** (the board) are for the settings page.
- **`display.state`:** `idle` or `developing`; `id` is the photo being developed.
- **`newest_seq` and `photo_count`** drive the "new photo" pill. `today`, `time` and `tz` are the camera's date, clock and time zone (§11.5.3).
- **`wifi_5ghz`** is false on the Pi 3B (§15.1.2).
- **`setup_check`** comes from the last self-test (§15.8).

### 10.4 ZIP downloads

`gallery/zipstream.py` 📐:

- **Snapshot:** list the chosen photos and `stat` each one when the request starts.
- **For each file:**
  - Read the whole file (≤ 8 MB) and compute `zlib.crc32`.
  - Send a local header with the real CRC and sizes (method 0 "stored", flags 0, DOS date and time from the photo ID), the name, then the data.
  - No data descriptors: some readers that walk local headers can't handle stored entries with descriptors.
- **Central directory** at the end. ZIP64 records (per-entry extra field, ZIP64 end record and locator) only when an offset or size passes 4 GiB, or there are more than 65,535 entries.
- **`Content-Length`** is computed up front from the snapshot (every size is known, nothing is compressed), so phones show a progress bar.
- **A file deleted mid-download** ends the response early, and the phone reports a failed download. Accepted: rare, and harmless.
- **Memory:** one file at a time, plus Waitress's 4 MiB high watermark.
- 🧪 Opens in iOS Files (Downloads → tap → folder) and in the Android Files app; progress shows in Safari and Chrome.

### 10.5 Security

- **Host check:** every request's `Host` must be an IP address (the share URL's, `10.42.0.1`, `127.0.0.1`), `<hostname>.local` or `localhost`. Anything else gets 421. This blocks DNS rebinding, which needs an attacker's host name; a page served from the camera's own IP is the camera.
- **Changes need proof of same origin:** every request that changes something (`POST`, `DELETE` under `/api/`) must carry `X-Inkstant: 1` and, if present, an `Origin` matching the Host. A form or script from another site can't add that header without a CORS preflight, and the gallery never answers preflights. `POST /download.zip` changes nothing, so it's exempt.
- **IDs** are checked with the regex in §5.1, and network numbers against 1 to 5.
- **Headers on every response:** `Content-Security-Policy: default-src 'self'; img-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`, plus `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`. So the templates have no inline scripts or styles (§11.8).
- **The gallery process can't change anything itself:** photo files are read-only to it, and settings, Wi-Fi, deletes and time all go through the control socket, which checks each command (§12).
- **Hotspot password** shows on the settings page; it's on the share screen anyway. Home Wi-Fi passwords are never sent to the phone.

## 11. Gallery web app

The app the camera serves in share mode: what it's for, every screen, how it looks and behaves, and the words it uses. The server side (routes, status, ZIP streaming, security) is §10. **Mockup:** [`mockups/gallery.html`](mockups/gallery.html), also published as a private page: [Inkstant Gallery Screens](https://claude.ai/artifact/PjVPnzEf1exNV4VfgkknP1). It shows the main screens; a switch flips the phones between light and dark.

### 11.1 What it's for

**Jobs, in order of how often they happen:**

1. Save one photo to the phone.
2. Save several photos, a whole day, or everything.
3. Look through photos at full size.
4. Put a chosen photo on the display module ("Show on camera").
5. Delete photos.
6. Change the camera's settings: slideshow, power, Wi-Fi, time, updates.

**Where it's used:**

- **Away:** the phone joins the camera's own Wi-Fi, with no internet.
- **At home:** the phone is on the home Wi-Fi.
- **Devices:** mostly phones (iPhone Safari, Android Chrome); sometimes a laptop at home.
- **Several people at once:** no accounts, no login (§1.7).

**Constraints that shape the design:**

| Constraint | Consequence |
|---|---|
| No internet on the camera's Wi-Fi | Everything ships from the camera: no web fonts, CDNs or third-party scripts. System fonts and inline SVG icons only |
| Plain `http://`, no HTTPS (§9.7) | No Web Share sheet, no service worker, no "install as app", no Clipboard API. These all need a secure context. ✅ [MDN secure contexts](https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts/features_restricted_to_secure_contexts) |
| 2.4 GHz camera Wi-Fi, about 3 to 5 MB/s 📐; photos are 3 to 5 MB | The grid uses 480 × 270 thumbnails; full photos load only in the viewer; downloads show their size first |
| A Pi 3A+ serving with 4 threads | Few requests per screen; thumbnails and photos cached forever (IDs are never reused) |
| The camera can turn off or stop sharing at any time | Every screen handles "can't reach the camera" (§11.4.9) |

**Browser support:**

- **Targets:** iOS and iPadOS Safari 15.4+, Android Chrome 108+, and current desktop Chrome, Firefox, Safari and Edge.
- **Why that line:** `<dialog>`, `loading="lazy"`, dynamic viewport units (`dvh`) and `:focus-visible` all arrive in iOS Safari 15.4. ✅ [MDN compatibility data](https://github.com/mdn/browser-compat-data)
- **Optional extra:** `content-visibility` needs Safari 18, so it's used only where available. The Fullscreen API is only partial on iPhone, so it isn't used. ✅

### 11.2 Map of the app

```text
                 ┌──────────────────────────────┐
  QR code ──────►│  Gallery  /                   │──⚙──►┌───────────────┐
                 │  grid of every photo, by day  │      │ Settings      │
                 └──┬───────────┬────────────┬───┘◄─────│ /settings     │
                    │tap photo  │Select      │⋯ menu    └──┬────────────┘
                    ▼           ▼            ▼             │ dialogs: home network,
          ┌──────────────┐ ┌───────────┐ ┌───────────────┐ │ camera Wi-Fi, update,
          │ Photo viewer │ │ Select    │ │ Download all  │ │ developer mode
          │ /p/<id>      │ │ mode      │ │ Jump to month │ │
          └──┬───────────┘ └──┬────────┘ └───────────────┘ │
             │ Download / Delete / Show on camera          │
             ▼                ▼                            ▼
       Download sheet    Delete confirm         Full-page states: can't reach
                                                the camera · sharing off · updating
```

- **The viewer opens on top of the gallery** with `history.pushState`, so the browser's back button or swipe-back closes it and the grid keeps its scroll position. `/p/<id>` also works as a direct link: closing it then goes to the gallery.
- **Settings is a separate page.** The Back link returns to the gallery.

### 11.3 Visual design

#### 11.3.1 Direction

Quiet, paper-grey surroundings that step back for the photos. The colour comes from the e-paper panel's own six inks. The mark next to the name is the panel's colour strip, in the order the panel numbers its colours (black, white, yellow, red, blue, green). The grid has thin gutters and square tiles, like a contact sheet. Photos sit edge to edge with no rounded corners or shadows; only floating things (sheets, dialogs, the action bar) get a shadow. 📐

#### 11.3.2 Colour

Every colour is a token. The app follows the phone's light or dark setting (`prefers-color-scheme`); the photo viewer is always dark.

| Token | Role | Light | Dark |
|---|---|---|---|
| `--bg` | Page background (the panel's cool grey, lightened) | `#EEF0F0` | `#101214` |
| `--surface` | Cards, sheets, dialogs, bars | `#FFFFFF` | `#1A1D20` |
| `--surface-2` | Chips, meter tracks, skeleton tiles | `#E2E5E6` | `#25292D` |
| `--line` | Dividers, input borders | `#CDD2D4` | `#353A3F` |
| `--ink` | Main text | `#16181A` | `#ECEEEF` |
| `--ink-2` | Secondary text | `#454B50` | `#B3B9BD` |
| `--ink-3` | Captions, counts, hints | `#62696E` | `#8E959A` |
| `--accent` | Links, buttons, toggles, selection (the panel's blue, made richer) | `#3A3F9E` | `#A3A9F7` |
| `--accent-ink` | Text on the accent | `#FFFFFF` | `#14163A` |
| `--danger` | Delete, destructive text | `#A3343A` | `#F28E92` |
| `--ok-ink` | "Saved", charging | `#2C6643` | `#82C9A0` |
| `--warn-bg` / `--warn-ink` | Warning banners (the panel's yellow) | `#F2E6A2` / `#3F3500` | `#3A3415` / `#F0DC82` |
| `--err-bg` / `--err-ink` | Error banners (the panel's red) | `#F6DEDF` / `#7A1E23` | `#3D1C1F` / `#F7B9BC` |
| `--info-bg` / `--info-ink` | Info banners | `#E2E3F5` / `#272B6E` | `#22244A` / `#C9CCFA` |
| `--viewer-bg` / `--viewer-ink` | Photo viewer | `#000000` / `#F2F2F2` | same |
| `--ink-k`, `--ink-w`, `--ink-y`, `--ink-r`, `--ink-b`, `--ink-g` | The panel's six inks (Pimoroni's values, the palette before version 3, §7.1), for the logo strip only | `#000000`, `#A1A4A5`, `#D0BE47`, `#9C484B`, `#3D3B5E`, `#3A5B46` | same |

**Contrast, computed (WCAG 2.x ratio):**

- **Text:** ink, ink-2, ink-3, accent, danger and ok-ink on the background and on surfaces are each 4.9:1 or more in light and 5.6:1 or more in dark; ink-3 is the lowest.
- **Banners and buttons:** banner text on its fill is 8.1:1 or more; text on the accent is 8.8:1 (light) and 7.9:1 (dark).
- **Viewer bars** sit on a black gradient scrim so their text stays readable on bright photos.
- These all pass WCAG AA (4.5:1). 📐 computed from the token values.

#### 11.3.3 Type

- **Fonts:** `system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", sans-serif`, so San Francisco on iPhone and Roboto on Android. Technical values (file sizes, exposure, versions, IP addresses) use `ui-monospace, "SF Mono", Menlo, Consolas, monospace`. All numbers use `tabular-nums`.
- **Scale** (rem, so it follows the browser's text size):

| Role | Size / weight | Used for |
|---|---|---|
| Title | 1.5rem / 650 | "Photos", "Settings" |
| Heading | 1.0625rem / 600 | Day headers, settings sections, sheet titles |
| Body | 1rem / 400 | Everything else |
| Small | 0.875rem / 400 | Status line, secondary rows, toasts |
| Label | 0.75rem / 600, uppercase, +0.04em | Settings group labels, badges |

- **Inputs are never smaller than 16 px**, or iOS Safari zooms in when they're tapped. ✅ [West Wind on iOS input zoom](https://weblog.west-wind.com/posts/2023/Apr/17/Preventing-iOS-Textbox-Auto-Zooming-and-ViewPort-Sizing)

#### 11.3.4 Space, shape, elevation

- **Spacing:** a 4 px grid (4, 8, 12, 16, 24, 32). The side gutter is 16 px, widened by the phone's safe-area insets.
- **Photo grid:** 2 px gaps; tiles have square corners.
- **Radius by role:** 12 px for sheets and dialogs; 8 px for buttons, inputs, banners and settings groups; full pill for chips and toggles.
- **Shadows:** only on sheets, dialogs, the select-mode action bar and toasts.
- **Width:** content tops out at 1280 px on large screens; settings at 640 px.

#### 11.3.5 Icons

- **Style:** an inline SVG sprite (`templates/icons.html`) on a 24 px grid, 1.75 px stroke, round caps.
- **Set:** back, close, settings, more (⋯), select, check, download, delete, show on camera (a small display module with an arrow), Wi-Fi, battery (with a charging bolt), info, calendar, warning, error, refresh.
- **Labels:** every icon button has a visible label or an `aria-label`, and a 44 × 44 px tap area.

#### 11.3.6 Motion

- **Viewer:** fades and scales in from the tapped tile (150 ms).
- **Sheets:** slide up (200 ms).
- **Toasts:** fade.
- **No spinners;** loading uses grey skeleton tiles.
- **Reduced motion:** with `prefers-reduced-motion`, everything is instant.

### 11.4 Screens

Wireframes are for a 390 px wide phone. The mockup shows them rendered.

#### 11.4.1 Gallery (`/`)

```text
┌──────────────────────────────────────┐
│ ▮▯▮▮▮▮ Inkstant      Select  ⋯   ⚙  │  header, sticky
│ 1,284 photos · ▮▮▯ 72% · Camera Wi-Fi│  status line
├──────────────────────────────────────┤
│ ⚠ Storage low: about 240 photos left │  banners, if any
├──────────────────────────────────────┤
│ Today                            14  │  day header, sticky under the header
│ ┌────────┐┌────────┐┌────────┐       │
│ │        ││ ▣      ││        │       │  3 square tiles per row, 2 px gaps;
│ └────────┘└────────┘└────────┘       │  ▣ = on the camera's display
│ ┌────────┐┌────────┐                 │
│ └────────┘└────────┘                 │
│ Yesterday                         6  │
│ ┌────────┐┌────────┐┌────────┐       │
│               …                      │
│      That's everything · 3 Mar 2026  │  end of the list
└──────────────────────────────────────┘
```

- **Header:**
  - The logo strip and "Inkstant".
  - **Select** (text button), **⋯** (menu: Download all…, Jump to month…, Stop sharing), and **⚙** (Settings).
- **Status line:** photo count · battery level (with a charging bolt, and "Saver" when the battery saver is on) · how it's sharing ("Camera Wi-Fi" or the home network's name). Tapping it opens Settings.
- **Day headers:**
  - "Today", "Yesterday", then the weekday and date ("Mon 12 Oct"), and the year when it isn't this year ("12 Oct 2025").
  - "Today" means the camera's date, not the phone's.
  - The day's photo count is on the right.
  - Photos taken while the clock was unset go under "Unknown date", at the position their sequence number gives them.
- **Tiles:**
  - Square crops of the thumbnail (`object-fit: cover`): 3 per row under 600 px wide, 4 from 600, 5 from 900, 6 from 1200.
  - **Badges:** "On camera" (the display-module icon) on the photo the module was last given; a pulsing "Developing" while it's being developed. Each badge has an icon and a text label for screen readers.
- **Loading:**
  - The first 60 photos come in the page's HTML, so the grid paints at once.
  - When you scroll within 1.5 screens of the end, the next 60 load, with a row of skeleton tiles meanwhile.
  - The list ends with "That's everything" and the date of the oldest photo.
- **New photos while it's open:** the status poll reports the newest sequence number (§11.5.1). If you're at the top, new photos appear at once. If you've scrolled down, a "1 new photo" pill appears at the top; tapping it scrolls up and adds them.
- **Empty:** the logo strip, "No photos yet", and "Tap the shutter button on the camera. New photos show up here straight away."
- **Jump to month** (in ⋯): a sheet listing months with photo counts ("October 2026 · 214").
  - Choosing one reloads the grid from that month onwards, with a "Back to newest" pill at the top.
  - Useful once there are thousands of photos.

#### 11.4.2 Photo viewer (`/p/<id>`)

```text
┌──────────────────────────────────────┐
│ ✕      Thu 15 Oct · 14:30         ⓘ │  top bar on a dark gradient
│                                      │
│                                      │
│ ┌──────────────────────────────────┐ │
│ │                                  │ │  the photo, fitted to the screen,
│ │           (full photo)           │ │  black background
│ │                                  │ │
│ └──────────────────────────────────┘ │
│                                      │
│  ▣ Show on camera   ⤓ Download  🗑 Delete │  bottom bar on a dark gradient
└──────────────────────────────────────┘
```

- **Loading:**
  - The thumbnail appears at once, stretched and slightly blurred.
  - The full photo (`/photos/<id>.jpg`) fades in over it when it has loaded, typically about a second on the camera's Wi-Fi. 📐
  - The next and previous full photos are preloaded once the current one is in.
- **Saving:**
  - The photo is a plain `<img>`, never a link or a CSS background, and nothing blocks the system's press-and-hold menu.
  - So press-and-hold gives "Save to Photos" on iPhone and "Download image" on Android (§9.6).
  - The first time the viewer opens on a phone, a tip shows once: "Press and hold the photo to save it to your phone." Its dismissal is remembered in the browser.
- **Moving between photos:**
  - Swipe left or right, or use the ← → keys on a computer.
  - Swiping only works when the page isn't pinch-zoomed (`visualViewport.scale` is 1), so zooming in to check focus never jumps to the next photo.
  - Pinch-zoom is never disabled.
- **Tap the photo** to hide or show the bars.
- **ⓘ Info** opens a sheet:
  - Date and time.
  - File size, and dimensions (4608 × 2592).
  - Exposure (for example 1/60 s), ISO.
  - File name.
- **Show on camera:**
  - Greyed out, with the reason under it, when there's no display module ("No display module in the camera") or the camera is busy starting.
  - When tapped: a toast "Developing on the camera, about 20 seconds", and the tile badge follows the progress.
- **Download:** a link to `/photos/<id>.jpg?dl=1`. On iPhone it goes to Files → Downloads; press-and-hold is the way into Photos.
- **Delete:** confirm (§11.4.5), then show the next photo (or the previous one if it was the last), with the toast "Photo deleted".
- **Deleted elsewhere** (by another phone while you're viewing it): "This photo was deleted" and a Close button.

#### 11.4.3 Select mode

```text
┌──────────────────────────────────────┐
│ Cancel        3 selected  Select all │  replaces the header
├──────────────────────────────────────┤
│ Today                       Deselect │  per-day select/deselect
│ ┌────────┐┌────────┐┌────────┐       │
│ │      ✔ ││      ○ ││      ✔ │       │  ✔ selected (accent ring), ○ not
│ └────────┘└────────┘└────────┘       │
├──────────────────────────────────────┤
│ ⤓ Download · 12 MB   ▣ Show   🗑 Delete │  action bar, above the home indicator
└──────────────────────────────────────┘
```

- **Entering and leaving:** enter with **Select**; leave with **Cancel**, the back button, or after an action.
- **Selecting:**
  - Tap a tile to select or unselect it. A selected tile gets a filled check, a 3 px accent ring inside its edge, and a slight dim.
  - **Select all** selects every photo, including ones not yet loaded (the server resolves "all").
  - Each day header offers **Select** or **Deselect** for that day.
- **Action bar:**
  - **Download** shows the total size.
  - **Show on camera** works only when exactly one photo is selected.
  - **Delete**.
  - The bar is disabled until something is selected.

#### 11.4.4 Downloading several photos

**Download sheet**, opened by Download in select mode or "Download all…" in ⋯:

```text
┌──────────────────────────────────────┐
│ Download 23 photos                   │
│ 92 MB · about 30 seconds             │
│                                      │
│ Safari saves one ZIP file to         │
│ Files → Downloads. Open it there,    │
│ then select the photos and tap       │
│ Share → Save Images.                 │
│                                      │
│ [        Download ZIP        ]       │
│ [           Cancel           ]       │
└──────────────────────────────────────┘
```

- **One photo selected:** Download gets the JPEG directly; no ZIP, no sheet.
- **Time estimate:** total size ÷ 3 MB/s, rounded generously. 📐 🧪 Measure real throughput in M6 and adjust.
- **Instructions follow the device** (detected from the user agent, for this text only):
  - **iPhone or iPad:** as above. ✅ [Picdrop](https://www.picdrop.com/web/faq/how-do-i-download-images-to-an-ipad-or-iphone-ios)
  - **Android:** "Chrome saves one ZIP file to Downloads. Open it in the Files app to extract the photos." 🧪
  - **Computer:** "Your browser saves one ZIP file."
- **Over 1 GB:** an extra line: "This is a big download. Keep this page open and the phone awake until it finishes."
- **After tapping Download ZIP:**
  - The sheet closes, and the toast says "Download started. Keep the camera on until it finishes."
  - The browser's own download progress takes over; a web page can't see it.
  - Share mode won't time out during the download (§11.5.2).
- **ZIP names:** `inkstant_23-photos_2026-10-15.zip`, or `inkstant_all_1284-photos.zip`.
- **No "new photos" option:** the camera doesn't track what was downloaded (decided 2026-10-15). Selecting a day covers "everything from today".

#### 11.4.5 Delete confirmation

A `<dialog>`:

- **Title:** "Delete 3 photos?"
- **Body:** "They'll be removed from the camera. Copies already saved on a phone aren't affected."
- **If one of them is on the display module**, an extra line: "The display module keeps showing it until it gets another photo."
- **Buttons:** **Cancel** (default focus) and **Delete** (danger colour).

#### 11.4.6 Settings (`/settings`)

Grouped sections on one scrolling page, at most 640 px wide. Labels sit above each group; every row is at least 48 px tall.

```text
┌──────────────────────────────────────┐
│ ‹ Photos            Settings         │
├──────────────────────────────────────┤
│ CAMERA                               │
│ ┌──────────────────────────────────┐ │
│ │ Battery     ▮▮▮▮▮▮▯▯▯ 72% ⚡     │ │
│ │ Display     Module in · [thumb]  │ │
│ │ Camera      Ready                │ │
│ │ Light       What it means      › │ │
│ │ Sharing     Camera Wi-Fi         │ │
│ │             "Inkstant-4F2A"      │ │
│ │             Turns off after 10   │ │
│ │             min without use      │ │
│ │             [ Stop sharing ]     │ │
│ └──────────────────────────────────┘ │
│ SLIDESHOW                            │
│ ┌──────────────────────────────────┐ │
│ │ Slideshow                  (●  ) │ │
│ │ Change picture every   1 hour  › │ │
│ │ Order        [Shuffle|Newest]    │ │
│ │ Quiet hours                (  ○) │ │
│ └──────────────────────────────────┘ │
│ While the camera is off it wakes to  │
│ show the next photo. …               │
│ POWER · WI-FI · DATE & TIME ·        │
│ STORAGE · SOFTWARE · ADVANCED        │
└──────────────────────────────────────┘
```

| Group | Row | Control | Values (§1.8) |
|---|---|---|---|
| **Camera** | Battery | Meter + % + charging bolt | Read only |
| | Display | "Module in" with a thumbnail of the photo it was last given, "Developing…" while active, or "No module" | Read only |
| | Camera | Ready · Asleep ("Tap the shutter button to wake it") · Starting · Problem (with the error) | Read only |
| | Light | "What the light means" › opens a sheet with §1.3's four looks (solid, slow blink, wink, off), each drawn as a short animated strip (static under reduced motion) next to its meaning | |
| | Sharing | Network and name; "Turns off after N min without use" on battery (the effective time, §8.7), "Stays on while charging" when plugged in; **Stop sharing** button | |
| **Slideshow** | Slideshow | Toggle | On / off |
| | Change picture every | Select | 5, 10, 15, 30 min; 1, 2, 3, 6, 12, 24 h |
| | Order | Two-way switch | Shuffle · Newest first |
| | Quiet hours | Toggle; when on, From and To time pickers (`<input type="time">`) | Can cross midnight |
| | *Footnote* | "While the camera is off it wakes to show the next photo. With the slideshow off, the screen turns white when the camera turns off; take the print off first to keep its photo." | |
| **Power** | Sleep after | Select | 1, 2, 3, 5, 10, 15 min, or Never. Footnote: "Asleep, the camera takes about a second longer to shoot." When it's at or above "Turn off after": "The camera turns off before it would sleep." |
| | Turn off after | Select | 1, 2, 5, 10, 15, 20, 30, 45, 60 min without use |
| | Battery saver | Toggle, with a status line: "On" · "On because the battery is low" (the toggle shows on; turning it off clears the automatic saver, §8.9) · "Off" | Footnote: "Sleeps after 1 min, turns off after 5, and stops sharing after 5 min." |
| | Turn on at 20% | Toggle (the level follows §8.9 when the shutdown level is high) | On by default |
| | Stop sharing after | Select | 5, 10, 15, 20, 30, 45, 60 min without use, on battery only |
| | Turn off at | Select | 4, 5, 8, 10, 15, 20% battery |
| | Limit charging to about 80% | Toggle | Footnote: "Helps the battery last more years. Each charge runs about a fifth shorter." |
| **Wi-Fi** | Home networks | List of up to five names, each with **Forget**; **Add network** | At five, Add is disabled with "Forget one to add another" |
| | Camera Wi-Fi | Name, and password (hidden, with **Show**); **Change** | Footnote: "Changes apply the next time sharing starts. While sharing, double-tap the shutter button on the camera to switch between home Wi-Fi and the camera's own." |
| **Date & time** | Camera time | Live clock (camera time + offset), time zone | |
| | | **Use this phone's time** | Also takes the phone's time zone (decided 2026-10-15). If the camera is more than a minute off, the row shows "The camera's clock is 3 min behind this phone" |
| **Storage** | Meter | Used vs free; "18.2 GB free · about 4,500 more photos"; "1,284 photos · 5.1 GB" | |
| **Software** | Version | `1.0.0` | |
| | Updates | **Check for updates**. Away, it's disabled with "Needs internet. Share at home to update." When there's an update: "Version 1.0.1 is available" and **Update** | |
| **Advanced** (collapsed `<details>`) | Developer mode | Toggle, with a confirmation (§11.4.7) | Off by default |
| | Setup check | Each self-test check with pass or fail and a fix-it hint; **Run again** (§15.8) | |
| | Logs | **Download logs** | |
| | About | Model, host name, address | Read only |

**Saving:**

- **Toggles, selects, switches and time pickers** save as soon as they change. A small "Saved" tick appears beside the control for 1.5 s. On an error, the control springs back, and a toast gives the server's reason.
- **Text** (networks, camera Wi-Fi) is changed only in dialogs, with an explicit Save.

#### 11.4.7 Dialogs

| Dialog | Fields | Buttons | Notes |
|---|---|---|---|
| **Add home network** | Network name (`autocapitalize="none"`, `autocorrect="off"`); password (hidden, **Show**); "This network has no password" checkbox | Cancel · Save | Name 1 to 32 bytes; password 8 to 63 characters unless "no password" is ticked; the server's message appears under the field. On a Pi 3B (`wifi_5ghz` false) it also says "This camera can only join 2.4 GHz networks." |
| **Change camera Wi-Fi** | Name; password (8 to 63) | Cancel · Save | "Phones that joined before will need the new password." |
| **Update** | — | Cancel · Update | "The camera downloads the update, installs it and restarts. This takes about 5 to 10 minutes. Keep it charging, or above 30%. The camera's buttons pause until it's done; pressing power turns it off once the update finishes. This page stops working while it restarts." |
| **Developer mode** | — | Cancel · Turn on | "The camera will join your home Wi-Fi every time it starts, so you can reach it over SSH. This uses more battery." |
| **Forget network** | — | Cancel · Forget | "Forget "Neyra Home"? The camera won't join it for sharing any more." |

All dialogs are `<dialog>` elements, closed by Esc or the backdrop (except while saving), with focus returned to the button that opened them.

#### 11.4.8 Banners

Shown at the top of the gallery and settings, from `/api/status`. Errors come first. At most two show; any more collapse into "1 more" that expands.

| Condition | Style | Text | Action |
|---|---|---|---|
| Storage full | Error | "Storage full. The camera can't take photos until you delete some." | **Select photos** |
| Storage low | Warning | "Storage low: about 240 photos left." | |
| Battery low | Warning | "Battery low (14%). Charge the camera soon." | |
| Clock unset | Warning | "The camera's clock isn't set." | **Use this phone's time** |
| Camera error | Error | "Camera problem: <detail>. Try turning the camera off and on again." | |
| Display error | Error | "The display module isn't responding. Take the print off and put it back on." | |
| Setup check failed | Error | "Setup check found a problem: <item>." | **See details** (Settings → Advanced → Setup check) |
| Update running | Info | "Updating the camera…" | |
| Update failed | Error | "The update didn't finish: <reason>. The camera is still on version <current>." | **Try again** (at home) |

Warnings use `role="status"` and errors `role="alert"`; each has an icon as well as colour.

#### 11.4.9 Whole-page states

| State | When | What it shows |
|---|---|---|
| **Can't reach the camera** | Two status polls in a row fail (about 20 s), or any action fails to connect | A card over the page: "Can't reach the camera" / "It may have turned off, or sharing stopped. Hold the shutter button on the camera for 2 seconds to start again, then reopen this page." / **Try again**. It keeps retrying every 10 s and disappears by itself when the camera answers |
| **Sharing is off** | After **Stop sharing** | "Sharing is off. You can close this page." |
| **Updating** | An update is running | Steps (Downloading → Installing → Restarting) from `/api/status`, then "The camera is restarting. Turn sharing on again to reconnect." If it fails, the page goes back to normal with the "Update failed" banner |

#### 11.4.10 Toasts

Short messages at the bottom, above the action bar. They last 3 s and sit in a live region. There's no Undo: deletes are confirmed up front instead.

"Photo deleted" · "3 photos deleted" · "Developing on the camera, about 20 seconds" · "Queued: the camera will show it after the current photo" · "Download started. Keep the camera on until it finishes." · "Saved" · "Couldn't save: <reason>" · "Time set" · "Network added" · "Sharing stopped".

### 11.5 Behaviour

#### 11.5.1 Keeping up to date

- **Status poll:** `/api/status` every 10 s while the page is visible, and at once when it becomes visible again (`visibilitychange`). Polls carry `X-Inkstant-Poll: 1`.
- **Polls don't count as activity**, so a tab left open on a laptop doesn't keep the camera sharing forever on battery. Taps, page loads, thumbnails and photos do count. 📐
- **What a poll updates:** banners, badges (on camera, developing), the battery, and the "new photo" pill (from `newest_seq`).
- **Several phones at once:** a change made on one phone appears on the others at their next poll. Deleted photos drop out of the grid the next time it fetches a page. The viewer handles a photo vanishing (§11.4.2).

#### 11.5.2 Share timeout and downloads

- **Downloads count as activity.** While a ZIP is streaming, the gallery reports activity every 30 s, so share mode never times out in the middle of a long download. 📐 (§9.2)
- **A single photo** downloads in seconds, so it needs nothing special.

#### 11.5.3 Times and units

- **Times** come from the photo ID, which is the camera's local time, and are shown as is. They're formatted in the phone's language with `Intl.DateTimeFormat`.
- **"Today" and "Yesterday"** use the camera's date from `/api/status`, so they don't shift when the phone is in another time zone.
- **Sizes** in decimal MB and GB (1 MB = 1,000,000 bytes), as iOS and Android show them.
- **Durations** rounded up: "about 30 seconds", "about 4 minutes".

#### 11.5.4 Errors

Every failed action shows the server's reason in plain words (from the `error.message` of §12), never a status code. Actions that can't work right now are greyed out with the reason, rather than failing after the tap.

### 11.6 Accessibility

- **Contrast:** every text colour pair is 4.5:1 or better in both themes (§11.3.2). The focus ring is 2 px of accent, 2 px outside the element, shown with `:focus-visible`.
- **Targets:** at least 44 × 44 px (Apple's guideline); WCAG 2.2 AA asks for 24 × 24. Small glyphs get padding to reach that. ✅ [Deque on iOS targets](https://dequeuniversity.com/rules/attest-ios/1.0/touch-target-size), [Adrian Roselli on WCAG target size](https://adrianroselli.com/2019/06/target-size-and-2-5-5.html)
- **Zoom:** pinch-zoom is never disabled (no `maximum-scale`). Sizes are in rem, so the browser's text-size setting scales the interface.
- **Screen readers:**
  - Tiles are buttons labelled like "Photo, Thursday 15 October, 14:30, on camera"; in select mode they use `aria-pressed`.
  - The viewer announces "Photo 3 of 1,284".
  - Dialogs have labelled titles; banners and toasts are live regions.
- **Not colour alone:** badges, banners and errors all carry an icon or text as well as colour.
- **Keyboard (computer):** everything works by keyboard. ← → move in the viewer, Esc closes the viewer, sheets and dialogs, and Tab order follows the layout.
- **Motion:** respects `prefers-reduced-motion`.

### 11.7 Performance

📐 Budgets, checked in M6 on a real phone over the camera's Wi-Fi:

| Item | Budget |
|---|---|
| HTML (with the first 60 tiles) | ≤ 20 KB |
| CSS | ≤ 15 KB |
| JS (all modules) | ≤ 30 KB |
| Thumbnail (480 × 270, quality 80) | 20 to 40 KB each |
| First screen (shell + about 20 visible thumbnails) | about 0.7 MB, under 1 s on the camera's Wi-Fi |
| Requests per gallery page load | 3 + one per visible thumbnail |

- **Caching:** thumbnails and photos are `immutable` for a year (§10.1), so going back to the gallery doesn't download them again. Prints are `no-cache`, since a palette change remakes them. Static files are `no-cache` with an ETag: the ES modules import each other by plain URL, so after an update each one revalidates with a cheap 304 instead of staying stale for a year.
- **Layout stability:** tiles have a fixed aspect ratio, and images get `width` and `height`, so nothing jumps while they load.
- **Large libraries:** each day group gets `content-visibility: auto` where supported (Safari 18+, Chrome 85+). 🧪 Scroll 3,000+ photos on an older iPhone.
- **No compression on the wire:** Waitress doesn't compress, and nothing large is text. JPEGs wouldn't shrink anyway.

### 11.8 How it's built

- **No build step, no framework.**
  - Jinja templates: `base.html`, `gallery.html`, `photo.html` (direct link), `settings.html` and `error.html`, plus the shared pieces `viewer.html`, `dialogs.html` and `icons.html` (the inline SVG sprite).
  - One stylesheet: `static/app.css`, with the tokens from §11.3.
  - ES modules: `common.js` (API calls, status poll, banners, toasts, dialogs), `gallery.js` (grid, scrolling, select mode, download sheet), `viewer.js` (the photo viewer), `photo.js` (the viewer on its own, for `/p/<id>`), `settings.js`, and `strings.js` (every message in one place, ready for translation later).
- **Progressive:** the gallery's first page and every photo page work without JavaScript (tiles are links to `/p/<id>`). Selecting, infinite scrolling and settings need it.
- **Security headers** on every response (§10.5):
  - `Content-Security-Policy: default-src 'self'; img-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`. So there are no inline scripts or styles anywhere.
  - `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
- **Requests that change something** send `X-Inkstant: 1` and JSON (§10.5).
- **Device detection** (iPhone, Android or computer) only picks the download instructions; nothing else depends on it.

### 11.9 Words

Rules for every string:

- Name things the way a person would: "photos", "display module" or "print", "shutter button" (BTN, since 2026-10-17; never "share button"), "camera Wi-Fi", "home network". Never `ID`, `seq`, `hotspot`, `epd` or `share mode`.
- Buttons say exactly what happens ("Delete", "Download ZIP", "Use this phone's time"), and the toast confirms it in the past tense ("Photo deleted").
- Errors say what went wrong and what to do, with no apology and no codes.
- Sentence case everywhere. Numbers as digits, with thousands separators in the phone's style.

Every message lives in `strings.js` (and in the templates for server-rendered text). The full list is in the sections above: §11.4.1 to §11.4.10.

## 12. Control socket

`/run/inkstant/control.sock`, a Unix stream socket, `root:inkstant-web`, mode 0660. The camera app creates it at start (`RuntimeDirectory=inkstant`). 📐

- **Framing:** one JSON object per line.
  - Request: `{"id": 7, "cmd": "photos.delete", "args": {"ids": ["000123_20261015-143022"]}}`
  - Reply: `{"id": 7, "ok": true, "result": {...}}`, or `{"id": 7, "ok": false, "error": {"code": "invalid", "message": "…"}}`
- **Who's calling:** `SO_PEERCRED` gives the caller's user. uid 0 may use every command; `inkstant-web` only those marked "gallery"; anyone else is refused.
- **Limits:** 64 KB per message; 5 s per request, except the commands that wait on Wi-Fi or the hardware: `share.start` 75 s, `selftest.run` 30 s, `update.check` 15 s and `update.start` 30 s.

| Command | Args | Result | Gallery | Notes |
|---|---|---|---|---|
| `status` | — | `/api/status` payload | ✓ | |
| `photos.delete` | `ids`, or `all: true` | `deleted`, `missing` | ✓ | Removes the JPEG, thumbnail, print and `.epd`; clears `undeveloped` and `current` if they match |
| `photos.show` | `id` | `queued` | ✓ | Errors: `no_module`, `not_ready` (not `ACTIVE`), `not_found` |
| `settings.get` | — | settings | ✓ | |
| `settings.set` | partial settings | settings, `applied` | ✓ | Checked against §5.4; hotspot changes rewrite its profile and apply at the next share. Setting `battery_saver.enabled` to false also turns off an automatic saver (§8.9). Timers, the saver and the frame rate are re-applied at once |
| `time.set` | `epoch_ms`, `tz` | — | ✓ | §6.7 |
| `wifi.list` | — | home names, hotspot name | ✓ | |
| `wifi.home.set` | `ssid`, `password` | — | ✓ | Writes a keyfile (§6.8) |
| `wifi.home.remove` | `n` | — | ✓ | |
| `share.stop` | — | — | ✓ | |
| `share.activity` | — | — | ✓ | |
| `update.check` | — | `current`, `latest` | ✓ | GitHub API, only while sharing at home |
| `update.start` | `version` | — | ✓ | §16.1. Refused unless sharing at home and charging or above 30% |
| `update.report` | `state`, `step`, `message` | — | | From `update.sh` (§16.1): sets `update_running` and the step shown in the gallery |
| `selftest.get` / `selftest.run` | — | Results per check | ✓ | §17; the setup check in §11.4.6 |
| `power.button` | — | `action` (`turn_on`, `shutdown`, `deferred`, `ignored`) | | From `inkstant-ctl power-button`; `deferred` during a software update (§8.2) |
| `shutdown` | `reason`, `reboot` | — | | §8.13 |
| `share.start` | `force_hotspot` | — | | Developer tool |
| `dev.set` | `enabled` | — | | §16.2 |
| `regen_epd` | — | `queued` | | §7.7 |

## 13. System configuration

Everything here is written by `install/configure.py`. It is idempotent: it can be re-run, and it backs up any file it changes (`<file>.inkstant-bak`, first time only).

### 13.1 config.txt

`/boot/firmware/config.txt`:

- **Stock lines that conflict are commented out**, by prefixing `#inkstant: `: `camera_auto_detect=1`, `display_auto_detect=1`, `dtoverlay=vc4-kms-v3d`, `max_framebuffers=2` and `dtparam=audio=on`. Duplicating them instead could load `vc4-kms-v3d` twice with different settings.
- **One managed block is added** at the end:

```ini
# BEGIN inkstant (managed by install/configure.py; edits here are overwritten)
[all]
dtparam=i2c_arm=on
dtparam=spi=on
dtparam=audio=off
camera_auto_detect=0
display_auto_detect=0
dtoverlay=vc4-kms-v3d,nohdmi,cma-128
max_framebuffers=1
disable_splash=1
force_eeprom_read=0
disable_poe_fan=1
ignore_lcd=1
disable_touchscreen=1
disable_camera_led=1
dtparam=act_led_trigger=timer
dtparam=pwr_led_trigger=none
dtoverlay=disable-bt
[pi3]
dtoverlay=imx708
dtoverlay=i2c-rtc,ds3231
[pi4]
dtoverlay=imx708
dtoverlay=i2c-rtc,ds3231
[pi5]
# configure.py writes dtoverlay=imx708,cam0 when device.toml says camera_port = "cam0"
dtoverlay=imx708
[all]
# END inkstant
```

- **Why each line** (goal, decided 2026-10-14: ready to shoot as fast as possible, with the least drain). ✅ [KittenLabs](https://kittenlabs.de/blog/2024/09/01/extreme-pi-boot-optimization/), [overlay docs](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm/boot/dts/overlays/README)
  - **HDMI off:** `nohdmi` and `max_framebuffers=1`. No screen is ever attached; the camera still needs the GPU. KittenLabs measured 136.7 → 122.6 mA on a Zero 2 W with HDMI off.
  - **CMA:** `cma-128` (§3, finding 9).
  - **Skip probing:** `camera_auto_detect=0`, `display_auto_detect=0`, `force_eeprom_read=0`, `ignore_lcd=1`, `disable_touchscreen=1` and `disable_poe_fan=1`. The camera is named directly with `dtoverlay=imx708`. `force_eeprom_read=0` also stops the firmware reading the module's ID chip, whose pin is the module detect (§3 finding 24).
  - **Onboard LEDs:** the red power LED and the camera LED off (`pwr_led_trigger=none`, `disable_camera_led=1`). The green ACT LED is the status light, blinking from early kernel boot (`act_led_trigger=timer`, §6.2; decided 2026-10-17, replacing `act_led_trigger=none` and a `gpio-led` overlay for an LED on the header).
  - **Bluetooth off:** `dtoverlay=disable-bt`.
  - **Audio off:** nothing plays sound.
  - **Buses:** I2C for the PiSugar and SPI for the display.
  - **Status light:** §6.2.
  - **RTC overlay:** §6.7.
- **One line, every model:** the firmware maps `vc4-kms-v3d` and `disable-bt` to their Pi 4 and Pi 5 versions, which take the same options. ✅ [overlay map](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm/boot/dts/overlays/overlay_map.dts) The model sections hold only what really differs (§15.1). ✅ [overlay README](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm/boot/dts/overlays/README)
- **The power LED:** ✅ bench Pi 4 (2026-10-17): the red PWR LED is off (trigger `none`, brightness 0). Some boards may also need `pwr_led_activelow` to stay dark. 🧪 per model.
- **To measure in M8** before keeping them: 🧪
  - Removing `auto_initramfs=1`, which took KittenLabs from 4.75 to 4.47 s.
  - KittenLabs' HDMI probe skips: `hdmi_ignore_edid=0xa5000080`, `hdmi_ignore_cec_init=1`, `hdmi_blanking=2`.
  - `disable_fw_kms_setup=1`: the firmware then stops passing a display mode to the kernel. It's moot with `nohdmi`, so it's kept only if it saves time. ✅ [KittenLabs](https://kittenlabs.de/blog/2024/09/01/extreme-pi-boot-optimization/)

### 13.2 cmdline.txt

- **Add `quiet`** (🧪 measured in M8). With Bluetooth disabled, the full UART is the serial console, and printing every kernel message at 115200 baud slows boot. 📐
- Nothing else changes; `fsck.repair=yes` stays.

### 13.3 systemd units

**`/etc/systemd/system/inkstant.service`**

```ini
# Inkstant camera app. Installed by install/configure.py.
[Unit]
Description=Inkstant camera
Wants=pisugar-server.service
After=pisugar-server.service
StartLimitIntervalSec=120
StartLimitBurst=5
OnFailure=inkstant-failsafe.service

[Service]
Type=notify
NotifyAccess=main
WorkingDirectory=/opt/inkstant/current
Environment=PYTHONPATH=/opt/inkstant/current/src
Environment=GPIOZERO_PIN_FACTORY=lgpio
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 -m inkstant camera
RuntimeDirectory=inkstant
RuntimeDirectoryMode=0755
Restart=on-failure
RestartSec=2
WatchdogSec=30
TimeoutStartSec=60
TimeoutStopSec=75
KillMode=mixed
OOMScoreAdjust=-800

[Install]
WantedBy=multi-user.target
```

- **`READY=1`** is sent once the state machine is running (before the camera is ready), so the slow camera start never trips `TimeoutStartSec`.
- **`OnFailure`** runs only when restarts are exhausted (5 crashes within 2 minutes). Then `inkstant-failsafe.service` (`Type=oneshot`, `ExecStart=/usr/local/bin/inkstant-ctl failsafe`) sends `rtc_alarm_disable` and powers off. A crash-looping camera switches itself off instead of draining the battery. 📐

**`/etc/systemd/system/inkstant-gallery.service`** (not enabled; started by the camera app)

```ini
# Inkstant gallery. Not enabled: the camera app starts it in share mode.
[Unit]
Description=Inkstant gallery
After=inkstant.service

[Service]
Type=exec
User=inkstant-web
Group=inkstant-web
SupplementaryGroups=systemd-journal
WorkingDirectory=/opt/inkstant/current
Environment=PYTHONPATH=/opt/inkstant/current/src
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 -m inkstant gallery 0.0.0.0:80
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictNamespaces=yes
LockPersonality=yes
MemoryMax=96M
OOMScoreAdjust=500
Restart=on-failure
RestartSec=1
TimeoutStopSec=5
```

`ProtectSystem=strict` makes the filesystem read-only for it. Connecting to the control socket still works, because Linux's read-only-mount check doesn't apply to sockets. 📐 🧪

### 13.4 Shutdown hook

When the Pi shuts down, the PiSugar 3 doesn't cut its 5 V output by itself. PiSugar's `pisugar-poweroff` tool does nothing on a PiSugar 3. ✅ [pisugar3.rs](https://github.com/PiSugar/pisugar-power-manager-rs/blob/master/pisugar-core/src/pisugar3.rs) So the shutdown arms the PiSugar's delayed power-off at the very last moment, from a systemd shutdown hook (decided 2026-10-15).

- **When it runs:** systemd runs every program in `/usr/lib/systemd/system-shutdown/` just before the final power-off, "after all services have been shut down, and after most mounts have been unmounted". It passes `poweroff`, `halt`, `reboot` or `kexec` and waits for them, with a 90 s safety timeout. ✅ [systemd-shutdown](https://manpages.debian.org/trixie/systemd/systemd-shutdown.8.en.html)
- **Firmware:** the delay needs PiSugar firmware 1.0.6 or later, and the write unlock needs 1.2.4 or later. ✅ [I2C docs](https://docs.pisugar.com/docs/product-wiki/battery/pisugar3/pisugar-3-i2c) The install script requires 1.2.4 (§15.6, step 6). I found no published self-update procedure, so an older board means contacting PiSugar support.
- **After the cut:** auto-hibernate (0x03 bit 6) is on by default after each restart. Once the output is off, the PiSugar hibernates. I2C and the custom button stop, but the power button, charger detection, RTC and timed power-on keep working. ✅ [I2C docs](https://docs.pisugar.com/docs/product-wiki/battery/pisugar3/pisugar-3-i2c)
- **Standby current:** PiSugar gives 1 mA with the output off and not hibernating, but no figure for hibernate. 🧪 Measure.

**`/usr/lib/systemd/system-shutdown/inkstant-poweroff`** (mode 0755). PiSugar's own delayed-shutdown steps, with the watchdog turned off first:

```sh
#!/bin/sh
# Inkstant. Runs after all services have stopped, just before the kernel
# powers off. Arms the PiSugar 3's delayed power cut, so the Pi then draws nothing.
# Does nothing on reboot, halt or kexec.
[ "$1" = "poweroff" ] || exit 0
B=1 A=0x57 DELAY=10
command -v i2cset >/dev/null 2>&1 || exit 0
i2cset -y $B $A 0x0B 0x29 || exit 0                              # unlock writes
v=$(i2cget -y $B $A 0x06) && i2cset -y $B $A 0x06 $((v & 0x7F))  # software watchdog off
i2cset -y $B $A 0x09 $DELAY                                      # delay in s (write before 0x02)
v=$(i2cget -y $B $A 0x02) && i2cset -y $B $A 0x02 $((v & 0xDF))  # clear bit 5: start the countdown
i2cset -y $B $A 0x0B 0x00                                        # lock writes
echo "inkstant: PiSugar power cut armed ($DELAY s)" > /dev/kmsg 2>/dev/null
exit 0
```

- By this point the root filesystem is read-only and pisugar-server has stopped, so nothing else is on the bus. ✅ [systemd-shutdown](https://manpages.debian.org/trixie/systemd/systemd-shutdown.8.en.html), [PiSugar script](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.5/scripts/PiSugar3_DelayedShutdown.sh)
- `dash` handles hex values in `$(( ))`. 🧪 Bench: about 10 s after `poweroff` the 5 V output drops; after `reboot` it doesn't.
- **The delay:** 10 s to start with. A halted Pi still draws current until the cut (100 mA on a 3B ✅ [Pi power docs](https://github.com/raspberrypi/documentation/blob/develop/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc)), so M8 measures the time from this hook to a halted Pi. The delay then becomes that time plus a margin for the PiSugar's inaccurate timer, probably 5 s. 🧪

### 13.5 pisugar-server

- **Version:** pinned to 2.3.5. The arm64 `.deb` comes from the GitHub release and is checked against `install/pisugar-server.sha256`. The `latest` tag is a nightly pre-release, so it's never used. ✅ [releases](https://github.com/PiSugar/pisugar-power-manager-rs/releases)
- **Preseeded answers** (`debconf-set-selections`, then `DEBIAN_FRONTEND=noninteractive dpkg -i`):
  ```text
  pisugar-server pisugar-server/model select PiSugar 3
  pisugar-server pisugar-server/address select 127.0.0.1
  pisugar-server pisugar-server/web boolean false
  pisugar-server pisugar-server/tcp boolean false
  pisugar-server pisugar-server/uds boolean true
  ```
  `jq` isn't installed, so the post-install script skips its web-login prompt. ✅
- **Drop-in** `/etc/systemd/system/pisugar-server.service.d/inkstant.conf`:
  ```ini
  [Service]
  ExecStart=
  ExecStart=/usr/bin/pisugar-server $OPTS --uds-mode 600
  ```
- **`pisugar-poweroff` is not installed:** it does nothing on a PiSugar 3 (§13.4). ✅
- **PiSugar settings** (§6.5) are applied over the socket and re-checked at every boot.

### 13.6 Other system settings

| File or action | Content | Why |
|---|---|---|
| `/etc/systemd/journald.conf.d/inkstant.conf` | `[Journal]` `Storage=persistent`, `SystemMaxUse=20M` | Logs survive restarts without filling the card |
| `/etc/systemd/system.conf.d/inkstant.conf` | `[Manager]` `DefaultTimeoutStopSec=15s` | Keeps shutdown well inside the watchdog and PiSugar timings 📐 |
| `/etc/modules-load.d/inkstant.conf` | `ledtrig-pattern` | §6.2 |
| `/etc/NetworkManager/dnsmasq-shared.d/inkstant.conf` | `no-resolv` | §9.4 |
| User | `useradd --system --user-group --no-create-home --shell /usr/sbin/nologin inkstant-web` | §10 |
| `/etc/cloud/cloud-init.disabled` | Empty file | Imager's first-boot setup runs through cloud-init on Trixie ✅ [Raspberry Pi newsletter](https://newsletters.raspberrypi.com/w/togQuZwIMG8etq5K677630dQ), and it's finished by now (§15.6, step 2). This also stops cloud-init from rewriting the network config |
| Disable | `apt-daily.timer`, `apt-daily-upgrade.timer`, `man-db.timer`, `e2scrub_all.timer`, `dpkg-db-backup.timer`, `fstrim.timer` | Persistent timers catch up right after boot when they were missed while off, which slows every boot and wake 📐 |
| Disable | `NetworkManager-wait-online.service`, `systemd-timesyncd.service`, `bluetooth.service`, `hciuart.service` and `ModemManager.service` if present, `rpi-eeprom-update.service` (Pi 4/5) | Never wait for the network; Bluetooth is off; no internet time (§6.7); EEPROM updates by hand |
| Keep | `NetworkManager`, `avahi-daemon` (`<hostname>.local`), `ssh` (as set up in Imager), `cron`, `logrotate.timer` | |
| USB hub off (3B and 3B+ only, not in developer mode) | The camera app, at start: `echo 1-1 > /sys/bus/usb/drivers/usb/unbind` (the on-board USB hub and Ethernet chip) | Up to ~100 mA by secondary reports, and the camera uses neither USB nor Ethernet. 📐 🧪 Keep it only if M8 measures a real saving with nothing else affected; the 3A+, 4B and 5 have no such chip to switch off this way. [Pi-Supply](https://learn.pi-supply.com/make/how-to-save-power-on-your-raspberry-pi) |
| Measure in M8 | `systemd-analyze critical-chain inkstant.service` and `systemd-analyze blame`; trim further only with numbers | §20 |

## 14. Power and battery life

Goal: the longest battery life that costs nothing in speed or features, except where the user chose otherwise: sleep, and the battery saver (decided 2026-10-14, updated 2026-10-15). Every figure here is an estimate until measured in M1 and M8 (§14.5).

### 14.1 Budget

- **Battery:** 5000 mAh × 3.7 V ≈ 18.5 Wh. Assuming about 85% boost efficiency, about 16 Wh usable, or about 3.2 Ah at 5 V. 📐
- **The Pi:** Raspberry Pi gives 350 mA as the Pi 3A+'s typical bare-board active current. ✅ [Pi power docs](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#power-supply) raspi.tv measured 240 to 350 mA with an HDMI screen and keyboard dongle attached. ✅ [raspi.tv](https://raspi.tv/2018/how-much-power-does-raspberry-pi-3a-plus-use)
- **The camera:** Raspberry Pi gives 250 mA as a Camera Module's typical draw. ✅ [Pi power docs](https://github.com/raspberrypi/documentation/blob/develop/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc) It's the largest single draw while the camera is on.
- **The display module:** under 100 mW while refreshing, and nothing between refreshes (PWR held low). ✅ [Waveshare wiki](https://www.waveshare.com/wiki/3.6inch_e-Paper_HAT%2B_%28E%29_Manual)

| State (Pi 3A+) | Draw at 5 V 📐 | On-time from full 📐 |
|---|---|---|
| Ready, camera streaming | ~600 mA | ~5 h |
| Asleep: camera stopped, Pi on | ~350 mA or less (bare board) | ~9 h |
| Developing | Ready + under 20 mA for the panel | — |
| Sharing | + Wi-Fi 🧪 | — |
| Halted, until the PiSugar's cut | ~100 mA (Raspberry Pi's figure for a halted 3B ✅) | — |
| Off | The PiSugar's own standby only; its "more than a year" clock implies at most about 0.57 mA on average (§6.7) | Months |

**Other boards** (typical bare-board active current ✅; on-time from 16 Wh before the camera and Wi-Fi 📐):

| Model | Current | On-time |
|---|---|---|
| Pi 3A+ | 350 mA | ~9 h |
| Pi 3B | 400 mA | ~8 h |
| Pi 3B+ | 500 mA | ~6.5 h |
| Pi 4B | 600 mA | ~5.5 h |
| Pi 5 | 800 mA | ~4 h |

### 14.2 Slideshow power

Wake, show the next photo, power off. The PiSugar's timed power-on (registers 0x40 to 0x47; `rtc_alarm_set <time> <weekday mask>`) turns its output on at a time of day, so the app sets the next one before each shutdown. ✅ [I2C docs](https://docs.pisugar.com/docs/product-wiki/battery/pisugar3/pisugar-3-i2c)

- **Each change:** boot, 15 s refresh, then shutdown and the power-off delay. That's about 40 to 60 s at 1.2 to 1.8 W, or 0.015 to 0.03 Wh.
- **So** roughly 500 to 1,200 changes per charge, before standby drain: hourly lasts about 3 to 7 weeks, every 15 minutes about 5 to 12 days. 📐
- **Default:** every hour (5 min to 24 h).

### 14.3 Battery-saving measures

Each measure is specified in the section listed; the savings are estimates until measured.

| Measure | Saves (estimate) | Costs | Status | § |
|---|---|---|---|---|
| The Pi fully unpowered when off (PiSugar delayed power cut, then hibernate) | Everything but the PiSugar's standby | Nothing | ✅ decided | 13.4 |
| **Sleep** after 5 idle minutes (setting) | Most of the camera's ~250 mA while idle | About 1 s on the first shot after it | Decided 2026-10-15 | 8.8 |
| Turn off after 10 idle minutes (setting) | All on-time after that | A full start (§1.5) for the next shot | Decided | 8.7 |
| **Battery saver**, by hand or by itself at 20% | Shorter sleep and turn-off times, the lowest frame rate, sharing stops sooner | About 0.5 s slower refocusing (none while the normal rate is also 5 fps); more first shots wait for a wake | Decided 2026-10-15 | 8.9 |
| Slideshow wakes skip the camera, Wi-Fi and heavy imports, with a 120 s cap | Seconds and current per wake | Nothing | 📐 | 8.11 |
| Fast start: no missed-timer catch-up, unneeded services off, `quiet`, fewer firmware probes | Seconds per start and wake | Nothing | 📐 🧪 M8 | 13 |
| HDMI off (`nohdmi`) | ~14 mA (KittenLabs, Zero 2 W) to ~25 mA | Nothing (no screen attached) | ✅ | 13.1 |
| The red power LED, the camera LED, Bluetooth and audio off (the green ACT LED is the status light) | ~2 to 5 mA per LED; a few mA more | Nothing | ✅ / 📐 | 13.1 |
| Wi-Fi radio off except while sharing | ~10 mA or more | Nothing (sharing turns it on) | ✅ / 📐 | 9 |
| Share mode stops after 10 min without use on battery; status polls don't count; downloads keep it alive | Wi-Fi time | Nothing | Decided | 9.2 |
| CPU frequency scaling: the kernel's default `ondemand` governor idles at the lowest clock and speeds up at once under load | Idle current | Nothing | ✅ [kernel config](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm64/configs/bcm2711_defconfig) | Kept as is |
| Event-driven app: BUSY and module detect by edge, 10 s timers. The one polling loop is the BTN reader, 50 I2C reads a second, since the PiSugar has no line to the Pi but I2C and power ✅ [PiSugar 3 docs](https://docs.pisugar.com/docs/product-wiki/battery/pisugar3/pisugar-3-series) (§6.6) | CPU wake-ups | 🧪 The BTN reader's current | 📐 | 4.5 |
| Display module switched off between updates (PWR low) | The module draws almost nothing | Nothing | ✅ | 6.3 |
| Low battery stops the slideshow; quiet hours skip wakes overnight | Wakes | Nothing | Decided | 8.12 |
| USB hub and Ethernet chip off on the 3B and 3B+ (the 3A+ has none), except in developer mode | Up to ~100 mA (unmeasured) | Their USB and Ethernet ports stop working; the camera uses neither | 📐 🧪 | 13.6 |
| Shortest safe PiSugar power-cut delay (10 s now). A halted Pi 3B still draws 100 mA until the cut ✅ | About 0.5 J per power-off | Nothing | 🧪 | 13.4 |
| Camera tuned for the lowest current that keeps it responsive: 5, 10 or 14 fps, and continuous autofocus against focus that rests once settled | Part of the camera's ~250 mA | Nothing, as long as the shutter delay stays at or under 0.5 s and refocusing within 1 s | 🧪 | 6.4 |
| A slideshow wake saves state and arms the next wake while the panel finishes powering down | 1 to 2 s per wake | Nothing | 📐 | 8.11 |
| Charge limit about 80% (setting, off by default) | Battery lifespan: PiSugar says about 8 times the charge cycles ✅ [I2C docs](https://docs.pisugar.com/docs/product-wiki/battery/pisugar3/pisugar-3-i2c) | About 20% less runtime per charge | Setting | 1.8 |

### 14.4 Savings not adopted

Each would save current only while the camera is on. The cost is in seconds, or in photos that come out wrong if you shoot before the camera has caught up. 📐 estimates from the libcamera and driver code (§3, findings 20 to 22); 🧪 none measured.

| Saving | Saves | Added delay | Other cost | Status |
|---|---|---|---|---|
| **Camera standby:** stop the stream when idle; restart it at the next press | Most of the camera's ~250 mA while idle | About 1 s. Restart and first frame take about 0.3 to 0.5 s; the sensor is powered down 5 s after the stream stops ✅. Then focus waits for its frame checks, and exposure and colour carry on at their tuned pace | If the press didn't wait for focus, the photo would be quick but could be blurred | **Adopted as sleep** after 5 min (2026-10-15), waiting for focus |
| **Lower frame rate:** 2 fps instead of 5 | Unknown: part of the camera's draw | Up to 0.3 s more shutter delay, 0.15 s on average. Refocusing takes about 2.5 s instead of 1 s | Exposure and colour settle 2.5 times slower too | Not adopted. The battery saver uses 5 fps, the slowest rate tested |
| **Idle step-down:** drop to 1 or 2 fps after 30 s without a press; back to the normal rate at the press | Unknown | Up to 0.5 to 1 s more on the first shot after a pause, the most common shot | Focus tracks slowly while idle, so that first shot is the likeliest to be soft | Not adopted; sleep does the same job better |
| **Small stream, switch to full resolution at each press** | The ISP and memory traffic for 3 MP frames instead of 12 MP | About 0.3 to 1 s per photo: stop, reconfigure, restart and wait for a full-resolution frame, then switch back. No measurement found for this camera; Arducam's 64 MP camera took about 3 s on a Pi 5 ✅ ([forum](https://forum.arducam.com/t/64mp-still-capture-with-preview-on-pi-5-why-so-much-delay-when-using-switch-mode/6770)) | About 20 MB more camera memory to keep both set-ups allocated; otherwise every switch reallocates about 69 MB, which Picamera2 warns can fragment memory ✅ [picamera2.py](https://github.com/raspberrypi/picamera2/blob/v0.3.38/picamera2/picamera2.py) | Not adopted |
| **Shoot 3 MP (binned) all the time** | The same as above | None (readout is 17 ms instead of 68 ms ✅) | Photos are 3 MP instead of 12 MP: a feature cost | Not adopted |

### 14.5 Measuring

In M1 and M8 (§20):

- **Pi current in each state:** starting, ready, asleep, capturing, developing, sharing on the camera's Wi-Fi, halted, and off.
- **Each measure:** current with it on and off.
- **How:** power the Pi from a bench supply through a USB meter, in place of the PiSugar.
- **PiSugar standby:** its hibernate current, with a meter in the battery lead.
- The results replace the 📐 figures here, the on-time estimates and the slideshow estimate.

## 15. Setup script and supported models

How a builder turns a freshly flashed Raspberry Pi into an Inkstant camera with one script, and how the whole software stack works on every supported Pi model. The script installs the software in §4 to §13; §13 has the exact system files. Hardware assembly comes first: README.md, Step 1.

### 15.1 Supported models

Decided 2026-10-14: the **Pi 3A+** is the main target, and the **Pi 3B, 3B+, 4B and 5** are also supported. Since 2026-10-17 the build itself (the README's guide, HARDWARE.md and the case) is for the 3A+ only, while the software keeps supporting all five, for other people's cases and the bench Pi 4. One release, one script and one SD card image layout serve all five. The differences are handled in two places: config.txt sections written by the script, and model checks in the software at run time.

#### 15.1.1 Every layer, every model

| Layer | 3A+ | 3B | 3B+ | 4B | 5 | Source |
|---|---|---|---|---|---|---|
| **Pi OS Lite (64-bit), Trixie** | ✓ | ✓ | ✓ | ✓ | ✓ | ✅ Imager lists the image for the `pi3-64bit`, `pi4-64bit` and `pi5-64bit` device families ([Imager OS list](https://downloads.raspberrypi.com/os_list_imagingutility_v4.json)) |
| **Kernel** | Pi 3/4 kernel | same | same | same | Pi 5 kernel | Both builds include the IMX708 driver, the LED pattern trigger, the DS1307/DS3231 RTC driver, spidev and i2c-dev ✅ ([Pi 3/4 config](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm64/configs/bcm2711_defconfig), [Pi 5 config](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm64/configs/bcm2712_defconfig)) |
| **config.txt section** | `[pi3]` | `[pi3]` | `[pi3]` | `[pi4]` | `[pi5]` | ✅ `[pi3]` covers the 3B, 3B+ and 3A+, not the Zero 2 W ([conditional filters](https://github.com/raspberrypi/documentation/blob/develop/documentation/asciidoc/computers/config_txt/conditional.adoc)) |
| **CPU** | 4 × Cortex-A53, 1.4 GHz | 4 × A53, 1.2 GHz | 4 × A53, 1.4 GHz | 4 × A72, 1.5 to 1.8 GHz | 4 × A76, 2.4 GHz | Raspberry Pi product pages. Speed targets in §7.6 are set on the 3A+; the 3B runs about 15% slower 📐 |
| **RAM** | 512 MB | 1 GB | 1 GB | 1 to 8 GB | 1 to 16 GB | Raspberry Pi product pages |
| **Camera connector and cable** | 15-pin; standard cable | same | same | same | Two 22-pin ports; Standard–Mini cable; `dtoverlay=imx708` is CAM1, `imx708,cam0` is CAM0 | ✅ [camera docs](https://www.raspberrypi.com/documentation/accessories/camera.html), [overlay README](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm/boot/dts/overlays/README) |
| **Camera pipeline** | Unicam + ISP; buffers from CMA (about 69 MB) | same | same | same | PiSP; camera buffers not from CMA | ✅ §3 finding 10, Picamera2 manual. Picamera2 picks the pipeline itself; our configuration is the same on all |
| **CMA** | `cma-128` | same | same | same | same, used for the display only | ✅ `vc4-kms-v3d` is mapped to `vc4-kms-v3d-pi4` and `-pi5`, which take the same `cma-128` and `nohdmi` options ([overlay map](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm/boot/dts/overlays/overlay_map.dts)) |
| **`gpu_mem`** | 64 MB default | 76 MB default | 76 MB default | 76 MB default | Not used | ✅ [Pi docs](https://github.com/raspberrypi/documentation/blob/develop/documentation/asciidoc/computers/legacy_config_txt/memory.adoc) |
| **40-pin header, our GPIO numbers** | Same | same | same | same | Same (on the RP1 chip) | ✅ §6.1: the HAT's own pins, the same on every 40-pin Pi |
| **Module detect on ID_SD (GPIO0)** | 🧪 | 🧪 | 🧪 | ✅ bench (2026-10-17) | 🧪 | §3 finding 24 |
| **GPIO library** | gpiozero + lgpio on `gpiochip0` | same | same | same | gpiozero picks `gpiochip4` if it exists, else `gpiochip0` | ✅ [gpiozero lgpio.py](https://github.com/gpiozero/gpiozero/blob/master/gpiozero/pins/lgpio.py) |
| **SPI0 and I2C1** | `/dev/spidev0.0`, `/dev/i2c-1` | same | same | same | Same paths, on RP1 | ✅ |
| **Status light: the ACT LED, blinking from early kernel boot** | `act_led_trigger=timer` 🧪 | same, through the firmware's virtual GPIO 🧪 | same 🧪 | same 🧪 (its triggers ✅ bench, 2026-10-17) | same 🧪 | §3 finding 25, §6.2 |
| **Bluetooth off** | `dtoverlay=disable-bt` | same | same | same | Same line; the firmware maps it to `disable-bt-pi5` | ✅ [overlay map](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm/boot/dts/overlays/overlay_map.dts) |
| **Wi-Fi** | 2.4 + 5 GHz | 2.4 GHz only (BCM43438) | 2.4 + 5 GHz | 2.4 + 5 GHz | 2.4 + 5 GHz | ✅ [3B product page](https://www.raspberrypi.com/products/raspberry-pi-3-model-b/) names the chip. The hotspot is 2.4 GHz on every model; on a 3B, home networks must be 2.4 GHz |
| **Ethernet** | None | Yes | Yes | Yes | Yes | Not used by the camera; fine for running the install |
| **Clock at boot** | PiSugar RTC through the kernel (`i2c-rtc,ds3231`) | same | same | same | `rtc_rtc2pi`. The Pi 5's own RTC is `rtc0` and keeps time only with a backup battery, which we don't fit | ✅ [Pi 5 RTC docs](https://github.com/raspberrypi/documentation/blob/develop/documentation/asciidoc/computers/raspberry-pi/rtc.adoc), §6.7 |
| **PiSugar 3 Plus** | Listed (as "3A") | Listed | Listed | Listed | Listed; line the PiSugar's spring pins up carefully (the Pi 5's header holes are smaller) 🧪 | ✅ [PiSugar 3 docs](https://docs.pisugar.com/docs/product-wiki/battery/pisugar3/pisugar-3-series), [PiSugar #144](https://github.com/PiSugar/PiSugar/issues/144) |
| **Power from the PiSugar (3 A max)** | Typical 350 mA | 400 mA | 500 mA | 600 mA | 800 mA; with a 3 A supply, USB is limited to 600 mA (we use none) | ✅ [Pi power docs](https://github.com/raspberrypi/documentation/blob/develop/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc) |
| **Boot firmware** | On the SD card | same | same | EEPROM (`rpi-eeprom-update` disabled; update by hand) | EEPROM (same) | §13.6 |
| **Case** | The build's case (65 × 56 mm board) | None: 85 × 56 mm, different ports | same | same | same | HARDWARE.md (the case is for the 3A+ only; its Other boards section has pointers for the rest) |
| **On-time per charge** | ~9 h | ~8 h | ~6.5 h | ~5.5 h | ~4 h | 📐 §14.1, before the camera and Wi-Fi |

**Result:** no layer stops any of the five models. Four things need checking on real boards:

- the PiSugar 3 Plus fit and I2C on the Pi 5;
- the ACT LED and module detect on every board but the Pi 4;
- putting a module on and taking it off while the camera is on;
- full-resolution camera memory on the 512 MB 3A+.

All four are on the bench list (§19.6).

#### 15.1.2 What the software does differently

| Where | What changes by model | How |
|---|---|---|
| Install: config.txt | RTC overlay on Pi 3 and 4 only; `,cam0` on a Pi 5 whose camera is on CAM0 | `[pi3]`, `[pi4]` and `[pi5]` sections (§13.1) |
| Install: `device.toml` | `family` (`pi3`, `pi4`, `pi5`), `wifi_5ghz` (false on the 3B), `camera_port` (Pi 5) | From `/proc/device-tree/model`, and the camera-port question on a Pi 5 (§15.5) |
| Install: checks | `rpi_apps.yaml` must exist on Pi 3 and 4 (CMA) | §15.6, step 4 |
| Run time: clock | Kernel RTC on Pi 3 and 4, `rtc_rtc2pi` on Pi 5 | The app checks what `rtc0` is (§6.7) |
| Run time: camera | Logs `CmaFree` on Pi 3 and 4 only | Picamera2's platform check |
| Run time: GPIO | Chip number | gpiozero chooses |
| Gallery | On a 3B, "Add network" notes "This camera can only join 2.4 GHz networks" | `wifi_5ghz` in `/api/status` |

#### 15.1.3 Not supported

| Board | Why | What the script does |
|---|---|---|
| Pi Zero 2 W | Needs the PiSugar 3 for the Zero, the mini camera cable and a soldered header. The software itself would run | Stops with a message; `--force-model` lets experimenters continue |
| Pi 400, Pi 500, Pi 500+ | No camera connector | Stops |
| Compute Modules 3, 4, 5 | No standard board for the PiSugar and the module's header | Stops; `--force-model` to experiment |
| Pi 1, 2, Zero, Zero W | No 64-bit Pi OS image for them ✅ ([Imager OS list](https://downloads.raspberrypi.com/os_list_imagingutility_v4.json)) | Can't run the image |

### 15.2 Before running the script

1. **Hardware assembled:**
   - The PiSugar 3 Plus mounted and switched on.
   - The camera ribbon in.
   - Nothing to wire (decided 2026-10-17): BTN is the shutter, and the ACT LED is the light. A module pushed onto the header lets the setup check (§15.8) test it too (README, Step 1).
2. **Flash** Raspberry Pi OS Lite (64-bit) with Raspberry Pi Imager 2. In Imager's settings, set:
   - host name;
   - user name and password;
   - home Wi-Fi and Wi-Fi country;
   - time zone;
   - SSH, with a public key if you have one.

   Imager writes these as cloud-init files, which the Pi applies on its first boot. ✅ [Raspberry Pi news](https://www.raspberrypi.com/news/cloud-init-on-raspberry-pi-os/)
3. **First boot:** wait about two minutes for it to finish, then log in over SSH (`ssh <user>@<hostname>.local`).
4. **Internet** is needed for the whole install: Wi-Fi, or an Ethernet cable on the boards that have a port.
5. **Time:** about 15 to 25 minutes on a 3A+, mostly downloading and unpacking packages (roughly 200 MB). 📐 🧪

### 15.3 Running it

One command, over SSH (in `tmux` if you like, so a dropped SSH connection doesn't stop it; re-running is safe anyway, §15.9):

```bash
curl -fsSL https://raw.githubusercontent.com/EnriqueNeyra/inkstant-camera/main/setup.sh | sudo bash
```

Options go after `bash -s --`, for example `… | sudo bash -s -- --dev`. Current images ask for your password at `sudo`. ✅ [release notes](https://downloads.raspberrypi.com/raspios_lite_arm64/release_notes.txt)

**`setup.sh`** (repository root, bash) is a small bootstrap (decided 2026-10-16: one command to install):

1. Everything is inside `main()`, called on the last line, so bash has read the whole script before any of it runs (it arrives through a pipe).
2. Needs root. Run from a file, it re-runs itself with `sudo`; piped, it says to pipe it to `sudo bash`.
3. **From a checkout** (a file next to `install/install.sh`): runs that checkout's `install/install.sh` with the options.
4. **Otherwise it downloads:**
   - The latest release's `SHA256SUMS`, then `inkstant.tar.gz`, and checks the tarball against it. A mismatch stops before anything is installed.
   - **No release yet** (HTTP 404 for `SHA256SUMS`): the source archive of `main` (`/archive/refs/heads/main.tar.gz`), with a line saying so. A private repository also answers 404, so if `main` fails too, it asks whether the repository is public.
   - `--ref <branch or tag>`: that ref's source archive instead (`/archive/<ref>.tar.gz`). `--ref` isn't passed on.
   - No network (`curl` status 000): stops with "check the Wi-Fi and the time".
   - Unpacks into a temporary folder (one top folder in both kinds of archive, so `--strip-components=1`), runs its `install/install.sh` with the other options and stdin from `/dev/null` (questions go to `/dev/tty`, §15.5), removes the folder, and exits with the installer's status. The installer copies what it needs into `/opt/inkstant/releases/<version>`, writing `_build.py` for a source install (§16.1).
5. `INKSTANT_REPO` and `INKSTANT_GITHUB` override the repository and host (forks, and the tests in `tests/test_setup.py`, which serve a stand-in GitHub on localhost).

**Without a published release or network access to GitHub** (bring-up and development): `git clone` the repository on the Pi and run `sudo ./setup.sh`; or run `python3 tools/build_release.py` on a computer, `scp dist/inkstant.tar.gz` to the Pi, unpack it, and run `sudo ./inkstant/install/install.sh`. `--dev` keeps SSH working after the reboot (§16.2).

**Flags** (every question in §15.5 also has one):

| Flag | Effect |
|---|---|
| `--ref <branch or tag>` | (`setup.sh` only) Install that branch or tag instead of the latest release |
| `--yes` | Accept every default, and reboot at the end without asking |
| `--update` | Used by the updater: no questions, keep all existing answers, don't restart anything, reboot through the camera app (§16.1) |
| `--reconfigure` | Ask the questions again and apply the answers; skip package installs |
| `--check` | Report what would change and run the checks, but change nothing |
| `--from-source` | Install from this git checkout instead of a release tarball (for development) |
| `--force-model` | Continue on an unsupported board |
| `--no-reboot` | Finish without rebooting |
| `--dev` | Turn on developer mode (§16.2) |

### 15.4 How the script is built

- **Three parts:**
  - **`setup.sh`** (bash, repository root): the one-command bootstrap above.
  - **`install/install.sh`** (bash) does what needs root, the network and the package manager: checks, apt, pisugar-server.
  - **`install/configure.py`** (standard-library Python 3.13, which Pi OS Lite already has) does everything that writes files. Generating config files is safer in Python than with `sed`.
- **Fail loudly, not halfway:**
  - `set -Eeuo pipefail` and an `ERR` trap that prints the failing step, the last log lines, the log path, and "It's safe to run the script again."
  - Each step checks its own result before the next starts.
- **One copy at a time:** `flock /run/inkstant-install.lock`.
- **Log:** everything, with timestamps, goes to `/var/log/inkstant-install.log` as well as the screen. Secrets (Wi-Fi passwords) are never logged.
- **Converge, don't replay:**
  - Every step works out what the system should look like, compares it with what it is, and changes only the difference.
  - It prints `ok` (nothing to do) or `changed` with what it changed.
  - So a second run changes nothing, and an interrupted run picks up where it stopped.
- **Remembers what it did:** `/var/lib/inkstant/install-state.json` keeps:
  - the version installed and the time;
  - the answers given (not passwords: the hotspot password lives in `settings.json`, home passwords in NetworkManager);
  - the services it disabled, the stock config lines it commented out, and the backups it made.

  The uninstaller (§15.9) uses it to put things back.
- **`--root <dir>`** (in `configure.py`, for tests only) prefixes every path, so file generation can be tested against a fake system on any machine (§19.4).

### 15.5 Questions it asks

Asked together at the start, so the rest runs without you. Defaults are shown in brackets; Enter accepts. Questions and answers go through `/dev/tty`, never stdout, so the install log (which `tee` copies stdout into) never holds a password; the final summary with the camera Wi-Fi password goes to `/dev/tty` too.

| Question | Default | Flag | Checked |
|---|---|---|---|
| Camera Wi-Fi name | `Inkstant-` + the last 4 hex digits of the Pi's serial number | `--hotspot-ssid` | 1 to 32 bytes |
| Camera Wi-Fi password | A random 10 characters from an alphabet with no look-alikes (no 0/O, 1/l/I) | `--hotspot-password` | 8 to 63 printable ASCII characters |
| Home network: "Use "Neyra Home" for sharing at home?" | Yes, if Imager set one up | `--home-ssid`, `--home-password`, `--no-home` | 1 to 32 bytes; password 8 to 63, or none |
| Home network, when none was found (installed over Ethernet) | Skip (sharing then always uses the camera's Wi-Fi; one can be added later in Settings) | same | same |
| Wi-Fi country, only if it isn't set | — | `--country` | Two-letter country code that `raspi-config` accepts |
| Is the camera mounted upside down? | No | `--camera-rotate 0` or `180` | |
| Is the display module mounted upside down? | No | `--display-rotation 0` or `180` | |
| Camera port (Pi 5 only; asked once, again with `--reconfigure`) | CAM1 | `--camera-port cam0` or `cam1` | 0 or 1 |
| Developer mode: join home Wi-Fi at every start so SSH keeps working? | No | `--dev` | |
| Reboot now? (at the end) | Yes | `--yes`, `--no-reboot` | |

"Upside down" for the camera sets both `camera_hflip` and `camera_vflip` (a 180° rotation). Both orientations can be changed later in `/etc/inkstant/device.toml`, or with `--reconfigure`.

### 15.6 Step by step

Each step lists what it checks, what it does, and what makes it stop. "Stop" means: print why and how to fix it, and exit. Nothing done so far is lost, and running again continues.

| # | Step | What happens | Stops when |
|---|---|---|---|
| 1 | **Start** | Take the lock, open the log, read `install-state.json`. Run `dpkg --configure -a` in case an earlier run was cut off inside apt | — |
| 2 | **Preflight** | All checks are run and reported together (see the list below) | Any check fails |
| 3 | **Questions** | §15.5. Skipped with `--update`; `--reconfigure` asks again | Bad answer: asked again |
| 4 | **Packages** | `apt-get update`; install §4.4's list, with `python3-picamera2` installed `--no-install-recommends`. Then upgrade whichever of `librpicam-app1`, `rpicam-apps-core`, `rpicam-apps-lite` and `rpicam-apps` is installed (`--only-upgrade`). rpicam-apps is built for one exact libcamera build but only asks for "that or newer", so apt keeps the old one when Picamera2 brings a newer libcamera. On the Pi 4 (2026-10-16), rpicam-apps 1.13.0 crashed listing cameras against libcamera `0.7.2+rpt20261009`, and 1.14.0 fixed it. apt waits up to 5 minutes for a lock held by something else (`-o DPkg::Lock::Timeout=300`), and the whole step retries twice on network errors. Then check Python is 3.13 and Picamera2 imports, and on Pi 3 and 4 that `rpi_apps.yaml` exists (§3, finding 10) | apt fails three times, or a check fails |
| 5 | **Interfaces** | `raspi-config nonint do_i2c 0` and `do_spi 0`. These also turn them on straight away (live `dtparam`, plus `modprobe i2c-dev`) ✅ [raspi-config](https://github.com/RPi-Distro/raspi-config/blob/master/raspi-config). Then check `/dev/i2c-1` exists. `/dev/spidev0.0` may only appear after the reboot, so its absence is a note, and the setup check tests it later | `/dev/i2c-1` is missing |
| 6 | **PiSugar** | See if anything answers on I2C at 0x57 (`i2cget -y 1 0x57 0x00`). Install pisugar-server 2.3.5 as in §13.5: download, checksum, preseeded answers, `dpkg -i`, the 0600 socket drop-in, then enable and restart it (its post-install script doesn't enable the service on a fresh install). Wait up to 10 s for the socket. Then require `get model` = `PiSugar 3` and `get firmware_version` ≥ 1.2.4 | No PiSugar on the bus ("Check the PiSugar is attached and switched on"); checksum mismatch; firmware too old ("Contact PiSugar support for a firmware update", §13.4) |
| 7 | **Camera check** (never stops) | `rpicam-hello --list-cameras` (rpicam-apps-lite is already in Pi OS Lite). The stock config.txt still auto-detects cameras at this point, so this works before our config is applied. Report the sensor it found. On a Pi 5, which port it's on is asked instead (§15.5) | Never; warns "No camera found: check the ribbon cable and its direction" |
| 8 | **App files** | Create the `inkstant-web` user and the `/var/lib/inkstant` folders with their owners and modes (§5.1). Copy the release to `/opt/inkstant/releases/<version>/`, run `python3 -m compileall -q`, switch the `/opt/inkstant/current` symlink atomically, and link `/usr/local/bin/inkstant-ctl`. Keep the previous release; delete older ones | Copy or compile fails |
| 9 | **Camera settings files** | Write `/etc/inkstant/device.toml` (`model`, `family`, `wifi_5ghz`, `camera_port`, flips, rotation). Write `settings.json` with defaults and the camera Wi-Fi name and password if it doesn't exist; otherwise only update what `--reconfigure` changed | — |
| 10 | **Wi-Fi** | Import or add the home network as `inkstant-home-1`, and write `inkstant-hotspot`, both as NetworkManager keyfiles (§6.8). Add the dnsmasq drop-in. Move `/etc/netplan/*.yaml` to `/var/lib/inkstant/backup/netplan/`. Create `/etc/cloud/cloud-init.disabled`. Nothing is disconnected: the SSH session keeps running on the current connection until the reboot | `nmcli` fails to load a profile |
| 11 | **PiSugar settings and clock** | Apply the settings in §6.5 over the socket, and read each back. Run `rtc_web`, then check `get rtc_time` is within 5 s of the system clock | A setting doesn't stick; the clock can't be set (warn only) |
| 12 | **System files** | §13: the config.txt block (below), cmdline.txt, the systemd units and drop-ins, the shutdown hook, the journald and systemd settings, `modules-load`. Then `systemctl daemon-reload`, enable `inkstant.service`, and disable the services in §13.6, recording which ones were enabled before. With `--update`, nothing is restarted | A file can't be written |
| 13 | **Checks before reboot** | `inkstant-ctl selftest --install` runs everything that can be checked before the reboot (§17). Write `install-state.json` | A critical check fails |
| 14 | **Finish** | Print the camera Wi-Fi name and password, how to take a photo (tap BTN) and share (hold it for 2 s), and what the light will do. Then reboot, or ask first. After the reboot the camera turns its Wi-Fi radio off unless developer mode is on, so SSH stops working; this summary says so | — |

**Preflight checks (step 2):**

- **Running as root.** If not, it asks to re-run with `sudo`.
- **Raspberry Pi OS Trixie on arm64:** `/etc/rpi-issue` exists, `VERSION_CODENAME=trixie` in `/etc/os-release`, and `dpkg --print-architecture` = `arm64`.
- **A supported model** from `/proc/device-tree/model` (§15.1.3); `--force-model` overrides.
- **First boot finished:** if cloud-init is still enabled, wait for `cloud-init status --wait` (up to 5 minutes), so Imager's settings aren't still being applied.
- **At least 2 GB free** on `/`.
- **The clock is plausible** (after the release's build date); otherwise HTTPS downloads would fail. It says to wait a minute for the network time.
- **Internet:** `https://archive.raspberrypi.com` and `https://github.com` reachable.
- **Wi-Fi country set** (`raspi-config nonint get_wifi_country`); if not, it's asked in step 3 and set with `do_wifi_country`.

**Editing config.txt** (`configure.py`, step 12):

1. Read `/boot/firmware/config.txt`. Save `config.txt.inkstant-bak` the first time.
2. Remove any earlier Inkstant block (between the `# BEGIN inkstant` and `# END inkstant` lines).
3. Comment out the stock lines that would conflict, by prefixing `#inkstant: `. Only these exact lines: `camera_auto_detect=1`, `display_auto_detect=1`, `dtoverlay=vc4-kms-v3d`, `max_framebuffers=2`, `dtparam=audio=on`. Record them in `install-state.json`.
4. Append the managed block from §13.1, with `imx708,cam0` on a Pi 5 whose camera is on CAM0.
5. Check the result: no active `dtoverlay=vc4-kms-v3d` outside the block, exactly one block, and the file ends with a newline.
6. Write it to a temp file in `/boot/firmware/`, `fsync`, then rename.

`cmdline.txt` gets the same treatment for its single line. The tokens added (`quiet`, once M8 confirms it helps) are recorded so they can be removed.

### 15.7 Everything it changes

| Kind | What |
|---|---|
| **Packages installed** | `python3-picamera2` and its dependencies, `python3-pil`, `python3-numpy`, `python3-flask`, `python3-waitress`, `python3-qrcode`, `python3-piexif`, `fonts-dejavu-core`, `i2c-tools`, `util-linux-extra`, `pisugar-server` 2.3.5 |
| **Files added** | `/opt/inkstant/`, `/usr/local/bin/inkstant-ctl`, `/etc/inkstant/device.toml`, `/var/lib/inkstant/`, `/etc/systemd/system/inkstant.service`, `inkstant-gallery.service`, `inkstant-failsafe.service`, `/etc/systemd/system/pisugar-server.service.d/inkstant.conf`, `/usr/lib/systemd/system-shutdown/inkstant-poweroff`, `/etc/systemd/journald.conf.d/inkstant.conf`, `/etc/systemd/system.conf.d/inkstant.conf`, `/etc/modules-load.d/inkstant.conf`, `/etc/NetworkManager/dnsmasq-shared.d/inkstant.conf`, `/etc/NetworkManager/system-connections/inkstant-*.nmconnection`, `/etc/cloud/cloud-init.disabled`, `/var/log/inkstant-install.log` |
| **Files edited** (backed up first) | `/boot/firmware/config.txt`, `/boot/firmware/cmdline.txt` |
| **Files moved** | `/etc/netplan/*.yaml` → `/var/lib/inkstant/backup/netplan/` |
| **Settings changed** | I2C and SPI on (`raspi-config`); Wi-Fi country (only if unset); PiSugar settings (§6.5) |
| **Services** | Enabled: `inkstant.service`. Disabled: §13.6's list, recorded |
| **Users** | `inkstant-web` (system user, no login) |
| **Not touched** | Your user account, SSH keys and SSH setting, host name, time zone (unless you later set it from a phone), anything in your home folder |

### 15.8 After the reboot: setup check

The camera checks itself on its first start after an install or update, so a builder knows it worked without SSH.

1. **Light:** a slow blink while starting, then solid when ready (§1.3).
2. **Self-test:** the camera app runs the self-test (§17) and saves the results to `/var/lib/inkstant/selftest.json`.
3. **If a critical check fails** (camera, display, PiSugar, storage), the gallery shows "Setup check found a problem: <item>" on every page. The light shows nothing for it (since 2026-10-17).
4. **Results in the gallery:** Settings → Advanced → **Setup check**, with each check, a pass or fail, a fix-it hint, and **Run again** (§11.4.6).

**What the builder does:**

1. Watch the light go solid.
2. With a module on, tap BTN: the light winks and the photo develops.
3. Hold BTN for 2 s: the share screen appears.
4. Join the camera's Wi-Fi from the QR code and open the gallery.
5. Open Settings → Advanced → Setup check: every row should pass.

### 15.9 Re-running, updating, reconfiguring, uninstalling

- **Re-running:** always safe. Finished steps print `ok`, and photos and settings are never touched. Re-run after a dropped connection, a failed step you've since fixed, or to repair a damaged setup.
- **Updating:** normally from the gallery (§16.1), which runs the new release's `install.sh --update`. That version keeps every answer, changes nothing while the camera is in use, and restarts through the camera app. Settings changed in the gallery since the install (the camera Wi-Fi name, developer mode) win over the install's answers, on an update and on a re-run alike (fixed 2026-10-17: an update put them back). If the update stops after switching to the new release, `update.sh` switches back to the old one.
- **Reconfiguring:** `sudo /opt/inkstant/current/install/install.sh --reconfigure` asks the §15.5 questions again and applies only those answers.
- **Uninstalling:** `sudo /opt/inkstant/current/install/uninstall.sh`:
  1. Stop and disable the Inkstant services; remove every file in §15.7 except the photos and the log.
  2. Put config.txt and cmdline.txt back: remove the block, un-comment the recorded lines, remove the recorded tokens.
  3. Re-enable the services recorded as disabled; remove `cloud-init.disabled`.
  4. Remove the `inkstant-*` Wi-Fi profiles, and offer to restore the netplan files from the backup.
  5. Ask before removing pisugar-server (other tools may use it).
  6. Keep `/var/lib/inkstant/photos` unless `--purge` is given and confirmed.
  7. Remove the `inkstant-web` user, then reboot.

### 15.10 When something goes wrong

| Symptom | Likely cause | Fix |
|---|---|---|
| "No internet" at preflight | Wi-Fi not joined, or the clock is wrong | Check `ping -c1 archive.raspberrypi.com`; wait a minute after boot for network time |
| apt waits a long time, then fails on the lock | First-boot updates still running | Wait and run again; the script already waits 5 minutes |
| "PiSugar not found" | PiSugar not seated or switched off; the Pi not sitting flat on its spring pins | Reseat it, press its power button, run again |
| "PiSugar firmware too old" | Firmware before 1.2.4 | Contact PiSugar support (§13.4) |
| "No camera found" (warning) | Ribbon reversed or loose; wrong port on a Pi 5 | Reseat the ribbon the way the [camera docs](https://www.raspberrypi.com/documentation/accessories/camera.html) show for your board; run again |
| SSH dropped during the install | Network blip | Log in and run the same command again |
| After the reboot: the light is solid but a tap gets no wink | No photo was taken: a setup check failed, or another problem the gallery explains | Share, open Settings → Advanced → Setup check, follow the hint |
| After the reboot: can't SSH in | Expected: the camera turns its Wi-Fi off | Share at home (SSH works while sharing), or turn on developer mode |

### 15.11 Later: a prebuilt image

🧪 Not in v1 (decided 2026-10-14).

- [rpi-image-gen](https://github.com/raspberrypi/rpi-image-gen) has an `rpi3` device layer and Trixie arm64 layers. ✅ [layer reference](https://raspberrypi.github.io/rpi-image-gen/layer_reference.html)
- Its stock `image-rpios` layout is boot + root only, so a photos partition needs a custom layout layer. ✅ [image-rpios](https://raspberrypi.github.io/rpi-image-gen/layer/image-rpios.html)
- No cloud-init layer is listed. For Imager to set Wi-Fi and the host name, the image needs:
  - cloud-init with the NoCloud bootfs data source;
  - Netplan;
  - raspi-config-vendor, for `cloudinit-rpi`. ✅ [Raspberry Pi guide](https://www.raspberrypi.com/news/how-to-add-your-own-images-to-imager/)
- **Distribution:** GitHub Releases, plus an optional Imager repository JSON so the camera appears inside Raspberry Pi Imager. ✅

## 16. Update and developer mode

### 16.1 Update

**Publishing a release:** `tools/build_release.py` builds `inkstant.tar.gz` (top folder `inkstant/`, with `src/inkstant/_build.py` holding `VERSION` and `BUILD_EPOCH`) and `SHA256SUMS`. Pushing a tag `v<VERSION>` makes CI build both and attach them to a GitHub release.

From the gallery's settings page, at home only (decided 2026-10-15):

1. **Check:** `update.check` asks the GitHub API for the latest non-pre-release.
2. **Preconditions:** sharing at home, and the battery charging or above 30%. Turn-off and the share timeout are paused until it ends.
   - `update_running` is set (§8.1). The light shows wait, and BTN is refused (taps and holds), so nothing stops sharing mid-download.
   - PWR is deferred: the camera turns off when the update finishes, instead of restarting (§8.2, §8.13).
3. **Start:** `update.start` runs `systemd-run --unit=inkstant-update --collect -p RuntimeMaxSec=1800 /opt/inkstant/current/install/update.sh <version>`, so the update survives the camera app being stopped, and gives up after 30 minutes.
4. **`update.sh`:**
   - Report each step with `inkstant-ctl update-report <state> <step>` (control command `update.report`, §12), shown in the gallery's "Updating" page (§11.4.9).
   - Download `inkstant.tar.gz` and `SHA256SUMS` over HTTPS and verify (step `downloading`).
   - Unpack to `/opt/inkstant/releases/<version>/` and run its `install.sh --update`, which switches the `current` symlink (step `installing`).
   - Write the result to `/var/lib/inkstant/update.json`, a record for diagnosing a failed update (the gallery learns the state through `update.report`), then run `inkstant-ctl shutdown --reboot` (step `restarting`). The camera app runs its shutdown sequence (§8.13, reason `update`) before the restart, or powers off if PWR was pressed.
   - **On any failure** (or the 30-minute limit): leave `current` on the old release, write `update.json` with the error, and report `failed`. The camera app clears `update_running`; the gallery shows the error; a deferred PWR press shuts down now.
5. **Keep two releases** (current and previous). `inkstant-ctl rollback` switches back over SSH.
6. **Later (not v1):**
   - Automatic rollback after three boots that never reach "ready".
   - Signed releases (for example with minisign) on top of HTTPS and checksums.

### 16.2 Developer mode

`inkstant-ctl dev on|off`, or a toggle under "Advanced" on the settings page (decided 2026-10-15). 📐

- `inkstant-home-1` autoconnects and the radio stays on at boot, so SSH works.
- Turn-off is paused while plugged in (§8.7). Sleep and the battery saver still apply.
- The USB hub stays on, on the 3B and 3B+ (§13.6).
- The gallery doesn't run outside share mode, so nothing else is exposed.

## 17. Diagnostics

`inkstant-ctl` (root):

| Command | What it does |
|---|---|
| `status` | Lifecycle, side state, errors, battery, storage, share (boot timing marks are only logged, below) |
| `selftest [--install]` | The checks below; non-zero exit on failure |
| `share on [--hotspot]` / `share off` | Share mode from the command line |
| `shutdown [--reboot]` | Clean shutdown or restart through the camera app (§8.13) |
| `dev on` / `dev off` | §16.2 |
| `regen-epd` | Rebuild every `.epd` (§7.7) |
| `rollback` | §16.1 |
| `power-button` | Run by pisugar-server (§8.2). Asks the camera app; if it doesn't answer within 3 s, runs `systemctl poweroff` |
| `failsafe` | Run by `inkstant-failsafe.service` (§13.3) |
| `update-report <state> [step] [message]` | Run by `update.sh` (§16.1) |

**Self-test checks** (also run automatically on the first start after an install or update; results go to `/var/lib/inkstant/selftest.json` and the gallery's Setup check; a critical failure shows a gallery banner, §15.8):

- **PiSugar:** socket answers; model PiSugar 3; firmware ≥ 1.2.4; soft power-off, its command, auto power-on and anti-mistouch set; I2C 0x57 answers. The PiSugar's own backup shutdown (3%, after 10 s) is a warning only.
- **Clock:** `/dev/rtc0` is the DS3231 driver (Pi 3 and 4); the RTC is valid.
- **Buses:** `/dev/spidev0.0` and `/dev/i2c-1` exist.
- **Camera:** listed by `Picamera2.global_camera_info()`; `rpi_apps.yaml` present on Pi 3 and 4.
- **Display:** the module-detect level only: "module in, not tested while idle" or "no module in", never a failure. The camera app owns the e-paper driver, so the check doesn't power the panel; `tools/bench/epd_show.py --probe` runs the reset/BUSY check from §6.3. A module that stops answering shows as the gallery's display error instead (§8.14).
- **Status light:** `/sys/class/leds/ACT` exists (if not, the hint is to update Raspberry Pi OS) and lists the `pattern` trigger (if not, a warning only: the light still blinks from software).
- **System:** the config.txt block is present and, on Pi 3 and 4, `CmaTotal` in `/proc/meminfo` is at least 120 MB (the `cma-128` option sets it through the device tree, so it never shows in `/proc/cmdline`); NetworkManager has the `inkstant-hotspot` profile; pisugar-server listens on no TCP port (`ss -ltn`).
- **Storage:** free space; the folders have the right modes.

**Logs:**

- **Where:** everything goes to journald (`journalctl -u inkstant -u inkstant-gallery -u pisugar-server`), and the gallery can download it.
- **Format:** one line per event, with a level.
- **Boot timing marks:** the app logs `uptime` at start, at the wake decision and at ready, so boot timing can be read without extra tools.

## 18. Security

- **Exposed to the network:**
  - Port 80 only while sharing.
  - mDNS (avahi), and SSH if the builder turned it on in Imager.
  - Nothing else: pisugar-server is socket-only, mode 0600 (§3, finding 1).
- **Accepted by design** (§1.7): no gallery login. Away, the hotspot's WPA2 password is the lock. At home, anyone on the home network can use the gallery while sharing.
- **Web defences:** Host check against DNS rebinding, a custom header and Origin check against cross-site requests, strict ID checks (§10.5).
- **Least privilege:**
  - The gallery runs as `inkstant-web`, sandboxed by systemd, with read-only photos.
  - Every change goes through the control socket, which checks the caller's user and every argument (§12).
- **Secrets:** home Wi-Fi passwords live only in root-only NetworkManager keyfiles. The hotspot password is in `settings.json` (root 0600) and on the share screen.
- **Updates:** HTTPS from GitHub plus SHA-256; signatures later (§16.1).

## 19. Testing

### 19.1 Off the Pi (pytest, in CI)

- **State machine:** every row of §8.2, plus §1.4's insert/remove table, as table-driven tests on `machine.py`.
- **Timers, sleep and the battery saver:** the effective times in §8.7 for every combination of settings and saver; sleep never at or above the turn-off time; use and camera use from each source; the automatic saver turning on at its level, ignoring bounces, dismissal and plugging in (§8.9).
- **Light:** `led_steady` for every state in §8.6, and the wink, with a fake clock.
- **BTN:** the tap reader against a fake I2C bus (read, clear, errors); a tap from both readers acted on once (§6.6); taps, double taps and holds in every state of §8.2.
- **Scheduling:** next wake with intervals, quiet hours (including over midnight), daylight-saving changes and the 120 s minimum; timed-wake detection at the window edges.
- **Photo store:** IDs, sequence recovery, atomic commit, startup recovery with planted orphans.
- **Images:**
  - `.epd` round trip and 180° rotation (checked against a slow reference).
  - Packing matches Waveshare's `getbuffer` output on the same indices.
  - A golden test for the dithered output of a reference image.
- **Display driver:** a fake SPI/GPIO recorder checks the exact byte sequence and DC levels against §6.3; abort at every step; every timeout.
- **PiSugar client:** recorded socket transcripts, including tap lines mixed into replies, error replies and disconnects.
- **Watchdog:** a fake I2C that relocks between our writes, to exercise read-back and retry.
- **Gallery:**
  - Flask test client with a fake control socket: every route, the Host check, the `X-Inkstant` check, ID checks.
  - ZIP streams checked with Python's `zipfile`, `unzip -t` and `7z t`; `Content-Length` equals the bytes sent; the ZIP64 path forced with a small threshold.
  - The browser tests, phone checklist and accessibility scan in §19.3 (Playwright against the simulator).
- **Control socket:** permission rules by user (faked peer credentials).
- **Network:** generated `nmcli --offline` arguments; keyfile write paths.

### 19.2 Simulator

`python3 tools/sim.py` runs the camera app with every fake, plus the gallery on `http://localhost:8080`.

- **Keys:** `b` a BTN tap (the shutter), `d` a double tap, `h` a hold, `p` PWR, `w` power on as a timed wake, `m` module on or off, `c` charger in or out, `l <n>` battery level, `q` quit.
- **Display:** every frame is written to `sim/display.png`, decoded from the packed frame, so the real packing path is what you see.
- **Camera:** a fake that serves sample photos at the real sizes.
- **Use:** gallery and behaviour work without a Pi.

### 19.3 Gallery web app

- **Server side (pytest):** templates render with sample photo sets (0, 1, 61, 5,000 photos, and some with no date). Every response carries the headers in §11.8. No template contains an inline script or style.
- **Browser (Playwright, in CI, against the simulator in §19.2):**
  - WebKit and Chromium, at 390 × 844, 412 × 915 and 1280 × 800.
  - **Flows:** scroll and load more; open the viewer, swipe, close with back; select a day and download (check the ZIP's headers and name); delete with confirmation; change a setting and reload; stop the server and see "Can't reach the camera"; dark mode.
  - An accessibility scan (axe-core) on each screen.
  - Playwright's WebKit is the engine behind Safari, but it isn't iOS Safari, so real phones still get the checklist below.
- **Real phones** (in M6 and M10), on an iPhone and an Android phone:
  - Press-and-hold saves to Photos / downloads.
  - A ZIP gets into Photos (iPhone) and extracts (Android).
  - QR join, then the gallery opens.
  - Dark mode; the largest text size; a quick VoiceOver and TalkBack pass.
  - Scrolling 3,000+ photos.

### 19.4 Setup script and models

**In CI (no Pi needed):**

- `shellcheck` on every shell script.
- `configure.py` against a fake system (`--root`), seeded with the stock Pi OS 2026-10-06 `config.txt`, `cmdline.txt` and an Imager netplan file. The generated files are compared with expected copies, for each model family and each camera port.
- **Run it twice:** the second run changes nothing.
- **Uninstall afterwards:** `config.txt` and `cmdline.txt` come back byte-for-byte.
- **Preflight logic** with faked `/etc/os-release`, model strings and disk sizes, including every unsupported board.

**On real boards** (milestone M7, and before each release):

| Board | Tests |
|---|---|
| **Pi 3A+** (main) | Fresh flash → install → setup check passes. Every bench test (§19.6) and acceptance test A1 to A16 (§19.7); soak tests; run the install again; update from the previous release; uninstall |
| **Pi 4B** | Fresh install, setup check, A1 to A9, share mode with an iPhone and an Android phone |
| **Pi 5** | Same as the 4B, plus PiSugar fit and I2C, the ACT LED and module detect, `rtc_rtc2pi` clock, CAM0 and CAM1 |
| **Pi 3B** | Fresh install, setup check, A1, A5, A7; a 2.4 GHz home network; Picamera2 import time and the §7.6 speed targets |
| **Pi 3B+** | Fresh install, setup check, A1, A5, A7 |

Each release's notes list the boards it was tested on.

### 19.5 On the Pi: bench scripts

`tools/bench/` has one script per test. Run them as root with the camera app stopped (`sudo systemctl stop inkstant`); each prints a result and saves raw data to `/var/lib/inkstant/bench`:

| Script | Measures | Bench test (§19.6) |
|---|---|---|
| `camera_fullres.py` | Starts the §6.4 configuration on the 3A+; logs `CmaFree` before and after; captures 20 frames; checks libcamera used `rpi_apps.yaml` | Full-resolution capture without CMA errors |
| `shutter_latency.py` | BTN tap time (when the camera reads it, after the PiSugar's double-tap window) against `SensorTimestamp` − exposure, and against the frame's arrival; 50 taps each at 5, 10 and 14 fps; `--auto` presses from software | Shutter delay |
| `settle.py` | Per-frame lens position, `AfState`, exposure and colour gains after re-aiming from far to near and after a lamp is switched; at 5, 10 and 14 fps, with and without the tuning override | Focus, exposure and colour settling |
| `epd_show.py` | Shows a test `.epd`; times each step of §6.3; `--abort-after N` for pull-out tests | No-module timeout, pull-out mid-develop |
| `inputs.py` | Module detect (pin 27) and BUSY edges with timestamps; `--light` steps through the ACT LED's looks | Light, module detect |
| `pisugar_probe.py` | Socket commands, tap events, watchdog enable/feed/disable with read-back, alarm set and read back | BTN, watchdog, firmware |
| `poweroff_test.sh` | `poweroff` with the hook; asks you to time the 5 V cut | Delayed power-off |
| `wake_cycle.sh` | Arms a wake 3 minutes out and powers off; on the next boot, prints the detection result | RTC wake cycle |
| `boot_timing.sh` | `systemd-analyze`, critical chain, app timing marks | Boot-to-ready |
| `share_e2e.md` | Manual checklist for phones (§19.7) | Share mode on iPhone and Android |

### 19.6 Bench checklist

The software's checks on real hardware, in M1 and M8 (§20), and the parts and case checks still open. The README's "Before you close the case" list gives builders the same case checks.

- [x] Amazon listings: the e-paper is the HAT+ (E); the camera is the Arducam IMX708 75° autofocus (checked 2026-10-15)

**Camera:**

- [ ] Full-resolution capture on the 3A+ with main + lores and 2 buffers, without CMA errors; `rpi_apps.yaml` is used and camera CMA is about 69 MB (`cma-128` set)
- [ ] Shutter delay (moment captured and frame in hand), current and CMA use on the 3A+ at 5, 10 and 14 fps
- [ ] Camera current at 5, 10 and 14 fps, and continuous against settled autofocus. Pick the lowest that keeps the shutter delay at or under 0.5 s and refocusing within 1 s
- [ ] Focus, exposure and colour settling after re-aiming and after a change of light, with and without the tuning override (`settle.py`)
- [ ] The first photo after the camera starts is settled (start-up runs 10 frames at 14 fps)
- [ ] Sleep and wake: the frame is in hand within 1 s of a press while asleep, in focus after re-aiming; no CMA allocation on waking; current while asleep
- [ ] `AeExposureMode` Short against Normal: handheld sharpness indoors
- [ ] Picamera2 import time and the camera app's memory on the 3A+
- [ ] Dithering palette tuned on the real panel

**Display:**

- [ ] No module: the BUSY timeout works and the app doesn't hang
- [ ] Taking a module off mid-develop: the app stops the update cleanly, the module is unharmed, and the next module put on develops that photo
- [x] Module detect on ID_SD (GPIO0) with the Pi's pull-down: high with a module on (the HAT's ID-chip pull-up wins; the chip answers at 0x50 on i2c-0 with "R-Pi"), low without (bench Pi 4, 2026-10-17)
- [ ] The same on the 3A+, 3B, 3B+ and Pi 5
- [ ] A module with PWR held low draws almost nothing
- [ ] Putting a module on and taking it off while the camera is on and idle doesn't reset the Pi or upset the PiSugar
- [ ] Share-screen QR codes scan from 15 to 30 cm on the real panel
- [ ] The screen is easy to read in outdoor shade, and a module goes on straight and stays on when the camera is shaken gently

**Light:**

- [x] The ACT LED has the `timer` and `pattern` triggers, and the red PWR LED is off (trigger `none`, brightness 0), on the Pi 4 with kernel 6.18 (2026-10-17)
- [ ] `act_led_trigger=timer` blinks the ACT LED from early kernel boot (needs a reinstall with the new config.txt), and what the LED shows before the kernel starts: the Pi 4, then the 3A+, 3B and Pi 5
- [ ] The pattern trigger and the red PWR LED staying dark on the 3A+, 3B and Pi 5
- [ ] The ACT LED through the case's opening: easy to see indoors and in daylight
- [ ] The four looks in §1.3 are easy to tell apart through the case's opening, in daylight and in the dark, by someone who hasn't read the table
- [ ] The LED stays dark from the app's "off" to the power cut

**Power and PiSugar:**

- [ ] PiSugar firmware is 1.2.4 or later. The shutdown hook's delayed power-off cuts 5 V about 10 s after `poweroff`, and not on `reboot`
- [ ] Shortest safe power-cut delay: the time from the shutdown hook to halted
- [ ] PWR through the case: click-and-hold boots, and a long press runs our command (soft shutdown)
- [x] BTN as the shutter (Pi 4, 2026-10-17): taps reach the camera app through its own reader, none through pisugar-server first and none twice; the tap delay is acceptable
- [ ] The PiSugar's double-tap window and hold time, measured
- [ ] pisugar-server listens on no TCP port; its socket is mode 0600; the camera app still gets taps and battery readings
- [ ] Watchdog: enabling works while pisugar-server is running (read-back); killing the feed restarts the camera within about a minute; a normal shutdown, even one longer than 60 s, isn't power-cycled
- [ ] RTC wake → refresh → power-off cycle on battery
- [ ] The kernel RTC (`i2c-rtc,ds3231`) sets the clock at boot, and the slideshow alarm still fires with it loaded
- [ ] Slideshow alarm while the PiSugar is charging ([#88](https://github.com/PiSugar/pisugar-power-manager-rs/issues/88))
- [ ] PiSugar hibernate current, and what its clock reads after the battery is disconnected and reconnected
- [ ] Current in each state (starting, ready, asleep, capturing, developing, sharing, halted, off) and each battery-saving measure on and off (§14)
- [ ] USB hub off on the 3B and 3B+: current saved, nothing else affected
- [ ] Boot-to-ready time, before and after §13's changes; `quiet`, initramfs removal and HDMI probe skips
- [ ] Battery cost of share mode (Wi-Fi on)
- [ ] Pi 5 with the PiSugar 3 Plus: every spring pin lands on its header pin, then I2C, power and the `rtc_rtc2pi` clock
- [ ] PWR and BTN press reliably through the case's caps
- [ ] The PiSugar's charging chip inside the case while charging at full current: it reaches 50 to 80 °C, so check the case leaves it air

**Sharing:**

- [ ] The hotspot (WPA2, PMF off) joins from an iPhone, an Android phone and a Mac
- [ ] Android with mobile data on reaches `http://10.42.0.1`, with and without "stay connected"; try the no-gateway DHCP option if it doesn't (§9.4)
- [ ] Share mode on iPhone and Android, at home (home Wi-Fi, IP QR code) and away (hotspot): QR join, the phone stays connected, the gallery opens, press-and-hold saves, a ZIP gets into Photos
- [ ] A WPA3-only home network (a router or phone hotspot set to WPA3 only), added in the gallery: sharing starts on it, the log says `inkstant-home-N now uses sae`, and in developer mode it then joins at boot. On a 3A+ as well as the bench Pi 4 (§9.5)
- [ ] The streamed ZIP opens in iOS Files and the Android Files app; the download shows progress
- [ ] `/tmp` is tmpfs on Pi OS (`findmnt /tmp`)

### 19.7 Acceptance scenarios (on the finished camera)

| # | Scenario | Pass when |
|---|---|---|
| A1 | Power on, tap BTN, watch it develop | Light: slow blink (starting) → solid → wink → slow blink (developing) → solid; photo on the module and in the gallery |
| A2 | Shoot five photos with no module, then put one on | Only the fifth develops (§1.4) |
| A3 | Take the module off mid-develop, then put another on | The second module gets the same photo; no hang |
| A4 | Long-press PWR while developing | Shuts down after the image is shown |
| A5 | Leave it idle 10 minutes, once with the slideshow on and once off | Asleep at 5 min (the light stays solid); off at 10. Slideshow on: the last photo stays. Slideshow off: the screen turns white first, with the light blinking slowly until it's done |
| A6 | Slideshow at 5 min on battery overnight | One change per interval, none in quiet hours; the battery matches the estimate |
| A7 | Share away: join by QR, open the gallery, save one photo, download a ZIP | Works on iPhone and Android |
| A8 | Share at home | QR with the home IP opens the gallery; the clock syncs |
| A9 | Run until low battery | Warning at 15%; shutdown at 5% with no alarm, and the screen turns white first; no wake until charged and turned on |
| A10 | Kill the camera app with `kill -STOP` | systemd restarts it (30 s), or the PiSugar power-cycles (60 s) |
| A11 | Fill the storage | Banners at 1 GB and 500 MB; at 500 MB a tap gets no wink and takes no photo; deleting photos clears it |
| A12 | Drain the battery fully, then charge and turn on | "Set the time" banner; slideshow paused (so the screen turns white at the next power-off); photos named by sequence only |
| A13 | Let it sleep, re-aim at something close, tap the shutter | The wink as soon as the tap is read; the photo is taken about 1 s later, in focus, and develops |
| A14 | Battery saver: turn it on; later, run the battery down to 20% with it off | On: it sleeps after 1 min and turns off after 5, and sharing stops after 5 min without use. At 20% it turns on by itself ("On because the battery is low"); plugging in turns it off; "Turn off" keeps it off until the next plug-in |
| A15 | Share, then tap, double-tap and hold BTN | A tap takes no photo, and the light keeps blinking slowly; a double tap switches between home Wi-Fi and the camera's own; a hold stops sharing, and taps take photos again once the photo is back on the module; the "What the light means" sheet matches what the light does |
| A16 | Start an update from the gallery and press PWR halfway | The light blinks slowly, BTN does nothing, the update finishes, and the camera turns off instead of restarting; next power-on runs the new version |

**Soak tests before v1.0:**

- 48 hours of slideshow wakes every 15 minutes on battery.
- 200 shots in a row.
- 50 pull-outs during develop.
- 20 share sessions alternating home and away.

## 20. Build plan

Milestones in order. Each ends with an exit check. M2 and the gallery half of M6 need no hardware, so they can run alongside M1 and M3 to M5.

| Milestone | Deliverables | Exit check | Needs |
|---|---|---|---|
| **M0 Foundations** | Repo layout (§4.4), `pyproject.toml`, dev requirements, CI (pytest and ruff on Python 3.13), `ipc.py`, `atomic.py`, all `hw/` interfaces with fakes, simulator skeleton | CI green; `sim.py` starts and shows a blank display | — |
| **M1 Hardware bring-up** (3A+ on the bench) | `tools/bench/` scripts for the camera, display, inputs, PiSugar, power-off and wake cycle; first measurements | These §19.6 tests pass: full-res capture and CMA, shutter delay and settling (frame rate chosen), display driver and no-module timeout, BTN, the ACT LED and module detect, PiSugar socket, watchdog, firmware ≥ 1.2.4, delayed power-off, RTC wake cycle, kernel RTC | Parts, Pi OS card |
| **M2 Core libraries** | `store.py`, `settings.py`, `state.py`, `imaging/*`, palette tuner; palette tuned on the panel | Unit tests; §7.6 timings measured on the 3A+ | M0 (and M1's display for tuning) |
| **M3 Camera app core** | `machine.py`, capture, display jobs, module events, BTN taps, status light (four looks and the wink), sleep and wake, turn-off, shutdown, watchdog, control socket, `inkstant-ctl power-button` | Acceptance A1 to A5, A10, A13 and A15 on hardware | M1, M2 |
| **M4 Power and slideshow** | Wake detection and path, scheduling, quiet hours, battery rules, battery saver, clock (§6.7) | A6 (one night), A9, A12, A14 | M3 |
| **M5 Share mode** | Keyfiles, home and hotspot start/stop, share screen, timeouts, `rtc_web` | Phones join and reach a test page, home and away | M3 |
| **M6 Gallery** | Everything in §10 and §11: all routes, pages, dialogs and states; ZIP streaming; settings; security headers and checks | A7, A8, A11; §19.1 gallery tests; §19.3 browser tests and phone checklist; §11.7 budgets met | M0 (in the simulator), M5 (on the Pi) |
| **M7 Install and integration** | Everything in §15: `install.sh`, `configure.py`, `uninstall.sh`, config.txt, units, hook, pisugar-server install, netplan import, setup check | §19.4: CI tests pass; fresh flash to working camera with only the documented steps on the 3A+, 4B and 5 (3B and 3B+ before release) | M3 to M6 |
| **M8 Boot and power tuning** | Measure, try §13.1 and §13.2's candidates, trim systemd; the power plan in §14.5 (current in each state, each measure on and off, PiSugar standby, power-cut delay, USB hub, camera settings) | Numbers recorded in §4.6 and §14 (boot to ready, wake cycle, current per state, battery estimates); a candidate is kept only if it helps and costs nothing in speed or features | M7 |
| **M9 Update, diagnostics, docs** | Update flow (with the light, deferred PWR and failure reporting), rollback, logs download, README builder guide | Update from a pre-release to v1.0 over home Wi-Fi; A16 | M7 |
| **M10 Release** | Soak tests (§19.7), every bench test checked, tag v1.0, GitHub release with `inkstant.tar.gz` and `SHA256SUMS` | All of the above pass | M8, M9 |

**Biggest risks, tackled first in M1:**

1. Full-resolution camera memory on the 512 MB 3A+.
2. The module on the header: going on straight every time, and taking it off and putting it on while the camera is on.
3. PiSugar power-off, alarm and watchdog behaving as documented on our firmware.
4. Android reaching the hotspot gallery (this one is M5).

Each has a fallback or a mitigation written down (§6.4 ladder, the case's guide for the module in the README, §6.6/§13.4, §9.4).

## 21. Open questions

Nothing here blocks writing the software. Each one has a default in this spec and is settled by a bench test, or by the user once the numbers are in.

| Question | Default in this spec | Settled by |
|---|---|---|
| Frame rate: 5, 10 or 14 fps | 5 fps until M1; the lowest-current rate that meets the targets | M1 measurements (§6.4) |
| Refocus target after re-aiming | 1 s (provisional, our choice) | The user, with M1's current and focus numbers |
| Android with mobile data on: does Chrome reach `http://10.42.0.1`? | Three fallbacks in order (§9.4); the last needs a product decision | M5 phone tests |
| Hotspot with Protected Management Frames | PMF off | M5 phone tests (§19.6) |
| Does the slideshow alarm fire while charging ([#88](https://github.com/PiSugar/pisugar-power-manager-rs/issues/88))? | If not, the slideshow skips while charging; accepted | M1 |
| Shortest safe power-cut delay | 10 s | M8 |
| Wake-to-shoot time from sleep | Target 1 s | M3 (A13) |
| Are the light's four looks easy to see and tell apart through the case's opening? | §1.3 | M3, with someone who hasn't read the table (§19.6) |
| How long the PiSugar waits before reporting a single tap | Accepted on the bench (2026-10-17) | Measure it (§6.6) |
| Is putting a module on or taking it off safe while the camera is on? | Allowed, as before | A bench test (§19.6) |
| Full-resolution camera memory on the 3A+ | 69 MB of a 128 MB CMA; the §6.4 failure ladder | M1 |

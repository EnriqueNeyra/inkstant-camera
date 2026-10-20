# Inkstant hardware reference

Every part of the camera, with the measurements an enclosure needs and links to the source drawings, schematics and 3D models. It's written for whoever designs the case, person or CAD agent. The [README](README.md) is the build guide for builders. [SPEC.md](SPEC.md) explains how the software behaves and why.

**How to read the numbers**

- All dimensions are in millimetres.
- ✅ comes from a primary source (a manufacturer drawing, CAD file or datasheet) or was checked on the bench camera. The source is named.
- 📐 is derived or estimated: read off a drawing or photo, or calculated. Good for a first model, not for final tolerances.
- 🧪 needs measuring on the real part before the case is finalised. See [Measure before you finalise](#measure-before-you-finalise).

**The ground rules for the case**

- No soldering, no wiring, and parts anyone can buy (Amazon-level).
- The print (the display) must pull off easily and go back on. Each print has its own enclosure, separate from the camera body, so it pops out like a piece of film.
- A beginner must be able to assemble it.
- **Every part is 3D-printed,** in as few parts as possible, and each prints without excessive supports. No light pipe or window in another material.
- **It holds together securely with no extra hardware:** no screws, glue, inserts or standoffs beyond the screws that come with the PiSugar. The one exception is the optional magnets in each print's enclosure.
- **The display needs no shading or recess** against sunlight: the small risk of sun damage is accepted.
- Design for the **Raspberry Pi 3B+ only** (decided 2026-10-19, replacing the 3A+, which is often sold out). It's easy to buy, Raspberry Pi will make it until at least January 2030 ✅ ([product page](https://www.raspberrypi.com/products/raspberry-pi-3-model-b-plus/)), and the PiSugar 3 Plus is made for it. It's the 3A+'s chip and layout with a USB and Ethernet end added, which the camera doesn't use. The software also runs on the Pi 3A+, 3B, 4B and 5, but this case doesn't need to fit them. The 3B has the same footprint as the 3B+ ✅ ([product brief](https://pip.raspberrypi.com/documents/RP-008338-DS)), so it should fit anyway. [Other boards](#other-boards-not-for-this-case) has pointers for the rest.

## Contents

1. [The build at a glance](#the-build-at-a-glance)
2. [3D models](#3d-models)
3. [Coordinate frame](#coordinate-frame)
4. [Stack-up (heights)](#stack-up-heights)
5. [Raspberry Pi 3B+](#raspberry-pi-3b)
6. [The print: Waveshare 3.6" e-Paper HAT+ (E)](#the-print-waveshare-36-e-paper-hat-e)
7. [Battery board: PiSugar 3 Plus](#battery-board-pisugar-3-plus)
8. [Camera module](#camera-module)
9. [Camera cable](#camera-cable)
10. [Status light](#status-light)
11. [What the case has to do](#what-the-case-has-to-do)
12. [A layout that fits](#a-layout-that-fits)
13. [Measure before you finalise](#measure-before-you-finalise)
14. [Other boards (not for this case)](#other-boards-not-for-this-case)
15. [Sources](#sources)

## The build at a glance

| Part | What it is | Notes |
|---|---|---|
| Computer | Raspberry Pi 3B+ | 85 × 56 mm. The PiSugar under it covers the first 65 mm; the USB and Ethernet end has nothing under it |
| Print | Waveshare 3.6" e-Paper HAT+ (E), one per print | Its 40-pin socket pushes onto the Pi's header. Pulled off, it keeps its picture with no power |
| Battery board | PiSugar 3 Plus, 5000 mAh | Sits under the Pi, touching the header from below with spring pins. Its **BTN** is the shutter and **PWR** is the power button |
| Camera | Arducam 12MP IMX708 75° autofocus (Raspberry Pi Camera Module 3 outline) | Connects to the Pi's CAMERA connector with a 15-pin flat cable |
| Light | The Pi's own green ACT LED | It shines out through a small opening in the case |
| Storage | microSD card | In the Pi's slot, at the board's short edge |
| Charging | USB-C on the PiSugar | The Pi's own power port isn't used |

Side view of the stack, display side up:

```text
   ┌──────────────── print (Waveshare HAT+ (E)) ────────────────┐  ← display faces the user (back of the camera)
   └──────┬──────────────────────────────────────┬──────────────┘
          │ tall 40-pin socket   ~16 mm gap (🧪) │ USB and Ethernet sockets, 16 high
   ┌──────┴──────────── Pi 3B+ (header on top) ──┴─────────────┐
   └──┬──────────────────────────────────┬─────────────────────┘
      │ PiSugar posts (1.6 mm) + spring pins under the header
   ┌──┴────── PiSugar 3 Plus board ───────┴──┐   nothing under the USB and Ethernet end
   │ USB-C, BTN, PWR, battery LEDs underneath, along the Pi's port edge
   └──────────── 5000 mAh battery ───────────┘    camera module faces the front (down in this view)
```

## 3D models

Use these in the CAD where they exist, and check each against its drawing.

| Part | Model | Notes |
|---|---|---|
| Pi 3B+ | Two community models on GrabCAD: [Raspberry Pi 3 B+](https://grabcad.com/library/raspberry-pi-3-b-2) (STEP and SolidWorks, from Raspberry Pi's mechanical drawing) and [Raspberry Pi 3 Model B+ - Reference Model](https://grabcad.com/library/raspberry-pi-3-model-b-reference-model-1) (STEP, measured with calipers) | Raspberry Pi publishes no 3B+ model, but its [DXF (RP-008336-DS)](https://pip.raspberrypi.com/documents/RP-008336-DS) has every part's outline, and the [PDF drawing (RP-008337-DS)](https://pip.raspberrypi.com/documents/RP-008337-DS) the heights. Check both models against them 📐. GrabCAD needs a free account to download |
| Print | None published (searched 2026-10-17: Waveshare, GrabCAD, step.parts, Printables, MakerWorld and Thingiverse) | Model it from Waveshare's [board size drawing](https://www.waveshare.com/img/devkit/LCD/3.6inch-e-Paper-E/3.6inch-e-Paper-HAT-plus-E-details-size.jpg) and [panel datasheet](https://files.waveshare.com/wiki/3.6inch_e-Paper_HAT%2B_E/3.6inch_e-Paper_HAT%2B.pdf), with the numbers [below](#the-print-waveshare-36-e-paper-hat-e) |
| PiSugar 3 Plus | PiSugar's own [STEP](https://github.com/PiSugar/PiSugar/blob/master/model_pro/pisugar3plus.STEP) and [STL](https://github.com/PiSugar/PiSugar/blob/master/model_pro/pisugar3plus.STL) ✅ | The board and its parts, with no battery. The same folder has a SolidWorks part, [batt5000.SLDPRT](https://github.com/PiSugar/PiSugar/blob/master/model_pro/batt5000.SLDPRT), probably a 5000 mAh battery; whether it's this board's battery isn't confirmed 🧪 |
| Camera | Raspberry Pi's [Camera Module 3 STEP (RP-008154-DS)](https://pip-assets.raspberrypi.com/categories/1207-design-files/documents/RP-008154-DS-1-camera-module-3-step.zip) and [mechanical drawing (RP-008153-DS)](https://pip-assets.raspberrypi.com/categories/1207-design-files/documents/RP-008153-DS-1-camera-module-3-standard-mechanical-drawing.pdf) ✅ | The Arducam board bought uses the Camera Module 3 outline; check it against the model 🧪 |

## Coordinate frame

Everything is given in one frame, the **Pi frame**, seen from above the Pi (the print's side):

- **Origin:** the corner of the Pi's board where the **port edge** (micro-USB power, HDMI and audio) meets the **microSD edge** (the short edge with the microSD slot and the two LEDs).
- **+x:** along the long edge, away from the microSD edge, towards the USB and Ethernet end.
- **+y:** away from the port edge, towards the 40-pin header, which runs along the far long edge.
- **+z:** up from the **top face of the Pi's board**, towards the print. The display faces +z, which is the camera's back. The lens faces −z, the camera's front.

This is the orientation of Raspberry Pi's own mechanical drawings, with their origin at the bottom-left. Header pin 1 is at the low-x end (x = 8.37).

## Stack-up (heights)

The print is fully seated on the 3B+, measured from the Pi's top face (z = 0). `t` is the Pi's board thickness: about 1.4 to 1.6 mm. Raspberry Pi doesn't publish it, so measure it 🧪.

| z | What | Source |
|---|---|---|
| ≈ +16 + 2.4 | Display surface: the print's board (≈1.6 📐) plus the glass (0.74, or 0.78 with film ✅) | Waveshare datasheet |
| ≈ +16 (🧪) | Underside of the print's board. Waveshare's photos show it seated just above a Pi 4's 16 mm USB ports, and the 3B+'s are the same height | 📐 Waveshare photos; measure |
| +8.5 | Top of the Pi's header pins | ✅ Pi drawing (Z-height 8.5) |
| +2.5 | Top of the header's plastic base, where the print's socket stops | 📐 standard 2.54 mm header |
| +16.0 / +13.5 / +6.5 / +6 / +5.5 | 3B+ USB-A stacks / Ethernet / HDMI / audio jack / CAMERA and DISPLAY connectors | ✅ Pi 3B+ drawing |
| 0 | Top face of the Pi's board | |
| −t | Bottom face of the Pi's board, resting on the PiSugar's posts | |
| below −t | The USB and Ethernet sockets' through-hole pins, under the far end | 🧪 measure |
| −t − 1.6 | Top face of the PiSugar's board. Its spring pins stand 0.4 mm above the posts and press into the header's solder joints | ✅ PiSugar STEP |
| −t − 2.6 | Bottom face of the PiSugar's board (it's 1.0 thick) | ✅ PiSugar STEP |
| −t − 4.2 | Bottom of BTN and PWR (they stick out 1.6 below the board) | ✅ PiSugar STEP |
| −t − 5.8 | Bottom of the PiSugar's USB-C socket (3.2 below the board) | ✅ PiSugar STEP |
| −t − 8.8 | Bottom of the battery plug, the lowest part on the board (6.2 below) | ✅ PiSugar STEP |
| below that | The 5000 mAh battery, under the PiSugar's board | 🧪 measure: no published size |

Pulling the print only part-way onto the header raises it by however far it's left off. Each contact needs about 3 mm of engagement to hold reliably 📐, so test any partial-seating design on the bench 🧪.

## Raspberry Pi 3B+

The camera's computer: 4 × Cortex-A53 at 1.4 GHz (BCM2837B0), 1 GB, dual-band Wi-Fi, in production until at least January 2030 ✅ ([product page](https://www.raspberrypi.com/products/raspberry-pi-3-model-b-plus/)). It's the 3A+'s layout with a USB and Ethernet end added: the header, the holes, the port edge and the LEDs are in the same places.

Sources: the [DXF (RP-008336-DS)](https://pip.raspberrypi.com/documents/RP-008336-DS), a full assembly drawing with every part's outline; the [PDF drawing (RP-008337-DS)](https://pip.raspberrypi.com/documents/RP-008337-DS), for heights; the [product brief (RP-008338-DS)](https://pip.raspberrypi.com/documents/RP-008338-DS); the [reduced schematics (RP-008339-DS)](https://pip.raspberrypi.com/documents/RP-008339-DS); and the [official case drawing (RP-008160-DS)](https://pip.raspberrypi.com/documents/RP-008160-DS). Positions below are from the DXF and heights from the PDF ✅, unless marked.

**Board**

- **Outline:** 85 × 56, corner radius 3.
- **Mounting holes:** ø2.7 with ø6.0 keep-out pads, at (3.5, 3.5), (61.5, 3.5), (3.5, 52.5) and (61.5, 52.5). That's a 58 × 49 pattern, the same on every 40-pin Pi (hole and pad sizes from the Pi 4 DXF, same pattern).
- **40-pin header:** 2 × 20 at 2.54 pitch.
  - Body: x 7.1 to 57.9, y 50.0 to 55.0, centred on (32.5, 52.5).
  - Pin 1 (square pad) is at (8.37, 51.23). Odd pins run along y = 51.23 (the inner row), and even pins along y = 53.77 (the edge row). Pin n is at x = 8.37 + 2.54 × ⌊(n − 1) / 2⌋.
  - Overall height is 8.5.

**Connectors and parts on top**

| Part | Position | Height above the board | Notes |
|---|---|---|---|
| micro-USB power | x 6.85 to 14.35, y −0.6 to 4.7 | small | Not used: the camera charges through the PiSugar |
| HDMI | x 24.75 to 39.25, y −1.5 to 10.65 | 6.5 | Not used. Sticks out 1.5 past y = 0 |
| CAMERA (CSI, 15-pin) | x 43.55 to 47.5, y 0.3 to 22.7 | 5.5 | Upright connector; the cable leaves upwards (+z) |
| Audio jack | x 50.0 to 57.0, y 0 to 12.5 | 6 | Not used. Its barrel sticks out to y −2.5 |
| DISPLAY (DSI) | x 1.5 to 5.45, y 16.8 to 39.2 | 5.5 | Not used |
| **Ethernet** | x 65.65 to 87.0, y 2.5 to 18.0 | 13.5 | Not used. Sticks out 2 past x = 85 |
| **USB-A, two stacks** | x 69.3 to 87.0; y 21.65 to 35.57 and 39.65 to 53.57 | 16.0 | Not used. Stick out 2 past x = 85 |
| USB and Ethernet chip (LAN7515) | x 57.05 to 66.05, y 23.4 to 32.4 | low | The camera switches its power off, except in developer mode |
| PoE header (4 pins) | x 59.0 to 64.0, y 44.1 to 48.7 | about the 40-pin header's 📐 | Not used |
| SoC | x 20.0 to 34.0, y 24.5 to 38.5 | low | Under the print; give the gap some airflow |
| Wi-Fi module (metal can) | x 6.6 to 17.1, y 36.2 to 49.4 | low | Its antenna is at the microSD edge (below) |

**LEDs:** two, at the microSD edge, labelled ACT and PWR on the board.

- **ACT (green, the status light):** x 0.35 to 1.45, y 10.7 to 12.3, centred at about (0.9, 11.5).
- **PWR (red):** x 0.35 to 1.45, y 7.1 to 8.7, centred at about (0.9, 7.9).
- The Pi 4 DXF puts them in the same places ✅. They're right-angle parts that shine out of the board's edge (−x) as well as up 📐; the official 3A+ case shows the same LEDs through two small holes in that side wall ✅.

**Underneath**

- **microSD socket:** x 1.69 to 13.13, y 22.1 to 34.1. The card sticks out to x −2.27 (y 22.5 to 33.5).
  - With the PiSugar fitted, the card sits in the 1.6 mm gap between the two boards and is reached from the −x side.
- **RAM:** x 37.6 to 49.6, y 27.5 to 39.5, low.
- **Through-hole pins:** the USB and Ethernet sockets' pins come through under the far end (x 67.4 to 82.5, y 3.6 to 53.6), as do the header's and the audio jack's. How far they stick out isn't published 🧪.
- **Nothing under the far end:** the PiSugar covers x 0 to 65 only.

**Wi-Fi antenna:** the bottom's "Proant" licence text is at the microSD edge, around x 1 to 5 and y 35 to 39 ✅, as on the Pi 4. Keep metal (magnets, screws, metal plates) and the battery away from that corner.

## The print: Waveshare 3.6" e-Paper HAT+ (E)

The removable "print". Each print is a complete board: an E Ink Spectra 6 panel glued to the front of a driver board, with a 40-pin socket on the back that pushes onto the Pi's header. Sources: [Waveshare wiki](https://www.waveshare.com/wiki/3.6inch_e-Paper_HAT+_(E)_Manual), [product page](https://www.waveshare.com/3.6inch-e-paper-hat-plus-e.htm?sku=32650), [board size drawing](https://www.waveshare.com/img/devkit/LCD/3.6inch-e-Paper-E/3.6inch-e-Paper-HAT-plus-E-details-size.jpg), [panel datasheet](https://files.waveshare.com/wiki/3.6inch_e-Paper_HAT%2B_E/3.6inch_e-Paper_HAT%2B.pdf) and [schematic](https://files.waveshare.com/wiki/3.6inch_e-Paper_HAT%2B_E/3.6inch_e-Paper_HAT%2B_Schematic.pdf). Waveshare publishes no STEP model.

**Driver board** (✅ Waveshare size drawing)

- **Outline:** 89.5 × 59.0, corner radius 2.
- **Mounting holes:** four, on the same 58 × 49 pattern as the Pi.
  - 5.0 in from each long edge.
  - 5.75 in from one short end, and 25.75 in from the other. The 25.75 end lies over the Pi's USB and Ethernet end.
  - The hole diameter isn't given 🧪.
- **The 40-pin socket:** on the back, between the two holes along one long edge, centred between them.
- **Weight:** 53 g ✅ (product page).

**Where it sits on a Pi** (✅ from the hole pattern and Waveshare's photos of it on a Pi 4)

- The print can only go on one way: holes over holes, the board lying over the Pi, the socket over the header.
- In the Pi frame, the board covers **x −2.25 to 87.25, y −1.5 to 57.5**.
  - It reaches 2.25 past the microSD/LED edge.
  - It reaches 1.5 past each long edge.
  - It reaches 2.25 past the far edge (x = 85), so it covers the whole 3B+, its USB and Ethernet sockets (to x 87.0) included.

**The panel on the front** (✅ panel datasheet; position 📐)

- **Glass:** 86.6 × 56.0 × 0.74 (0.78 with the protective film). Tolerance ±0.2 on the outline.
- **Active area:** 76.2 × 50.8, 600 × 400 pixels at 0.127 pitch.
- **Borders around the active area:** 2.6 on three sides, and **7.8 at the cable end**.
- **The panel's cable:** a 50-way flat cable from the cable end wraps around that short edge of the board, to a connector on the back. The size drawing shows it sitting about 1.46 in from the board's edge.
- **In the Pi frame:**
  - The cable end is at **−x**, over the microSD/LED edge.
  - The glass is centred on the board (about 1.45 and 1.5 in from its edges 📐): **x −0.8 to 85.8, y 0 to 56**.
  - Active area: **x 7.0 to 83.2, y 2.6 to 53.4**, centred at (45.1, 28.0). Use this for the display window. A bezel may cover the borders but should leave the whole active area visible.

**The back of the board** (📐 from the size drawing and photos)

- **The 40-pin socket:** black, and extra tall. Fully seated, it holds the board just above 16 mm USB ports (a Pi 4's in Waveshare's photos; the 3B+'s are as tall). Measure its body height and the seated gap 🧪.
- **Other parts:**
  - the panel cable's connector, near the cable end;
  - a 9-pin GH1.25 socket for other boards (unused, here);
  - the BS jumper;
  - small ICs.

  All are low compared with the socket.
- **The ID chip's pull-ups on pins 27 and 28:** these are what tell the camera a print is on ✅ (schematic; HAT+ spec 3.9 kΩ).

**Environment and handling** (✅ Waveshare)

- **Operating temperature:** 0 to 50 °C. The FAQ says 0 to 40 °C, so design for 40.
- **Humidity:** 35 to 65 %RH.
- **Storage:** below 30 °C and 55 %RH, for 6 months at most.
- **Sunlight:** long direct sunlight can damage the panel for good. The case doesn't shade or recess the display against it: that small risk is accepted.
- **Refresh:** a full refresh takes about 15 s and flickers. That's normal.

**Electrical facts that affect the case**

- **The print draws its power from the Pi's 5 V pins** (2 and 4). It switches its own supply on through pin 12 only while it refreshes ✅ (schematic).
- **With the print off, the header's bare pins are exposed.** These include 5 V (pins 2 and 4) and 3.3 V (pins 1 and 17). A key or coin could short them, and a finger could zap them with static.
- **A print put on one pin along, one row over or turned round** joins the 5 V pins to the wrong pins and can damage the Pi. See [what the case has to do](#what-the-case-has-to-do).
- **Taking a print off or putting one on while the camera is on** works in the software, but hasn't been tested electrically on this header 🧪.

## Battery board: PiSugar 3 Plus

Sources: [PiSugar docs](https://docs.pisugar.com/docs/product-wiki/battery/pisugar3/pisugar-3-series), [labelled board photo](https://cdn.pisugar.com/img/PiSugar3-5000mah.jpg), and PiSugar's own **[STEP model](https://github.com/PiSugar/PiSugar/blob/master/model_pro/pisugar3plus.STEP)** and [STL](https://github.com/PiSugar/PiSugar/blob/master/model_pro/pisugar3plus.STL) (folder `model_pro` in the [PiSugar repo](https://github.com/PiSugar/PiSugar)). Import the STEP into CAD. The model has the board and its parts, but not the battery.

The STEP model's own axes are: X 0 to 65, Y up (height 0 to 9.22), Z 0 to 57. Converting to the Pi frame: **x = 65 − X, y = Z − 1, and the board's top face (Y 7.22) sits at z = −t − 1.6.** The X axis is mirrored. That's checked: the spring pins then land exactly under header pins 2, 4, 6, 3, 5 and 39 (5 V, 5 V, GND, SDA, SCL, GND).

**Board** (✅ STEP, unless marked)

- **Outline:** 65 × 56 (the model is 57 deep, including the USB sockets sticking out 1.0 past the port edge). It lines up with the 3B+'s first 65 mm (x 0 to 65); the USB and Ethernet end (x 65 to 87) has nothing under it.
- **Thickness:** 1.0.
- **Posts:** four brass posts, ø5.5 × 1.6 high, M2.5 thread, under the Pi's four holes. The Pi sits on them, held with four M2.5 screws ✅ (TinyTronics listing).
- **Spring pins:** stand 0.4 above the posts, under the header at x 7.7 to 14.1, y 50.6 to 54.4 (pins 2 to 6) and at x 56.0 to 57.3, y 50.6 to 51.9 (pin 39).
- **Weight:** 96 g with the battery ✅ (TinyTronics listing).

**Parts on the underside** (Pi frame, ✅ STEP; named from PiSugar's photo)

| Part | x | y | Sticks out below the board | Notes |
|---|---|---|---|---|
| USB-C (charging) | 7.0 to 16.0 | −1.0 to 8.7 | 3.2 | The camera's charging port. Opens towards −y, right under the Pi's own power port |
| **BTN** (the shutter) | 19.3 to 24.1 | −0.1 to 3.0 | 1.6 | Side-press button, pressed towards +y from the port edge |
| **PWR** (on/off) | 27.8 to 32.5 | −0.1 to 3.0 | 1.6 | Side-press button, pressed towards +y |
| A row of five small parts | centres at x 38.1, 40.2, 42.3, 44.4, 46.4 | 1.25 to 3.4 | 0.6 | Each 1.3 × 2.15. The four battery-level LEDs are among them, shining down (−z) 📐 (check which) |
| micro-USB (second charging port) | 49.1 to 56.4 | −1.0 to 5.3 | 2.4 | Not needed. Can stay covered |
| Battery plug (PH2.0) | 0.5 to 5.9 | 38.8 to 46.8 | 6.2 | The lowest point on the board. The battery's lead plugs in here |
| Reset button | about 53 | about 15 | low | For emergencies only. Needs no access 📐 |
| 6-pin extension header (5VOUT, GND, MDAT, MSCL, SDAT, SSCL) | about 43 to 56 | about 38 | 🧪 | 2.54 pitch. Male pins may be fitted. Unused. Measure how far they stick out |
| Inductors and other large parts | x 1 to 14 / 38.7 to 57 | y 15 to 38 / 21 to 35 | 3.6 / 2.0 | Mostly flat. Keep the battery clear of them |

**Battery:** a 5000 mAh Li-Po pouch (4.2 V max, protection built in ✅ TinyTronics) under the board.

- Its size and thickness aren't published 🧪. Measure the board with the battery fitted.
- A 5000 mAh pouch is typically 8 to 10 thick 📐.

**Buttons and lights** (✅ PiSugar docs and the camera's software)

- **BTN is the camera's shutter:**
  - a tap takes a photo;
  - a double tap takes one photo, unless the camera is sharing;
  - a 2 s hold turns sharing on or off;
  - a double tap while sharing switches Wi-Fi.

  Its cap must be easy to find by touch, and it must press cleanly.
- **PWR:** a short press then a hold turns the camera on, and a long press turns it off. Its cap is best a little recessed, so it isn't pressed by accident.
- **The battery LEDs:** they count 1 to 4 when the camera turns on and show the charge level. They face down, near the port edge.

**Heat:** the charging chip can reach 50 to 60 °C charging at 2 A, and up to 80 °C at 3 A ✅ (PiSugar docs). Leave air around the underside, and keep it away from the battery and the e-paper.

## Camera module

The camera bought is the [Arducam 12MP IMX708 75° autofocus module](https://www.arducam.com/arducam-12mp-imx708-autofocus-camera-module-with-hdr-pdaf-for-raspberry-pi.html) ([Amazon B0C9PYCV9S](https://www.amazon.com/dp/B0C9PYCV9S)). Arducam's IMX708 boards use the Raspberry Pi Camera Module 3 outline. The drawing below is Raspberry Pi's [Camera Module 3 product brief](https://datasheets.raspberrypi.com/camera/camera-module-3-product-brief.pdf), page 4 ✅. Check the Arducam board against it 🧪, since its lens and connector may differ slightly.

- **Board:** 25.0 × 23.862 × 1.12.
- **Holes:** four ø2.2 holes (ø4.75 pads) for M2 screws. They're 2.0 in from the left and right edges, at heights 2.0 and 14.5 from the edge with the notch: a **21 × 12.5** pattern.
- **Lens:** centred across the board (12.5 from each side), level with the upper holes (14.5 up).
  - The lens block is 10.8 × 10.8.
  - The barrel is ø5.75 (standard) or ø6.95 (wide).
  - The lens front is 6.98 above the board's front face.
- **Overall depth:** 11.3 for the standard lens (12.0 wide), including the 15-pin connector on the back. Raspberry Pi lists it as 25 × 24 × 11.5.
- **Optics (75°, standard lens):**
  - Field of view: 66° horizontal × 41° vertical (75° diagonal).
  - Focal length 4.74 mm, f/1.8.
  - Focus from 10 cm to infinity.
  - The sensor's long side runs along the board's 25 mm width.
- **What the print shows:** the software crops the 16:9 frame to the print's 3:2, so the print shows about **57° × 41°** 📐 (calculated).
- **Mounting:**
  - Mount the camera with its 25 mm side parallel to the print's long side (the x axis), facing −z.
  - The setup script can flip the picture if the case holds it upside down.
  - The window in front of the lens must clear the field of view, plus a margin for the cropped corners.
- **Operating temperature:** 0 to 50 °C ✅.

## Camera cable

- **Type:** a 15-pin to 15-pin flat cable, 1.0 pitch, 16 wide ✅ (Raspberry Pi docs). The metal contacts face away from each connector's latch.
- **Length:** it depends on where the case puts the camera. Measure the route with a strip of paper before buying; 100, 150 and 200 mm are common 📐.
- **Bending:** the cable can be folded flat, but don't crease it hard across its contacts.
- **Route:** the CAMERA connector is at x 43.55 to 47.5, near the port edge, and the cable leaves it upwards (+z) into the gap under the print. The USB and Ethernet sockets fill the far end of that gap (x 65.65 to 87, y 2.5 to 53.6, up to 16 high), so the cable can't run over them: it has to leave the gap over a board edge. Keep it clear of the print's socket. If a fold would press into the print, make a channel for it.

## Status light

The camera's only light is the Pi's green **ACT** LED. The software drives it, and its kernel name is `ACT`. It has four looks:

| Look | Meaning |
|---|---|
| Solid | Ready |
| Slow blink (0.5 s on, 0.5 s off) | Wait |
| A 0.4 s wink off | A photo was taken |
| Off | The camera is off |

The red PWR LED next to it is turned off by the software on the bench Pi 4 ✅, but that isn't checked on a 3B+ yet: keep its light out of the opening 🧪.

- **Where:** the light comes out of the microSD edge (x ≈ 0), about 11.5 from the port edge, just above the board's top face (z ≈ 0 to 1). The red PWR LED is only about 3.5 away, at y ≈ 8.
- **What's above it:** the print covers the LEDs from above, from z ≈ 16 up. Its board edge and panel cable are at x −2.25. So the light has to come out beside the board edge, under the print, through the −x wall.
- **No light pipe:** there's no separate part and no second material, so the case's own printed wall does the job. Opaque plastic can't carry light round a corner, so the light needs a straight path to the outside 📐:
  - a small hole through the −x wall, in line with the LED;
  - or a wall thinned to a layer or two, which glows through in a light-coloured filament.
- **Keep the red LED out:** line the opening up with the green LED only, or put a printed rib between the two.
- **Nearby:** the microSD slot (y 22 to 34) and the Wi-Fi antenna (around y 35 to 39) are on the same edge.

## What the case has to do

**The print**

- **Guide the print onto the header** so it can only go on straight and the right way round, never a pin along or a row over. Its four mounting holes line up with the Pi's, so guide posts through them are one way to do this 🧪 (check the hole size).
- **Let the print be pulled straight off and pushed straight on,** with a grip that doesn't rock it, since rocking bends the pins. The print covers the whole Pi, so the grip has to be on the print's own enclosure.
- **Hold the print firmly while it's on**, without screws. The 40-pin socket's own grip may be enough, or too much to pull easily 🧪.
- **Protect the bare header when the print is off.** Recess the pins so a key or coin can't bridge them, and so a fingertip is less likely to touch them.
- **Make a display window** for the active area (x 7.0 to 83.2, y 2.6 to 53.4 in the Pi frame).

**The rest**

- **Status light:** a straight opening from the ACT LED to the outside ([above](#status-light)).
- **Button caps:** for **BTN** (the shutter: easy and clear to press) and **PWR** (recessed). Both buttons are on the PiSugar's underside at the port edge and are pressed towards +y.
- **Openings:**
  - the **PiSugar's USB-C** for charging;
  - the **four battery LEDs** on the PiSugar's underside (among the row of five small parts);
  - the **microSD slot**, reached from −x between the Pi and the PiSugar.

  Cover the Pi's own ports, the USB and Ethernet sockets included. None are used.
- **Camera:** a lens window, and printed clips or a pocket that hold the camera board square, with no screws. Its holes (the 21 × 12.5 pattern) can take printed pegs.
- **Air:** around the PiSugar's charging chip, and a little through the gap under the print where the Pi's SoC and USB chip sit.
- **Plastic, not metal,** near the Wi-Fi antenna corner. Keep any magnets away from it.
- **Mounting the Pi and PiSugar:** they come as one stack, joined by the four M2.5 screws that come with the PiSugar, through the Pi into its brass posts. The case holds that stack itself, for example by its board edges, with no extra hardware.
- **Prints:** each print has its own printed enclosure, separate from the camera body, so it pops out like a piece of film. Every print's enclosure fits the guide the same way.
- **Magnets:** each print's enclosure has somewhere to add small disc magnets, so a kept print can go on the fridge or any steel surface.
  - The magnets are optional: a print without them docks and works the same.
  - They must stay put without glue (loose neodymium magnets are dangerous if swallowed), and hold the print's 53 g plus its enclosure.
  - The tall socket on the back mustn't hold them too far off the steel.
  - Keep them away from the camera module (its autofocus uses a magnet and a coil) and the Wi-Fi antenna corner.

## A layout that fits

Notes for the CAD, not requirements. Everything here is 📐.

- **The stack:** PiSugar and battery at the bottom (front), the Pi, then the print in its enclosure on top (back). The display faces the user, and the lens faces the scene.
- **The camera:** the 3A+ layout put it beside the Pi, under the print's overhang. On the 3B+ the USB and Ethernet sockets fill that space, so the camera needs another place. What's free: under the Pi's far end (x 65 to 87, below the board), beside the PiSugar and its battery, and outside the board's edges.
- **The status light:** a straight opening in the −x wall, level with the LED, under the print.
- **The buttons, USB-C and battery LEDs:** all on the port-edge wall (−y), low down at the PiSugar's level, BTN and PWR side by side.
- **Body size:** at least the print's 89.5 × 59 plus walls, about 94 × 63 in plan with 2 mm walls. Its depth is about 16 (print gap) + 2.4 (print) + t + 8.8 (PiSugar) + the battery + walls, around 40 to 45 in all 🧪.

## Measure before you finalise

| What | Why |
|---|---|
| The print's socket body height, and the gap between the Pi's board and the print's board when fully seated | Sets the camera's depth, the body's thickness and how far the socket stands off a fridge |
| How far the print can be left off the header and still work (contact engagement), and the force to pull it off | The pull-off grip and how the print is held |
| The print's board thickness and its mounting-hole diameter | Guide posts and how each print's enclosure fits |
| The Pi's board thickness `t` | Every z below the Pi |
| The PiSugar battery's length, width and thickness, how it's attached, and where its lead runs | The bottom of the stack |
| How far the PiSugar's extension header pins stick out, if fitted | Clearance under the PiSugar |
| The Arducam board: outline, holes, lens centre, depth, and which edge its connector is on | The camera mount and lens window |
| The ACT and PWR LED positions on the actual 3B+, and whether the PWR LED stays off | The light's opening |
| Whether the seated print clears the 3B+'s 16.0 mm USB stacks, or rests on them | The print's height and the gap |
| How far the USB and Ethernet sockets' pins stick out under the board | Anything under the far end |
| BTN and PWR travel and press force through a printed cap | The cap design |
| Which of the PiSugar's five small parts are the LEDs | The light openings |
| Whether a print can be taken off and put on with the camera on, with no reset | Whether the case must ask the user to turn it off first |

## Other boards (not for this case)

The case is for the 3B+ only. The software also runs on these boards, so this is a head start for anyone designing a case for one of them. Everything else in this guide (the print, the PiSugar, the camera, the hole pattern, the header and the coordinate frame) is the same on all of them ✅.

**Pi 3B**

- **Board:** the same footprint as the 3B+ ✅ ([3B+ product brief](https://pip.raspberrypi.com/documents/RP-008338-DS)), so the case should fit it 📐. Its USB and Ethernet chip is the LAN9514, which the camera also switches off.
- **Wi-Fi:** 2.4 GHz only.

**Pi 3A+** (the build's board until 2026-10-19)

Sources: the [mechanical drawing (RP-008330-DS)](https://pip.raspberrypi.com/documents/RP-008330-DS), the [schematic (RP-008332-DS)](https://pip.raspberrypi.com/documents/RP-008332-DS) and the [official case drawing (RP-008329-DS)](https://pip.raspberrypi.com/documents/RP-008329-DS). 3D models: [Raspberry Pi Model 3 A+](https://grabcad.com/library/raspberry-pi-model-3-a-1) (from a 3D scan) and [Raspberry Pi 3 Model A+](https://grabcad.com/library/raspberry-pi-3-model-a-1) on GrabCAD.

- **Board:** 65 × 56, corner radius 3 ✅: the 3B+ without its far end. Holes, header, port edge, CAMERA connector and LEDs are where they are on the 3B+ ✅.
- **USB-A:** one, at about x 53.4 to 67.7, y 25.0 to 38.0, 7.0 high ✅, sticking out about 2.7 past x = 65 📐. No Ethernet.
- **With the PiSugar 3 Plus:** the PiSugar's outline matches the board exactly.
- **The print** reaches 22.25 past its far edge. That overhang leaves room beside the Pi for the camera.

**Pi 4B** (the bench camera)

All ✅ from the [PDF drawing (RP-008343-DS)](https://pip.raspberrypi.com/documents/RP-008343-DS) or the [DXF (RP-008342-DS)](https://pip.raspberrypi.com/documents/RP-008342-DS), which is a full PCB assembly export with part outlines and silkscreen. [Schematic (RP-008345-DS)](https://pip.raspberrypi.com/documents/RP-008345-DS).

- **Board:** 85 × 56, corner radius 3. Holes, header and LEDs are exactly as on the 3B+.
- **Ports along the port edge (y = 0):**
  - USB-C power: x 11.2, height 3.2.
  - micro-HDMI 0 and 1: x 26.0 and 39.5, height 3.0.
  - Audio jack: x 54.0, y 0 to 12.5, height 6.0.
- **CAMERA connector:** x 45.05 to 49.00, y 0.3 to 22.7, height 5.5.
- **DISPLAY connector:** x 1.5 to 5.45, y 16.8 to 39.2.
- **The far end (x 66.65 to 88.0):** these stick out 3 past x = 85 and sit under the print, as on the 3B+ but with the Ethernet at the other corner. Waveshare's photos show the print just above them.
  - Ethernet: y 38.0 to 53.5, height 13.5.
  - USB 3: y 20.4 to 34.3, height 16.0.
  - USB 2: y 1.7 to 15.6, height 16.0.
  - PoE header: 5 × 5, centred at (61.5, 46.4).
- **microSD card:** underneath, x −2.27 to 13.1, y 22.1 to 34.1.
- **With the PiSugar 3 Plus:** the PiSugar covers only x 0 to 65. The USB/Ethernet end has no battery board under it.

**Pi 5**

The least checked. Source: the [mechanical drawing (RP-008347-DS)](https://pip.raspberrypi.com/documents/RP-008347-DS), whose dimensions Raspberry Pi notes are approximate.

- **Board, holes and header:** 85 × 56. The holes (ø2.7) and the header are where they are on the other boards ✅.
- **Ports along the port edge:** USB-C at x 11.2, micro-HDMI at x 25.8 and 39.2 ✅.
- **The far end:** Ethernet at y 10.2, and USB stacks at y 29.1 and 47, swapped compared with the Pi 4. They stick out 3 past x = 85 ✅.
- **Two 22-pin camera/display connectors** (0.5 pitch, 11.5 wide ✅): about x 47 to 50 and 53 to 56, y 0.7 to 16 📐. The camera needs a 22-pin (0.5 pitch, 11.5 wide) to 15-pin cable ✅.
- **Power button and status LED, both at the microSD edge:** the button is centred near y 18.4, and the LED (one LED, red or green) near y 13.3 📐 from the drawing. The light's opening has to line up with this LED instead.
- **Cooling:** the Active Cooler would sit under the print. Whether it clears the print hasn't been checked 🧪.
- **The PiSugar:** its spring pins need lining up carefully, since the Pi 5's header holes are smaller 🧪 ([PiSugar #144](https://github.com/PiSugar/PiSugar/issues/144)).

## Sources

**Raspberry Pi** (all © Raspberry Pi Ltd)

- [Raspberry Pi 3B+ product page](https://www.raspberrypi.com/products/raspberry-pi-3-model-b-plus/): production until at least January 2030 (the October 2025 product brief still says January 2028)
- [Raspberry Pi 3B+ DXF (RP-008336-DS)](https://pip.raspberrypi.com/documents/RP-008336-DS): a full assembly drawing with part outlines and silkscreen, the source of the 3B+ positions
- [Raspberry Pi 3B+ mechanical drawing, PDF (RP-008337-DS)](https://pip.raspberrypi.com/documents/RP-008337-DS): the heights
- [Raspberry Pi 3B+ product brief (RP-008338-DS)](https://pip.raspberrypi.com/documents/RP-008338-DS), [reduced schematics (RP-008339-DS)](https://pip.raspberrypi.com/documents/RP-008339-DS) and [case drawing (RP-008160-DS)](https://pip.raspberrypi.com/documents/RP-008160-DS)
- [Raspberry Pi 3A+ product page](https://www.raspberrypi.com/products/raspberry-pi-3-model-a-plus/): specifications, and production until at least January 2030
- [Raspberry Pi 3A+ mechanical drawing, PDF (RP-008330-DS)](https://pip.raspberrypi.com/documents/RP-008330-DS)
- [Raspberry Pi 3A+ schematic (RP-008332-DS)](https://pip.raspberrypi.com/documents/RP-008332-DS)
- [Raspberry Pi 3A+ case drawing (RP-008329-DS)](https://pip.raspberrypi.com/documents/RP-008329-DS)
- [Raspberry Pi 4B mechanical drawing, PDF (RP-008343-DS)](https://pip.raspberrypi.com/documents/RP-008343-DS)
- [Raspberry Pi 4B DXF (RP-008342-DS)](https://pip.raspberrypi.com/documents/RP-008342-DS): a full assembly drawing with part outlines and silkscreen. The best source for exact positions, and the hole, header, LED and microSD details the 3A+ drawing leaves out
- [Raspberry Pi 4B schematic (RP-008345-DS)](https://pip.raspberrypi.com/documents/RP-008345-DS)
- [Raspberry Pi 5 mechanical drawing (RP-008347-DS)](https://pip.raspberrypi.com/documents/RP-008347-DS)
- [Board documentation](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html), including the connector specifications ([source on GitHub](https://github.com/raspberrypi/documentation/blob/develop/documentation/asciidoc/computers/sbcs/sbc-models.adoc))
- [HAT+ specification](https://datasheets.raspberrypi.com/hat/hat-plus-specification.pdf): ID chip pull-ups and mechanical guidance
- [Camera Module 3 product brief](https://datasheets.raspberrypi.com/camera/camera-module-3-product-brief.pdf): the camera's drawing and optics

**Waveshare**

- [3.6" e-Paper HAT+ (E) wiki](https://www.waveshare.com/wiki/3.6inch_e-Paper_HAT+_(E)_Manual)
- [Product page](https://www.waveshare.com/3.6inch-e-paper-hat-plus-e.htm?sku=32650) ([Amazon B0G656R3M9](https://www.amazon.com/dp/B0G656R3M9))
- [Board size drawing](https://www.waveshare.com/img/devkit/LCD/3.6inch-e-Paper-E/3.6inch-e-Paper-HAT-plus-E-details-size.jpg)
- [Bare panel size drawing](https://www.waveshare.com/img/devkit/LCD/3.6inch-e-Paper-E/3.6inch-e-Paper-E-details-size.jpg)
- [Panel datasheet, with the outline drawing on page 7](https://files.waveshare.com/wiki/3.6inch_e-Paper_HAT%2B_E/3.6inch_e-Paper_HAT%2B.pdf)
- [Driver board schematic](https://files.waveshare.com/wiki/3.6inch_e-Paper_HAT%2B_E/3.6inch_e-Paper_HAT%2B_Schematic.pdf)

**PiSugar**

- [PiSugar 3 series docs](https://docs.pisugar.com/docs/product-wiki/battery/pisugar3/pisugar-3-series)
- [Labelled board photo](https://cdn.pisugar.com/img/PiSugar3-5000mah.jpg)
- [PiSugar 3 Plus STEP](https://github.com/PiSugar/PiSugar/blob/master/model_pro/pisugar3plus.STEP) and [STL](https://github.com/PiSugar/PiSugar/blob/master/model_pro/pisugar3plus.STL)
- [TinyTronics listing](https://www.tinytronics.nl/nl/power/powerbanks-en-batterijmodules/pisugar-3-plus-ups-batterij-module-voor-raspberry-pi): weight, battery chemistry, M2.5 screws

**Arducam**

- [12MP IMX708 autofocus module](https://www.arducam.com/arducam-12mp-imx708-autofocus-camera-module-with-hdr-pdaf-for-raspberry-pi.html) ([Amazon B0C9PYCV9S](https://www.amazon.com/dp/B0C9PYCV9S))

**Checked on the bench camera** (Raspberry Pi 4B, 2026-10-17; nothing has been checked on a 3B+ yet)

- The print works seated straight on the header.
- Pin 27 reads high with the print on.
- The ACT LED shows the light's patterns, and the PWR LED stays off.
- BTN taps take photos.

# Share mode on phones: manual checklist (SPEC.md §19.5, §19.7)

Run each column on one iPhone (iOS 17 or later) and one Android phone (Android 12 or later).
Write the result (✓, ✗ and a note) in each cell, and keep the filled-in copy with the release notes.

Before you start: the camera has at least 5 photos, a module is in, the battery is above 30 %,
and `inkstant-ctl status` shows no errors.

## Away (the camera's own Wi-Fi)

| # | Step | Expected | iPhone | Android |
|---|---|---|---|---|
| 1 | Tap BTN once, away from home Wi-Fi | The light shows "share"; the screen shows the Wi-Fi QR code and the camera's address within about 30 s | | |
| 2 | Scan the Wi-Fi QR code with the phone's camera app from 15, 20 and 30 cm | The phone offers to join `Inkstant-XXXX` at each distance | | |
| 3 | Join | Joins without typing the password; no "no internet" prompt blocks it | | |
| 4 | Scan the second QR code (the address) | The gallery opens at `http://10.42.0.1` | | |
| 5 | With mobile data **on**, open `http://10.42.0.1` by hand | The gallery opens (if not on Android, note it: §9 fallback) | | |
| 6 | Grid, viewer, swipe, info sheet | Photos load; EXIF shows date and exposure | | |
| 7 | Download one photo | Saved to Photos (iPhone) / Downloads or Gallery (Android) | | |
| 8 | Select 3 and download as ZIP | The ZIP downloads and opens; 3 full-size JPEGs | | |
| 9 | "Show on camera" on an older photo | The camera's screen develops that photo | | |
| 10 | Delete one photo | It disappears from the grid and from the slideshow | | |
| 11 | Settings: change quiet hours, save, reload | The change is kept | | |
| 12 | Leave the phone idle for the share timeout | Share mode stops; the hotspot disappears; the screen returns to the photo | | |

## At home (the home Wi-Fi)

| # | Step | Expected | iPhone | Android |
|---|---|---|---|---|
| 13 | Tap BTN once at home | The screen shows only the address QR code and "On <home network>" | | |
| 14 | Scan it on a phone on the same Wi-Fi | The gallery opens at the camera's home address | | |
| 15 | Take a photo while sharing | The new photo appears in the gallery within a few seconds | | |
| 16 | Tap BTN again | Share mode stops; the screen goes back to the last photo | | |
| 17 | Double-tap BTN at home | The camera's own Wi-Fi and its QR code appear even at home | | |

## Notes

- Battery before / after 10 minutes of sharing: ____ % / ____ %
- Anything odd:

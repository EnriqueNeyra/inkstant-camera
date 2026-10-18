# Share mode on phones: manual checklist

Run each column on one iPhone (iOS 17 or later) and one Android phone (Android 12 or later).
Write the result (✓, ✗ and a note) in each cell, and keep the filled-in copy with the release notes.

Before you start: the camera has at least 5 photos, a module is in, the battery is above 30 %,
and `inkstant-ctl status` shows no errors.

## Away (the camera's own Wi-Fi)

| # | Step | Expected | iPhone | Android |
|---|---|---|---|---|
| 1 | Hold BTN for 2 s, away from home Wi-Fi | The light blinks slowly; the screen shows the Wi-Fi QR code and the camera's address within about 30 s | | |
| 2 | Scan the Wi-Fi QR code with the phone's camera app from 15, 20 and 30 cm | The phone offers to join `Inkstant-XXXX` at each distance | | |
| 3 | Join | Joins without typing the password; no "no internet" prompt blocks it | | |
| 4 | Scan the second QR code (the address) | The gallery opens at `http://10.42.0.1` | | |
| 5 | With mobile data **on**, open `http://10.42.0.1` by hand | The gallery opens (if not, note which phone) | | |
| 6 | Grid, viewer, swipe, info sheet, the Photo / Print switch | Photos load; EXIF shows date and exposure; Print shows the photo in the display's six colours | | |
| 7 | Download one photo | Saved to Photos (iPhone) / Downloads or Gallery (Android) | | |
| 8 | Select 3 and download as ZIP | The ZIP downloads and opens; 3 full-size JPEGs | | |
| 9 | "Show on camera" on an older photo | The camera's screen develops that photo | | |
| 10 | Delete one photo | It disappears from the grid and from the slideshow | | |
| 11 | Settings: change quiet hours, save, reload | The change is kept | | |
| 12 | Leave the phone idle for the share timeout | Share mode stops; the hotspot disappears; the screen returns to the photo | | |

## At home (the home Wi-Fi)

| # | Step | Expected | iPhone | Android |
|---|---|---|---|---|
| 13 | Hold BTN for 2 s at home | The screen shows only the address QR code and "On <home network>" | | |
| 14 | Scan it on a phone on the same Wi-Fi | The gallery opens at the camera's home address | | |
| 15 | Tap BTN while sharing | No photo is taken: no wink, and the light keeps blinking slowly | | |
| 16 | Double-tap BTN while sharing at home | The camera switches to its own Wi-Fi and shows its two QR codes; a second double tap goes back to home Wi-Fi | | |
| 17 | Hold BTN for 2 s | Share mode stops; the screen goes back to the last photo, and taps take photos again | | |

## Notes

- Battery before / after 10 minutes of sharing: ____ % / ____ %
- Anything odd:

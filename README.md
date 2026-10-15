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
- Mill-Max Maxnetic 10-pin magnetic connector, so the print slides in and out
- 3D-printed case

## Software

Planned:

- Raspberry Pi OS Lite.
- A Python app using Picamera2 for the camera.
- A web gallery for viewing and downloading photos.
- One setup script that installs and configures everything.

## Status

In the design stage. Next come the parts and bench tests, then the software and the case.

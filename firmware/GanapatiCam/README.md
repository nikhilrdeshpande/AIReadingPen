# GanapatiCam — XIAO ESP32-S3 Sense live-stream firmware

Firmware for the Seeed XIAO ESP32-S3 Sense (OV3660 sensor) that streams MJPEG over Wi-Fi to a
laptop, where the AI worker runs. Built and flashed with arduino-cli (`./flash.sh`).

## Files
- `camera.cpp` — the firmware (camera init, flicker fix, quality defaults, HTTP server, serial commands)
- `GanapatiCam.ino` — two-line entry point
- `flash.sh` — compile + flash + serial monitor
- `usb_feed.py` — view/grab frames over USB, no Wi-Fi needed (`--save`, `--avg`, `--cmd`)
- `laptop_stream.py` — laptop-side MJPEG client: `frames(url)` generator, `snapshot(url)`, `set_control(url, var, val)`

## Current network setup (2026-09-13)
Home network address was http://192.168.29.78/ (see wifi_secrets.h, git-ignored). Ask the router for a DHCP reservation for MAC
90:70:69:10:96:A0 so it stays fixed. The u.FL antenna MUST be clipped on: without it the signal is
-93 dBm and nothing works; with it -50 to -63 dBm.

## Setting up for the event
1. In `camera.cpp` set `WIFI_SSID` / `WIFI_PASS` to the laptop's network (a phone hotspot or the
   laptop's own hotspot both work). Keep `MAINS_HZ = 50`.
2. `./flash.sh` — the boot log prints `FEED: http://<ip>/`. Give the camera a fixed IP / DHCP
   reservation on the router so the laptop always finds it.
3. On the laptop: `python3 laptop_stream.py http://<ip>` to check, then import `frames()` in the worker.

## HTTP endpoints
| URL | Purpose |
|---|---|
| `:81/stream` | MJPEG live stream on **port 81** (own server task, so it never blocks the pages below). `/stream` on port 80 redirects there. |
| `:82/stream` | second independent stream slot; each port serves one viewer at a time (laptop worker on 81, phone check on 82) |
| `/capture` | one full-quality JPEG at the current size |
| `/control?var=<name>&val=<n>` | change a sensor setting live (names below) |
| `/status` | JSON: chip temperature, stream fps, exposure, gain, free memory |

Settings for `/control` and the serial `x` command: brightness, contrast, saturation (-2..2),
sharpness (-3..3), denoise (0..8), ae_level (-2..2), gain_ceiling (1..16), quality (4..63),
framesize (see table), vflip, hmirror, wb_mode (0 auto,1 sunny,2 cloudy,3 office,4 home),
cpu (80/160/240), xclk (10..20), txpower (8..78, x0.25 dBm), wifi (0/1).

## Frame sizes (framesize enum values) measured on this board
| value | size | sensor fps | notes |
|---|---|---|---|
| 11 | SVGA 800x600 | ~28 | binned (2x2), cleanest low-light, smallest frames |
| 12 | XGA 1024x768 | ~28 | binned — **default live stream** (lowest grain, 28 fps) |
| 13 | HD 1280x720 | ~17 | full-res crop, 16:9; needs ~4x more gain than XGA = grainier in dim light |
| 14 | SXGA 1280x1024 | ~14 | full-res |
| 15 | UXGA 1600x1200 | ~14 | full-res, good for the final photo |
| 19 | QXGA 2048x1536 | ~15 | sensor maximum; JPEG can overflow the buffer in dark/noisy scenes |

Frame bytes depend heavily on light: in a lit room HD is ~50 kB and UXGA ~100 kB; in a dark room at
maximum gain the noise makes them 3-4x larger. Wi-Fi comfortably carries ~1 MB/s of MJPEG, so HD at
15 fps or UXGA at ~8 fps are safe. FHD 1920x1080 overflowed the buffer in the dark — avoid.

## Flicker (LED banding) fix
LED lights on 50 Hz mains pulse at 100 Hz. The sensor's banding filter only works if its "band step"
matches the real line rate; the esp32-camera driver leaves it at a wrong value (98 lines). The
firmware measures the line rate at boot and re-programs the step (218 lines at 4:3 sizes, 230 at
16:9), and again after every frame-size change. Result: exposure locks to exact multiples of 10 ms.

## Heat (measured, USB-streaming continuously, no heatsink, ~26 C room)
| condition | chip temperature |
|---|---|
| hotspot (AP) mode on, 240 MHz | 68-69 C (long sessions drift to ~75-85 C) |
| Wi-Fi radio off | 51-52 C |
| + CPU 160 MHz | 49 C |
| + camera clock 10 MHz | 46 C (frame rate halves) |
| AP on, TX power 8.5 dBm | no improvement |
| **Event setup: station mode on home Wi-Fi, heatsink fitted, 5V USB-A charger, 1-2 viewers streaming 13 fps** | **52-54 C steady over 10 min** |

The radio is the main heat source, the CPU clock is second. For the event: run in station mode
(joins your network; the radio idles between packets, unlike AP mode which is always on),
`x cpu 160` costs nothing for MJPEG streaming, keep `MAX_STREAM_FPS` at 10-15, stick a small
heatsink on the ESP32-S3 can, and don't box it in — it needs airflow. The chip is rated to 105 C;
it throttles nothing by itself, but the sensor gets noisier when hot.

## Grain / noise
Grain is sensor gain. In a dim room HD needs 10-15x gain (its maximum) and looks grainy; the binned
4:3 sizes (SVGA/XGA) need ~4x less gain for the same brightness and average 4 pixels per output
pixel, so they are far cleaner. Order of remedies: more light on the visitor > XGA instead of HD >
`denoise 8` (halves grain, softens detail) > `gain_ceiling 6` (cleaner but darker; let the AI
worker brighten) > `--avg` / multi-frame averaging on the laptop for the final photo.

## Known driver bugs worked around in camera.cpp
- `set_gainceiling()` writes the enum into the 10-bit register: image goes black. We write 0x3a18/19 directly.
- banding step never recomputed for the JPEG PLL (see above).
- frame buffers are sized once at init: we init at QXGA so every size fits, then switch to XGA.
- switching between 16:9 (HD/FHD) and 4:3 sizes at runtime leaves the sensor ~7x less sensitive
  (XGA needed 15.5x gain after HD vs 2.25x after a clean boot). The firmware re-initialises the
  camera whenever the aspect ratio changes. Staying within 4:3 (XGA live, UXGA stills) avoids it entirely.

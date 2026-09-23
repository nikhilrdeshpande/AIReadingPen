#!/bin/sh
# Compile and flash GanapatiCam to the XIAO ESP32-S3 Sense, then show its boot log.
# Edit WIFI_SSID / WIFI_PASS in camera.cpp first if you want it on your home Wi-Fi.
set -e
cd "$(dirname "$0")"
PORT=$(ls /dev/cu.usbmodem* | head -1)
arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3:PSRAM=opi .
arduino-cli upload  --fqbn esp32:esp32:XIAO_ESP32S3:PSRAM=opi --port "$PORT" .
echo "--- boot log (Ctrl-C to quit) ---"
arduino-cli monitor --port "$PORT" --config baudrate=115200

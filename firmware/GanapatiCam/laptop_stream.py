#!/usr/bin/env python3
"""Laptop-side client for the GanapatiCam MJPEG stream. No OpenCV needed (numpy + Pillow only).

  python3 laptop_stream.py http://192.168.1.50            # print fps + save a frame every 2 s to frames/
  python3 laptop_stream.py http://localhost:8080          # works against the USB viewer too

Use frames(url) as a generator in your AI worker:

    from laptop_stream import frames
    for img, jpg in frames("http://<camera-ip>"):      # img = HxWx3 uint8 numpy (RGB), jpg = raw bytes
        ...run detection on img...

With OpenCV installed the one-liner alternative is:
    cap = cv2.VideoCapture("http://<camera-ip>:81/stream")
"""
import io, sys, time, os, urllib.request
import numpy as np
from PIL import Image

def frames(base_url, timeout=10):
    """Yield (numpy RGB image, jpeg bytes) forever; reconnects automatically if the stream drops."""
    url = base_url.rstrip('/').rsplit(':', 1)[0] if base_url.count(':') > 1 else base_url.rstrip('/')
    url = url + ':81/stream'     # the stream lives on its own server on port 81
    while True:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                buf = b''
                while True:
                    chunk = resp.read(16384)
                    if not chunk: break
                    buf += chunk
                    while True:
                        a = buf.find(b'\xff\xd8'); b = buf.find(b'\xff\xd9', a + 2) if a >= 0 else -1
                        if a < 0 or b < 0: break
                        jpg = buf[a:b + 2]; buf = buf[b + 2:]
                        try:
                            yield np.asarray(Image.open(io.BytesIO(jpg)).convert('RGB')), jpg
                        except Exception:
                            pass   # corrupt frame, skip
        except Exception as e:
            print(f"stream error: {e}; reconnecting in 2 s", file=sys.stderr)
            time.sleep(2)

def snapshot(base_url, timeout=10):
    """One full-quality JPEG from /capture (use this for the final blessing photo)."""
    with urllib.request.urlopen(base_url.rstrip('/') + '/capture', timeout=timeout) as r:
        return r.read()

def set_control(base_url, var, val, timeout=5):
    """Change a sensor setting live, e.g. set_control(url, 'framesize', 13) or ('brightness', 1)."""
    with urllib.request.urlopen(f"{base_url.rstrip('/')}/control?var={var}&val={val}", timeout=timeout) as r:
        return r.read().decode()

if __name__ == '__main__':
    base = sys.argv[1] if len(sys.argv) > 1 else 'http://192.168.4.1'
    os.makedirs('frames', exist_ok=True)
    n = 0; t0 = time.time(); last_save = 0
    for img, jpg in frames(base):
        n += 1
        if time.time() - last_save > 2:
            open(f'frames/frame_{int(time.time())}.jpg', 'wb').write(jpg); last_save = time.time()
        if n % 30 == 0:
            print(f"{n / (time.time() - t0):.1f} fps  {img.shape[1]}x{img.shape[0]}  {len(jpg)/1024:.0f} kB")

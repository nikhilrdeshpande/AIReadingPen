#!/usr/bin/env python3
"""Show the GanapatiCam feed over USB - no Wi-Fi needed.

  python3 usb_feed.py            # live feed at http://localhost:8080  (open in browser)
  python3 usb_feed.py --save x.jpg   # grab one frame to a file and exit
  python3 usb_feed.py --save x.jpg --avg 8   # average 8 frames: less noise and flicker, best for stills
  python3 usb_feed.py --smooth 3     # live feed with 3-frame rolling average (steadier, slight motion blur)
  python3 usb_feed.py --cmd "i"      # send a command to the board (i, f, r/w reg, x name value)

Needs: pip3 install pyserial
"""
import glob, sys, time, threading, serial
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

PORT = (glob.glob('/dev/cu.usbmodem*') or ['/dev/cu.usbmodem1101'])[0]
ser = serial.Serial(PORT, 115200, timeout=2)
lock = threading.Lock()

def grab():
    with lock:
        ser.reset_input_buffer()
        ser.write(b'c')
        line = ser.readline()
        while line and not line.startswith(b'FRAME'):
            line = ser.readline()
        if not line:
            return None
        n = int(line.split()[1])
        return ser.read(n) if n else None

def argval(flag, default):
    return int(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default

def average_frames(n, quality=95):
    """Grab n frames and average them - a software layer that cancels residual flicker and sensor noise."""
    import io, numpy as np
    from PIL import Image
    acc = None; k = 0
    for _ in range(n):
        jpg = grab()
        if not jpg: continue
        a = np.asarray(Image.open(io.BytesIO(jpg)).convert('RGB'), dtype=np.float32)
        acc = a if acc is None else acc + a; k += 1
    if not k: return None
    out = io.BytesIO()
    Image.fromarray((acc / k).round().astype('uint8')).save(out, 'JPEG', quality=quality)
    return out.getvalue()

if '--cmd' in sys.argv:
    # send one command line to the board and print its reply, e.g.  --cmd "i"  or  --cmd "x brightness 1"
    cmd = sys.argv[sys.argv.index('--cmd') + 1]
    ser.reset_input_buffer(); ser.write(cmd.encode() + b'\n')
    time.sleep(0.3 if not cmd.startswith('f') else 2.5)
    sys.stdout.write(ser.read(ser.in_waiting or 1).decode('utf-8', 'replace'))
    sys.exit(0)

if '--save' in sys.argv:
    out = sys.argv[sys.argv.index('--save') + 1]
    n = argval('--avg', 1)
    jpg = average_frames(n) if n > 1 else grab()
    if not jpg: sys.exit("no frame received from " + PORT)
    open(out, 'wb').write(jpg)
    print(f"saved {len(jpg)} bytes to {out}")
    sys.exit(0)

PAGE = (b"<!doctype html><title>GanapatiCam USB</title><body style='margin:0;background:#111;text-align:center;color:#eee;font-family:sans-serif'>"
        b"<h3>GanapatiCam (USB)</h3><img src='/stream' style='max-width:100%'></body>")

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == '/stream':
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.end_headers()
            smooth = argval('--smooth', 1)
            if smooth > 1:
                import io, numpy as np
                from PIL import Image
            hist = []
            try:
                while True:
                    jpg = grab()
                    if not jpg: time.sleep(0.2); continue
                    if smooth > 1:
                        hist.append(np.asarray(Image.open(io.BytesIO(jpg)).convert('RGB'), dtype=np.float32))
                        hist = hist[-smooth:]
                        buf = io.BytesIO()
                        Image.fromarray((sum(hist) / len(hist)).round().astype('uint8')).save(buf, 'JPEG', quality=85)
                        jpg = buf.getvalue()
                    self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n' % len(jpg))
                    self.wfile.write(jpg + b'\r\n')
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif self.path == '/capture':
            jpg = grab() or b''
            self.send_response(200 if jpg else 500)
            self.send_header('Content-Type', 'image/jpeg'); self.end_headers()
            self.wfile.write(jpg)
        else:
            self.send_response(200); self.send_header('Content-Type', 'text/html'); self.end_headers()
            self.wfile.write(PAGE)

print(f"reading camera on {PORT}; open http://localhost:8080  (Ctrl-C to stop)")
ThreadingHTTPServer(('127.0.0.1', 8080), H).serve_forever()

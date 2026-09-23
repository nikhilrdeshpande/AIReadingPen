#include <Arduino.h>
// GanapatiCam - camera test + live feed for Seeed XIAO ESP32-S3 Sense
//
// Boots the OV2640 camera, then serves:
//   http://<ip>/          simple page with live stream
//   http://<ip>:81/stream raw MJPEG stream (open in browser / VLC / OpenCV) - note port 81
//   http://<ip>:82/stream second independent stream slot (one viewer per port)
//   http://<ip>/capture   single JPEG snapshot
//   http://<ip>/control?var=brightness&val=1   change a sensor setting live
//   http://<ip>/status    JSON: chip temperature, stream fps, exposure, gain, memory
// and also answers frame requests over the USB serial port (see camLoop / usb_feed.py).
//
// Wi-Fi: fill in WIFI_SSID / WIFI_PASS to join your home network.
// If left empty (or the join fails) the board starts its own hotspot
// "GanapatiCam" (password ganapati123) at http://192.168.4.1

#include "esp_camera.h"
#include <WiFi.h>
#include "esp_http_server.h"
#include <math.h>

// ---- Wi-Fi settings ----
#include "wifi_secrets.h"   // defines WIFI_SSID / WIFI_PASS; copy wifi_secrets.example.h and edit (git-ignored)
const char* AP_SSID   = "GanapatiCam";
const char* AP_PASS   = "ganapati123";

// ---- Image quality settings ----
const int MAINS_HZ = 50;   // 50 for India/Europe, 60 for USA/Japan. Used for LED/tube-light flicker removal.
const framesize_t STREAM_SIZE = FRAMESIZE_XGA;  // 1024x768 binned: ~28 fps, half the grain of HD in dim light. Use /control?var=framesize&val=15 (UXGA) for stills.
const int MAX_STREAM_FPS = 15;   // cap for /stream. The sensor runs ~28 fps; 15 is smooth to the eye and roughly halves work and heat.

// ---- XIAO ESP32-S3 Sense camera pins ----
#define PWDN_GPIO_NUM  -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM  10
#define SIOD_GPIO_NUM  40
#define SIOC_GPIO_NUM  39
#define Y9_GPIO_NUM    48
#define Y8_GPIO_NUM    11
#define Y7_GPIO_NUM    12
#define Y6_GPIO_NUM    14
#define Y5_GPIO_NUM    16
#define Y4_GPIO_NUM    18
#define Y3_GPIO_NUM    17
#define Y2_GPIO_NUM    15
#define VSYNC_GPIO_NUM 38
#define HREF_GPIO_NUM  47
#define PCLK_GPIO_NUM  13

httpd_handle_t server = NULL;         // port 80: page, /capture, /control, /status
httpd_handle_t streamServer[2] = {NULL, NULL};   // ports 81 and 82: /stream, one viewer each, own tasks so they never block the pages
void fixBandingFilter(sensor_t* s, bool measure);
bool initCamera(framesize_t size);

// ---- stream statistics (for /status and the 't' serial command) ----
static uint32_t g_framesStreamed = 0, g_bytesStreamed = 0, g_streamStartMs = 0, g_lastFrameMs = 0;
static int g_streamClients = 0;
static int g_grabFailures = 0;      // consecutive esp_camera_fb_get() failures; camera is re-initialised after 5
static bool g_useHomeWifi = false;  // true when joined to WIFI_SSID (enables auto-reconnect)
static framesize_t g_size = FRAMESIZE_INVALID;   // current frame size
static float g_fpsNow = 0;

// Why the chip last (re)started - tells a power blip from a crash
static const char* resetReasonText() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON:   return "power-on";
    case ESP_RST_SW:        return "software";
    case ESP_RST_PANIC:     return "crash (panic)";
    case ESP_RST_INT_WDT:   return "crash (interrupt watchdog)";
    case ESP_RST_TASK_WDT:  return "crash (task watchdog)";
    case ESP_RST_WDT:       return "watchdog";
    case ESP_RST_BROWNOUT:  return "brownout (power dip)";
    case ESP_RST_DEEPSLEEP: return "deep sleep";
    case ESP_RST_EXT:       return "external reset";
    default:                return "unknown";
  }
}

static void statusText(char* buf, size_t n) {
  float fps = (g_streamClients && g_streamStartMs) ? g_framesStreamed * 1000.0f / (millis() - g_streamStartMs) : 0;
  sensor_t* s = esp_camera_sensor_get();
  uint32_t exp16 = ((uint32_t)s->get_reg(s,0x3500,0xff) << 16) | (s->get_reg(s,0x3501,0xff) << 8) | s->get_reg(s,0x3502,0xff);
  uint16_t gain  = ((s->get_reg(s,0x350a,0xff) & 3) << 8) | s->get_reg(s,0x350b,0xff);
  snprintf(buf, n,
    "{\"temp_c\":%.1f,\"uptime_s\":%lu,\"stream_clients\":%d,\"frames_streamed\":%lu,\"stream_fps\":%.1f,"
    "\"avg_frame_kb\":%.1f,\"exposure_ms\":%.1f,\"gain_x\":%.2f,\"free_heap_kb\":%u,\"free_psram_kb\":%u,\"cpu_mhz\":%u,\"wifi_rssi\":%d,"
    "\"wifi_mode\":\"%s\",\"ip\":\"%s\",\"ap_clients\":%d,\"reset_reason\":\"%s\"}",
    temperatureRead(), millis() / 1000, g_streamClients, g_framesStreamed, fps,
    g_framesStreamed ? g_bytesStreamed / 1024.0f / g_framesStreamed : 0,
    exp16 / 16.0f / 21.75f, gain / 16.0f, ESP.getFreeHeap() / 1024, ESP.getFreePsram() / 1024, getCpuFrequencyMhz(),
    WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0,
    WiFi.status() == WL_CONNECTED ? "station" : (WiFi.getMode() & WIFI_MODE_AP) ? "hotspot" : "off",
    WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString().c_str() : WiFi.softAPIP().toString().c_str(),
    WiFi.softAPgetStationNum(), resetReasonText());
}

static esp_err_t status_handler(httpd_req_t* req) {
  char buf[400]; statusText(buf, sizeof(buf));
  httpd_resp_set_type(req, "application/json");
  return httpd_resp_send(req, buf, HTTPD_RESP_USE_STRLEN);
}

static const char INDEX_HTML[] =
  "<!doctype html><html><head><meta name=viewport content='width=device-width'>"
  "<title>GanapatiCam</title><style>body{margin:0;background:#111;color:#eee;"
  "font-family:sans-serif;text-align:center}img{max-width:100%}a{color:#fc0}</style></head>"
  "<body><h3>GanapatiCam live</h3><img id=v>"
  "<script>document.getElementById('v').src='http://'+location.hostname+':81/stream'</script>"
  "<p><a href='/capture' target=_blank>snapshot</a></p></body></html>";

static esp_err_t index_handler(httpd_req_t* req) {
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, INDEX_HTML, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t capture_handler(httpd_req_t* req) {
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) { httpd_resp_send_500(req); return ESP_FAIL; }
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=capture.jpg");
  esp_err_t r = httpd_resp_send(req, (const char*)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  return r;
}

#define PART_BOUNDARY "123456789000000000000987654321"
static const char* STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char* STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char* STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

static esp_err_t stream_handler(httpd_req_t* req) {
  char part[64];
  esp_err_t res = httpd_resp_set_type(req, STREAM_CONTENT_TYPE);
  if (res != ESP_OK) return res;
  if (g_streamClients++ == 0) { g_streamStartMs = millis(); g_framesStreamed = 0; g_bytesStreamed = 0; }
  const uint32_t minInterval = MAX_STREAM_FPS > 0 ? 1000 / MAX_STREAM_FPS : 0;
  uint32_t lastSent = 0;
  while (true) {
    uint32_t now = millis();
    if (now - lastSent < minInterval) { delay(minInterval - (now - lastSent)); }
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) { Serial.println("frame grab failed"); g_grabFailures++; res = ESP_FAIL; break; }
    g_grabFailures = 0;
    lastSent = millis();
    g_framesStreamed++; g_bytesStreamed += fb->len;
    size_t hlen = snprintf(part, sizeof(part), STREAM_PART, fb->len);
    res = httpd_resp_send_chunk(req, STREAM_BOUNDARY, strlen(STREAM_BOUNDARY));
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, part, hlen);
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, (const char*)fb->buf, fb->len);
    esp_camera_fb_return(fb);
    if (res != ESP_OK) break;   // client disconnected
  }
  g_streamClients--;
  return res;
}

// ---- Flicker fix ----
// LED and tube lights on 50 Hz mains flicker at 100 Hz. A rolling-shutter sensor only
// avoids banding if the exposure time is an exact multiple of 10 ms. The OV3660's
// auto-exposure quantises exposure to "band steps" (registers 0x3a08..0x3a0e), but the
// esp32-camera driver leaves those at values for a slower clock (98 lines), so exposure
// lands on 8 x 98 = 784 lines = 35.8 ms, not a multiple of 10 ms, and bands roll through
// the picture. Here we measure the real line rate from frame timestamps and program the
// correct step (about 219 lines for 800x600 JPEG on this board).
// measure=true times real frames (takes ~3 s); measure=false uses the driver's nominal clock,
// which matched the measurement within 0.1% on this board. Call again after every frame-size change.
void fixBandingFilter(sensor_t* s, bool measure) {
  int hts = (s->get_reg(s, 0x380c, 0xff) << 8) | s->get_reg(s, 0x380d, 0xff);
  int vts = (s->get_reg(s, 0x380e, 0xff) << 8) | s->get_reg(s, 0x380f, 0xff);
  // Nominal line rate from the driver's PLL setup: SYSCLK 50 MHz (40 MHz at QXGA) / HTS
  double nominal = (s->status.framesize == FRAMESIZE_QXGA ? 40e6 : 50e6) / hts;
  double lineRate = nominal, fps = nominal / vts;
  if (measure) {
    // Skip warm-up frames, then take the SHORTEST interval between frame timestamps
    // (dropped frames only ever make an interval longer, never shorter).
    camera_fb_t* fb;
    for (int i = 0; i < 20; i++) { fb = esp_camera_fb_get(); if (fb) esp_camera_fb_return(fb); }
    int64_t prev = 0, minDelta = INT64_MAX;
    for (int i = 0; i < 60; i++) {
      fb = esp_camera_fb_get(); if (!fb) break;
      int64_t t = (int64_t)fb->timestamp.tv_sec * 1000000 + fb->timestamp.tv_usec;
      esp_camera_fb_return(fb);
      if (prev && t - prev > 1000 && t - prev < minDelta) minDelta = t - prev;
      prev = t;
    }
    if (minDelta != INT64_MAX) {
      fps = 1e6 / (double)minDelta;
      lineRate = fps * vts;
      if (fabs(lineRate - nominal) / nominal > 0.10) {
        Serial.printf("FLICKER FIX: measured %.0f lines/s looks off vs nominal %.0f, using nominal\n", lineRate, nominal);
        lineRate = nominal;
      }
    }
  }
  int step50 = (int)(lineRate / 100.0 + 0.5);        // lines per 1/100 s
  int step60 = (int)(lineRate / 120.0 + 0.5);        // lines per 1/120 s
  int max50 = vts / step50, max60 = vts / step60;
  s->set_reg(s, 0x3a08, 0xff, step50 >> 8); s->set_reg(s, 0x3a09, 0xff, step50 & 0xff);
  s->set_reg(s, 0x3a0a, 0xff, step60 >> 8); s->set_reg(s, 0x3a0b, 0xff, step60 & 0xff);
  s->set_reg(s, 0x3a0e, 0x3f, max50);       s->set_reg(s, 0x3a0d, 0x3f, max60);
  s->set_reg(s, 0x3c01, 0x80, 0x80);                       // manual band selection
  s->set_reg(s, 0x3c00, 0x04, MAINS_HZ == 50 ? 0x04 : 0);  // 50 or 60 Hz
  s->set_reg(s, 0x3a00, 0x20, 0x20);                       // banding filter on
  Serial.printf("FLICKER FIX: %.2f fps, %d lines/frame, %.0f lines/s (nominal %.0f) -> %d Hz band step %d lines (max %d bands)\n",
                fps, vts, lineRate, nominal, MAINS_HZ, MAINS_HZ == 50 ? step50 : step60, MAINS_HZ == 50 ? max50 : max60);
}

// Max analog gain the auto-exposure may use, in multiples (1..16). NOTE: the driver's
// set_gainceiling() is broken for the OV3660 (it writes the enum number into the 10-bit
// register, clamping gain to ~0.2x and blacking out the image), so write the register directly.
static void setGainCeiling(sensor_t* s, int times) {
  int v = times * 16 - 8; if (v > 0x3ff) v = 0x3ff; if (v < 16) v = 16;   // 16x -> 0xf8, the sensor default
  s->set_reg(s, 0x3a18, 0x03, v >> 8);
  s->set_reg(s, 0x3a19, 0xff, v & 0xff);
}

// ---- Sensor image-processing defaults ----
void applyQualityDefaults(sensor_t* s) {
  s->set_lenc(s, 1);         // lens shading correction (brighter, even corners)
  s->set_bpc(s, 1);          // black pixel correction
  s->set_wpc(s, 1);          // white pixel correction
  s->set_raw_gma(s, 1);      // gamma
  s->set_dcw(s, 1);          // downsize with averaging (less aliasing / noise)
  s->set_whitebal(s, 1);
  s->set_awb_gain(s, 1);
  s->set_wb_mode(s, 0);      // auto white balance
  s->set_exposure_ctrl(s, 1);
  s->set_gain_ctrl(s, 1);
  setGainCeiling(s, 16);     // max sensor gain 1..16 (x). Lower = less noise but darker in dim rooms.
  s->set_ae_level(s, 0);     // -2..2 exposure bias
  s->set_denoise(s, 6);      // 0..8   (6 = best noise/detail trade-off measured; 7+ visibly softens)
  s->set_sharpness(s, 2);    // -3..3   (+2 measured ~20% crisper with no extra noise)
  s->set_contrast(s, 0);
  s->set_saturation(s, 1);   // -2..2   (+1 gives livelier skin tones; the sensor gamma flattens colour a bit)
  s->set_brightness(s, 0);
  s->set_vflip(s, 0);
  s->set_hmirror(s, 0);
}

static int applySetting(sensor_t* s, const char* name, int v);
static void dumpInfo(sensor_t* s);
void fixBandingFilter(sensor_t* s, bool measure);

// /control?var=<name>&val=<n>  e.g. /control?var=brightness&val=1  (names: see applySetting)
static esp_err_t control_handler(httpd_req_t* req) {
  char q[96] = {0}, var[24] = {0}, val[12] = {0};
  if (httpd_req_get_url_query_str(req, q, sizeof(q)) != ESP_OK ||
      httpd_query_key_value(q, "var", var, sizeof(var)) != ESP_OK ||
      httpd_query_key_value(q, "val", val, sizeof(val)) != ESP_OK) {
    httpd_resp_set_status(req, "400 Bad Request");
    return httpd_resp_send(req, "usage: /control?var=<name>&val=<n>", HTTPD_RESP_USE_STRLEN);
  }
  int r = applySetting(esp_camera_sensor_get(), var, atoi(val));
  httpd_resp_set_type(req, "text/plain");
  return httpd_resp_send(req, r == 0 ? "OK" : r == -99 ? "unknown setting" : "sensor error", HTTPD_RESP_USE_STRLEN);
}

// /stream on port 80 just redirects to port 81 so old links keep working
static esp_err_t stream_hint_handler(httpd_req_t* req) {
  char host[48] = "192.168.4.1";
  httpd_req_get_hdr_value_str(req, "Host", host, sizeof(host));
  char* colon = strchr(host, ':'); if (colon) *colon = 0;
  char loc[80]; snprintf(loc, sizeof(loc), "http://%s:81/stream", host);
  httpd_resp_set_status(req, "302 Found");
  httpd_resp_set_hdr(req, "Location", loc);
  return httpd_resp_send(req, NULL, 0);
}

void startServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 80;
  httpd_uri_t index_uri   = { "/",        HTTP_GET, index_handler,   NULL };
  httpd_uri_t capture_uri = { "/capture", HTTP_GET, capture_handler, NULL };
  httpd_uri_t stream_uri  = { "/stream",  HTTP_GET, stream_handler,  NULL };
  httpd_uri_t stream_hint = { "/stream",  HTTP_GET, stream_hint_handler, NULL };
  httpd_uri_t control_uri = { "/control", HTTP_GET, control_handler, NULL };
  httpd_uri_t status_uri  = { "/status",  HTTP_GET, status_handler,  NULL };
  if (httpd_start(&server, &config) == ESP_OK) {
    httpd_register_uri_handler(server, &index_uri);
    httpd_register_uri_handler(server, &capture_uri);
    httpd_register_uri_handler(server, &stream_hint);
    httpd_register_uri_handler(server, &control_uri);
    httpd_register_uri_handler(server, &status_uri);
  }
  for (int i = 0; i < 2; i++) {            // :81/stream and :82/stream - two independent viewers (e.g. laptop worker + phone)
    httpd_config_t sc = HTTPD_DEFAULT_CONFIG();
    sc.server_port = 81 + i;
    sc.ctrl_port = 32769 + i;
    sc.max_open_sockets = 3;
    sc.stack_size = 6144;
    if (httpd_start(&streamServer[i], &sc) == ESP_OK) httpd_register_uri_handler(streamServer[i], &stream_uri);
  }
}

bool initCamera(framesize_t size) {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer   = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM;  c.pin_d1 = Y3_GPIO_NUM;  c.pin_d2 = Y4_GPIO_NUM;  c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM;  c.pin_d5 = Y7_GPIO_NUM;  c.pin_d6 = Y8_GPIO_NUM;  c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size   = FRAMESIZE_QXGA;   // allocate buffers for the sensor's max (2048x1536) so any size can be selected at runtime
  c.jpeg_quality = 12;               // 0-63, lower = better (below 12 risks frame-buffer overflow in noisy/dark scenes)
  c.fb_count     = 2;
  c.fb_location  = CAMERA_FB_IN_PSRAM;
  c.grab_mode    = CAMERA_GRAB_LATEST;

  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) {
    Serial.printf("CAMERA INIT FAILED: 0x%x\n", err);
    return false;
  }
  sensor_t* s = esp_camera_sensor_get();
  const char* name = s->id.PID == 0x26 ? "OV2640" : s->id.PID == 0x3660 ? "OV3660" : s->id.PID == 0x5640 ? "OV5640" : "unknown";
  Serial.printf("CAMERA OK: sensor PID 0x%04x (%s)\n", s->id.PID, name);
  s->set_framesize(s, size);
  g_size = size;
  applyQualityDefaults(s);
  fixBandingFilter(s, true);
  return true;
}

void camSetup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println("\n=== GanapatiCam boot ===");
  Serial.printf("RESET REASON: %s\n", resetReasonText());

  bool camOk = initCamera(STREAM_SIZE);
  if (camOk) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (fb) {
      Serial.printf("TEST FRAME OK: %ux%u, %u bytes JPEG\n", fb->width, fb->height, fb->len);
      esp_camera_fb_return(fb);
    } else {
      Serial.println("TEST FRAME FAILED");
    }
  }

  bool joined = false;
  if (strlen(WIFI_SSID) > 0) {
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    Serial.printf("Joining Wi-Fi '%s'", WIFI_SSID);
    for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) { delay(500); Serial.print("."); }
    Serial.println();
    joined = WiFi.status() == WL_CONNECTED;
    if (!joined) Serial.printf("WIFI: join failed, status %d (1=no such SSID on 2.4 GHz, 4/6=wrong password or disconnected)\n", WiFi.status());
  }
  if (joined) {
    g_useHomeWifi = true;
    WiFi.setAutoReconnect(true);
    Serial.printf("FEED: http://%s/\n", WiFi.localIP().toString().c_str());
  } else {
    // Hotspot fallback. If a home network is configured, keep the station side alive too
    // (AP+STA) so the camera still joins the home network when the router comes up later.
    WiFi.mode(strlen(WIFI_SSID) > 0 ? WIFI_AP_STA : WIFI_AP);
    bool apOk = WiFi.softAP(AP_SSID, AP_PASS, 6 /*channel*/, 0 /*visible*/, 4 /*max clients*/);
    Serial.printf("HOTSPOT: softAP start %s\n", apOk ? "OK" : "FAILED");
    Serial.printf("HOTSPOT: connect to Wi-Fi '%s' (password %s), then open http://%s/\n",
                  AP_SSID, AP_PASS, WiFi.softAPIP().toString().c_str());
    if (strlen(WIFI_SSID) > 0) { g_useHomeWifi = true; WiFi.setAutoReconnect(true); WiFi.begin(WIFI_SSID, WIFI_PASS); }
  }
  startServer();
}

// ---- USB serial command interface (used by usb_feed.py) ----
//   c                       -> "FRAME <len>\n" + JPEG bytes
//   i                       -> dump exposure / gain / banding / timing registers
//   f                       -> measure sensor frame rate over 2 s
//   s                       -> scan Wi-Fi networks visible to the 2.4 GHz radio
//   t                       -> status JSON: chip temperature, stream fps, exposure, gain, memory
//   r <reg>                 -> read a sensor register        e.g. r 3a00
//   w <reg> <mask> <value>  -> write masked register bits    e.g. w 3a00 20 20
//   x <name> <value>        -> named setting, see applySetting() e.g. x brightness 1
static uint8_t rd(sensor_t* s, int reg) { return (uint8_t)s->get_reg(s, reg, 0xff); }

static void dumpInfo(sensor_t* s) {
  uint32_t exp16 = ((uint32_t)rd(s,0x3500) << 16) | (rd(s,0x3501) << 8) | rd(s,0x3502); // exposure, 1/16 line units
  uint16_t gain  = ((rd(s,0x350a) & 3) << 8) | rd(s,0x350b);
  uint16_t hts   = (rd(s,0x380c) << 8) | rd(s,0x380d);
  uint16_t vts   = (rd(s,0x380e) << 8) | rd(s,0x380f);
  uint16_t b50   = (rd(s,0x3a08) << 8) | rd(s,0x3a09);
  uint16_t b60   = (rd(s,0x3a0a) << 8) | rd(s,0x3a0b);
  Serial.printf("INFO exposure_lines=%.1f gain=%u/16 hts=%u vts=%u\n", exp16 / 16.0, gain, hts, vts);
  Serial.printf("INFO aec_ctrl00(3a00)=%02x band_ctrl(3c00)=%02x band_mode(3c01)=%02x b50step=%u b50max=%u b60step=%u b60max=%u\n",
                rd(s,0x3a00), rd(s,0x3c00), rd(s,0x3c01), b50, rd(s,0x3a0e) & 0x3f, b60, rd(s,0x3a0d) & 0x3f);
  Serial.printf("INFO aec_max_50(3a14/15)=%u aec_max_60(3a02/03)=%u aec_ctrl05(3a05)=%02x pll(3034-37)=%02x %02x %02x %02x\n",
                (rd(s,0x3a14) << 8) | rd(s,0x3a15), (rd(s,0x3a02) << 8) | rd(s,0x3a03), rd(s,0x3a05),
                rd(s,0x3034), rd(s,0x3035), rd(s,0x3036), rd(s,0x3037));
}

static void measureFps() {
  uint32_t t0 = millis(); int n = 0;
  while (millis() - t0 < 2000) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (fb) { esp_camera_fb_return(fb); n++; }
  }
  Serial.printf("FPS %.2f\n", n / ((millis() - t0) / 1000.0));
}

// Switching between 16:9 (HD/FHD) and 4:3 sizes at runtime leaves the OV3660 ~7x less sensitive
// (measured: XGA needed 15.5x gain after HD, 2.25x after a clean boot), so a ratio change
// does a full camera re-init. Same-ratio changes (XGA <-> UXGA) are a plain register switch.
static bool is169(framesize_t f) { return f == FRAMESIZE_HD || f == FRAMESIZE_FHD || f == FRAMESIZE_QHD; }
static int changeFramesize(framesize_t f) {
  sensor_t* s = esp_camera_sensor_get();
  if (g_size != FRAMESIZE_INVALID && is169(g_size) == is169(f)) {
    int r = s->set_framesize(s, f);
    if (r == 0) { g_size = f; fixBandingFilter(s, false); }
    return r;
  }
  Serial.println("FRAMESIZE: aspect ratio change, re-initialising camera");
  esp_camera_deinit();
  delay(50);
  return initCamera(f) ? 0 : -1;
}

static int applySetting(sensor_t* s, const char* name, int v) {
  #define S(n, call) if (!strcmp(name, n)) return call;
  S("brightness",  s->set_brightness(s, v))     S("contrast",   s->set_contrast(s, v))
  S("saturation",  s->set_saturation(s, v))     S("sharpness",  s->set_sharpness(s, v))
  S("denoise",     s->set_denoise(s, v))        S("quality",    s->set_quality(s, v))
  S("framesize",   changeFramesize((framesize_t)v))
  S("gain_ceiling", (setGainCeiling(s, v), 0))   // x gain_ceiling 8  -> max 8x gain
  S("aec",         s->set_exposure_ctrl(s, v))  S("aec_value",  s->set_aec_value(s, v))
  S("ae_level",    s->set_ae_level(s, v))       S("night",      s->set_aec2(s, v))
  S("agc",         s->set_gain_ctrl(s, v))      S("agc_gain",   s->set_agc_gain(s, v))
  S("awb",         s->set_whitebal(s, v))       S("awb_gain",   s->set_awb_gain(s, v))
  S("wb_mode",     s->set_wb_mode(s, v))        S("lenc",       s->set_lenc(s, v))
  S("bpc",         s->set_bpc(s, v))            S("wpc",        s->set_wpc(s, v))
  S("raw_gma",     s->set_raw_gma(s, v))        S("dcw",        s->set_dcw(s, v))
  S("vflip",       s->set_vflip(s, v))          S("hmirror",    s->set_hmirror(s, v))
  S("effect",      s->set_special_effect(s, v))
  // power / heat controls
  S("cpu",         (setCpuFrequencyMhz(v), 0))                    // x cpu 160  (80/160/240 MHz)
  S("xclk",        s->set_xclk(s, LEDC_TIMER_0, v))               // x xclk 10  camera clock MHz (10-20; lower = cooler, fewer fps)
  S("txpower",     (WiFi.setTxPower((wifi_power_t)v), 0))         // x txpower 34  (units of 0.25 dBm: 8=2dBm .. 78=19.5dBm)
  S("wifi",        (v ? (WiFi.mode(WIFI_AP), WiFi.softAP(AP_SSID, AP_PASS, 6, 0, 4), 0) : (WiFi.mode(WIFI_OFF), 0)))  // x wifi 0/1
  S("apopen",      (WiFi.softAPdisconnect(true), WiFi.mode(WIFI_AP), WiFi.softAP(AP_SSID, v ? NULL : AP_PASS, 6, 0, 4), 0))  // x apopen 1 = hotspot without password (test)
  #undef S
  return -99;
}

static void handleCommand(char* line) {
  sensor_t* s = esp_camera_sensor_get();
  char cmd = line[0];
  if (cmd == 'c') {
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) { Serial.println("FRAME 0"); g_grabFailures++; return; }
    g_grabFailures = 0;
    g_framesStreamed++; g_bytesStreamed += fb->len;
    if (!g_streamStartMs) g_streamStartMs = millis();
    Serial.printf("FRAME %u\n", fb->len);
    Serial.write(fb->buf, fb->len);
    Serial.flush();
    esp_camera_fb_return(fb);
  } else if (cmd == 'i') {
    dumpInfo(s);
  } else if (cmd == 'f') {
    measureFps();
  } else if (cmd == 's') {   // Wi-Fi scan: lists networks the 2.4 GHz radio can see
    Serial.println("SCAN start");
    int n = WiFi.scanNetworks(false, true);
    for (int i = 0; i < n; i++)
      Serial.printf("SCAN %-32s ch %2d  %4d dBm  %s\n", WiFi.SSID(i).c_str(), WiFi.channel(i), WiFi.RSSI(i),
                    WiFi.encryptionType(i) == WIFI_AUTH_OPEN ? "open" : "secured");
    Serial.printf("SCAN done (%d networks)\n", n);
    WiFi.scanDelete();
  } else if (cmd == 't') {
    char buf[400]; statusText(buf, sizeof(buf)); Serial.println(buf);
  } else if (cmd == 'r') {
    int reg = (int)strtol(line + 1, NULL, 16);
    Serial.printf("REG %04x = %02x\n", reg, rd(s, reg));
  } else if (cmd == 'w') {
    char* e; int reg = strtol(line + 1, &e, 16); int mask = strtol(e, &e, 16); int val = strtol(e, &e, 16);
    int r = s->set_reg(s, reg, mask, val);
    Serial.printf("%s w %04x mask %02x val %02x -> now %02x\n", r == 0 ? "OK" : "ERR", reg, mask, val, rd(s, reg));
  } else if (cmd == 'x') {
    char name[24] = {0}; int v = 0;
    if (sscanf(line + 1, "%23s %d", name, &v) == 2) {
      int r = applySetting(s, name, v);
      if (r == -99) Serial.printf("ERR unknown setting %s\n", name);
      else Serial.printf("%s x %s %d\n", r == 0 ? "OK" : "ERR", name, v);
    } else Serial.println("ERR usage: x <name> <value>");
  }
}

bool initCamera(framesize_t size);

// Continuous-streaming housekeeping: recover the camera if it stops delivering frames,
// and rejoin home Wi-Fi if it drops. Runs every 2 s from camLoop().
static void housekeeping() {
  static uint32_t last = 0;
  if (millis() - last < 2000) return;
  last = millis();
  if (g_grabFailures >= 5) {
    Serial.println("RECOVER: camera stopped delivering frames, re-initialising");
    esp_camera_deinit();
    delay(100);
    g_grabFailures = initCamera(g_size == FRAMESIZE_INVALID ? STREAM_SIZE : g_size) ? 0 : 5;   // keep retrying every 2 s if it fails
  }
  static bool wasConnected = false;
  bool nowConnected = WiFi.status() == WL_CONNECTED;
  if (g_useHomeWifi && !nowConnected) {
    static uint32_t lastTry = 0;
    if (millis() - lastTry > 15000) { lastTry = millis(); Serial.println("WIFI: (re)joining home network"); WiFi.begin(WIFI_SSID, WIFI_PASS); }
  }
  if (nowConnected && !wasConnected) {
    Serial.printf("FEED: http://%s/  (joined '%s', %d dBm)\n", WiFi.localIP().toString().c_str(), WIFI_SSID, WiFi.RSSI());
    if (WiFi.getMode() & WIFI_MODE_AP) { WiFi.softAPdisconnect(true); WiFi.mode(WIFI_STA); Serial.println("HOTSPOT: stopped, home network is up"); }
  }
  wasConnected = nowConnected;
}

void camLoop() {
  housekeeping();
  static char line[64]; static int n = 0;
  while (Serial.available()) {
    int ch = Serial.read();
    if (ch == 'c' && n == 0) { handleCommand((char*)"c"); continue; }   // bare 'c' needs no newline
    if (ch == '\n' || ch == '\r') {
      if (n > 0) { line[n] = 0; handleCommand(line); n = 0; }
    } else if (n < (int)sizeof(line) - 1) {
      line[n++] = (char)ch;
    }
  }
  delay(2);
}

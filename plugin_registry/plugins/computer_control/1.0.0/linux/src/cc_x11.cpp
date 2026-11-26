#include "cc_x11.h"

#include <X11/Xatom.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/extensions/XTest.h>
#include <X11/keysym.h>

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <zlib.h>

#include <cstdio>
#include <cstring>
#include <map>
#include <mutex>
#include <vector>

namespace pai::cc::x11 {

namespace {

std::mutex g_mu;
Display* g_dpy = nullptr;
bool g_tried = false;
bool g_xtest = false;

// Called with g_mu held.
Display* OpenLocked() {
    if (g_tried) return g_dpy;
    g_tried = true;
    g_dpy = XOpenDisplay(nullptr);
    if (g_dpy) {
        int ev, er, major, minor;
        g_xtest = XTestQueryExtension(g_dpy, &ev, &er, &major, &minor) != 0;
    }
    return g_dpy;
}

struct KeyFor {
    KeyCode code = 0;
    bool shift = false;
    bool remapped = false;
};

// keysym -> a keycode (and whether Shift is needed), from the current keymap.
bool Lookup(Display* d, KeySym sym, KeyFor& out) {
    int lo = 0, hi = 0;
    XDisplayKeycodes(d, &lo, &hi);
    int per = 0;
    KeySym* map = XGetKeyboardMapping(d, static_cast<KeyCode>(lo), hi - lo + 1, &per);
    if (!map) return false;
    bool found = false;
    for (int kc = lo; kc <= hi && !found; ++kc) {
        for (int level = 0; level < per && level < 2; ++level) {              // base and Shift levels only
            if (map[(kc - lo) * per + level] == sym) {
                out.code = static_cast<KeyCode>(kc);
                out.shift = (level == 1);
                found = true;
                break;
            }
        }
    }
    XFree(map);
    return found;
}

// A keycode with no symbols at all, to borrow for a keysym that is not on the keyboard (e.g. "€" or "✓").
KeyCode SpareKeycode(Display* d) {
    int lo = 0, hi = 0;
    XDisplayKeycodes(d, &lo, &hi);
    int per = 0;
    KeySym* map = XGetKeyboardMapping(d, static_cast<KeyCode>(lo), hi - lo + 1, &per);
    if (!map) return 0;
    KeyCode spare = 0;
    for (int kc = hi; kc >= lo && !spare; --kc) {
        bool empty = true;
        for (int i = 0; i < per; ++i) if (map[(kc - lo) * per + i] != NoSymbol) { empty = false; break; }
        if (empty) spare = static_cast<KeyCode>(kc);
    }
    XFree(map);
    return spare;
}

std::map<int32_t, KeyFor> g_remapped;     // keysym -> the borrowed keycode while the key is held

}  // namespace

bool Available(std::string* why) {
    std::lock_guard<std::mutex> lock(g_mu);
    Display* d = OpenLocked();
    if (!d) { if (why) *why = "no X display (DISPLAY is not set or the server refuses us)"; return false; }
    if (!g_xtest) { if (why) *why = "the X server has no XTEST extension"; return false; }
    return true;
}

bool XTestAvailable() { return Available(nullptr); }

bool ScreenSize(int& w, int& h) {
    std::lock_guard<std::mutex> lock(g_mu);
    Display* d = OpenLocked();
    if (!d) return false;
    w = DisplayWidth(d, DefaultScreen(d));
    h = DisplayHeight(d, DefaultScreen(d));
    return w > 0 && h > 0;
}

bool MotionAbsolute(double x, double y, std::string& error) {
    std::lock_guard<std::mutex> lock(g_mu);
    Display* d = OpenLocked();
    if (!d || !g_xtest) { error = "xtest_unavailable"; return false; }
    XTestFakeMotionEvent(d, DefaultScreen(d), static_cast<int>(x), static_cast<int>(y), CurrentTime);
    XSync(d, False);
    return true;
}

bool Button(int b, bool pressed, std::string& error) {
    std::lock_guard<std::mutex> lock(g_mu);
    Display* d = OpenLocked();
    if (!d || !g_xtest) { error = "xtest_unavailable"; return false; }
    XTestFakeButtonEvent(d, static_cast<unsigned>(b), pressed ? True : False, CurrentTime);
    XSync(d, False);
    return true;
}

bool Key(int32_t keysym, bool pressed, std::string& error) {
    std::lock_guard<std::mutex> lock(g_mu);
    Display* d = OpenLocked();
    if (!d || !g_xtest) { error = "xtest_unavailable"; return false; }
    const KeySym sym = static_cast<KeySym>(keysym);

    if (!pressed) {                                                     // release what we pressed, however we pressed it
        auto it = g_remapped.find(keysym);
        KeyFor k;
        if (it != g_remapped.end()) k = it->second;
        else if (!Lookup(d, sym, k)) { error = "key_not_available"; return false; }
        XTestFakeKeyEvent(d, k.code, False, CurrentTime);
        if (k.shift) XTestFakeKeyEvent(d, XKeysymToKeycode(d, XK_Shift_L), False, CurrentTime);
        if (it != g_remapped.end()) {                                   // give the borrowed keycode back
            KeySym none = NoSymbol;
            XChangeKeyboardMapping(d, k.code, 1, &none, 1);
            g_remapped.erase(it);
        }
        XSync(d, False);
        return true;
    }

    KeyFor k;
    if (!Lookup(d, sym, k)) {
        k.code = SpareKeycode(d);
        if (!k.code) { error = "key_not_available"; return false; }
        KeySym s = sym;
        XChangeKeyboardMapping(d, k.code, 1, &s, 1);
        XSync(d, False);                                                // the server must see the mapping before the key
        k.remapped = true;
        g_remapped[keysym] = k;
    }
    if (k.shift) XTestFakeKeyEvent(d, XKeysymToKeycode(d, XK_Shift_L), True, CurrentTime);
    XTestFakeKeyEvent(d, k.code, True, CurrentTime);
    XSync(d, False);
    return true;
}

namespace {
void PutBe32(std::vector<unsigned char>& v, uint32_t x) {
    v.push_back(x >> 24); v.push_back((x >> 16) & 255); v.push_back((x >> 8) & 255); v.push_back(x & 255);
}
void PngChunk(FILE* f, const char type[4], const std::vector<unsigned char>& data) {
    std::vector<unsigned char> len;
    PutBe32(len, static_cast<uint32_t>(data.size()));
    fwrite(len.data(), 1, 4, f);
    fwrite(type, 1, 4, f);
    if (!data.empty()) fwrite(data.data(), 1, data.size(), f);
    uLong crc = crc32(0L, Z_NULL, 0);
    crc = crc32(crc, reinterpret_cast<const Bytef*>(type), 4);
    if (!data.empty()) crc = crc32(crc, data.data(), static_cast<uInt>(data.size()));
    std::vector<unsigned char> c;
    PutBe32(c, static_cast<uint32_t>(crc));
    fwrite(c.data(), 1, 4, f);
}
}  // namespace

bool CaptureScreenPng(const std::string& path, int& w, int& h, std::string& error) {
    std::vector<unsigned char> raw;                       // filtered scanlines, built under the lock
    {
        std::lock_guard<std::mutex> lock(g_mu);
        Display* d = OpenLocked();
        if (!d) { error = "no_x_display"; return false; }
        Window root = DefaultRootWindow(d);
        XWindowAttributes at;
        if (!XGetWindowAttributes(d, root, &at) || at.width <= 0 || at.height <= 0) { error = "capture_failed"; return false; }
        w = at.width;
        h = at.height;
        XImage* img = XGetImage(d, root, 0, 0, static_cast<unsigned>(w), static_cast<unsigned>(h), AllPlanes, ZPixmap);
        if (!img) { error = "capture_failed"; return false; }
        raw.reserve(static_cast<size_t>(h) * (1 + static_cast<size_t>(w) * 3));
        const unsigned long rm = img->red_mask, gm = img->green_mask, bm = img->blue_mask;
        auto shift = [](unsigned long m) { int s = 0; while (m && !(m & 1)) { m >>= 1; ++s; } return s; };
        const int rs = shift(rm), gs = shift(gm), bs = shift(bm);
        for (int y = 0; y < h; ++y) {
            raw.push_back(0);                             // PNG filter type 0 (none)
            for (int x = 0; x < w; ++x) {
                const unsigned long px = XGetPixel(img, x, y);
                raw.push_back(static_cast<unsigned char>((px & rm) >> rs));
                raw.push_back(static_cast<unsigned char>((px & gm) >> gs));
                raw.push_back(static_cast<unsigned char>((px & bm) >> bs));
            }
        }
        XDestroyImage(img);
    }
    uLongf clen = compressBound(static_cast<uLong>(raw.size()));
    std::vector<unsigned char> z(clen);
    if (compress2(z.data(), &clen, raw.data(), static_cast<uLong>(raw.size()), 3) != Z_OK) { error = "capture_failed"; return false; }
    z.resize(clen);

    const int fd = ::open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0600);
    FILE* f = fd < 0 ? nullptr : fdopen(fd, "wb");
    if (!f) { if (fd >= 0) ::close(fd); error = "capture_failed"; return false; }
    static const unsigned char sig[8] = {0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A};
    fwrite(sig, 1, 8, f);
    std::vector<unsigned char> ihdr;
    PutBe32(ihdr, static_cast<uint32_t>(w));
    PutBe32(ihdr, static_cast<uint32_t>(h));
    ihdr.push_back(8); ihdr.push_back(2); ihdr.push_back(0); ihdr.push_back(0); ihdr.push_back(0);   // 8-bit RGB
    PngChunk(f, "IHDR", ihdr);
    PngChunk(f, "IDAT", z);
    PngChunk(f, "IEND", {});
    const bool ok = fclose(f) == 0;
    if (!ok) error = "capture_failed";
    return ok;
}

bool ActiveWindow(long& pid, std::string& title) {
    std::lock_guard<std::mutex> lock(g_mu);
    Display* d = OpenLocked();
    if (!d) return false;
    Window root = DefaultRootWindow(d);
    Atom active = XInternAtom(d, "_NET_ACTIVE_WINDOW", True);
    if (active == None) return false;
    Atom type;
    int fmt;
    unsigned long n, after;
    unsigned char* data = nullptr;
    if (XGetWindowProperty(d, root, active, 0, 1, False, XA_WINDOW, &type, &fmt, &n, &after, &data) != Success || !data || n == 0) {
        if (data) XFree(data);
        return false;
    }
    Window w = *reinterpret_cast<Window*>(data);
    XFree(data);
    if (!w) return false;
    pid = 0;
    title.clear();
    Atom pid_atom = XInternAtom(d, "_NET_WM_PID", True);
    if (pid_atom != None && XGetWindowProperty(d, w, pid_atom, 0, 1, False, XA_CARDINAL, &type, &fmt, &n, &after, &data) == Success && data) {
        if (n > 0) pid = static_cast<long>(*reinterpret_cast<unsigned long*>(data));
        XFree(data);
    }
    Atom name_atom = XInternAtom(d, "_NET_WM_NAME", True), utf8 = XInternAtom(d, "UTF8_STRING", True);
    if (name_atom != None && XGetWindowProperty(d, w, name_atom, 0, 256, False, utf8, &type, &fmt, &n, &after, &data) == Success && data) {
        title.assign(reinterpret_cast<char*>(data), n);
        XFree(data);
    }
    return true;
}

}  // namespace pai::cc::x11

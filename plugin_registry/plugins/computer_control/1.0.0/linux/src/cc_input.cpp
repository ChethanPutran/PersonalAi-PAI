#include "cc_input.h"

#include "cc_x11.h"
#include "pai_portal.h"
#include "pai_system_helpers.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <memory>
#include <mutex>

namespace pai::cc {

std::string SessionKind() {
    const std::string s = pai::sys::SessionType();
    if (s == "x11" || s == "wayland") return s;
    if (!pai::sys::EnvString("WAYLAND_DISPLAY", "").empty()) return "wayland";
    if (!pai::sys::EnvString("DISPLAY", "").empty()) return "x11";
    return "unknown";
}

namespace {

int EnvInt(const char* name, int fallback, int lo, int hi) {
    const std::string s = pai::sys::EnvString(name, "");
    int v = s.empty() ? fallback : std::atoi(s.c_str());
    if (v == 0 && !s.empty()) v = fallback;
    return std::max(lo, std::min(hi, v));
}

// ----------------------------------------------------------------------------- portal

class PortalBackend : public InputBackend {
public:
    const char* Name() const override { return "portal"; }

    bool Ready(std::string& error, std::string& hint) override {
        auto& session = pai::portal::InputSession::Get();
        if (session.Status().active) return true;
        std::string code, detail;
        if (session.Start(EnvInt("PAI_CC_START_TIMEOUT", 120, 1, 600), code, detail)) return true;
        if (code == "cancelled") { error = "input_denied"; hint = "the user declined remote control in the desktop's dialog"; }
        else if (code == "timeout") { error = "input_timeout"; hint = "no answer to the desktop's permission dialog"; }
        else if (code == "portal_unavailable") {
            error = "portal_unavailable";
            hint = "no desktop portal with remote-desktop support (xdg-desktop-portal) is running";
        } else { error = "input_failed"; hint = detail; }
        return false;
    }

    bool Active() override { return pai::portal::InputSession::Get().Status().active; }

    bool Move(double gx, double gy, std::string& error) override {
        auto st = pai::portal::InputSession::Get().Status();
        if (st.streams.empty()) { error = "no_screens_shared"; return false; }
        const double right = Right(st), bottom = Bottom(st);
        if (gx >= right && gx <= right + 1e-6) gx = right - 0.5;          // the exact far edge belongs to the last pixel
        if (gy >= bottom && gy <= bottom + 1e-6) gy = bottom - 0.5;
        for (const auto& s : st.streams) {
            if (gx >= s.x && gx < s.x + s.w && gy >= s.y && gy < s.y + s.h)
                return pai::portal::InputSession::Get().PointerMotionAbsolute(s.node_id, gx - s.x, gy - s.y, error);
        }
        error = "point_outside_shared_screens";                           // never quietly moved onto a screen
        return false;
    }
    bool Button(int b, bool pressed, std::string& error) override {
        return pai::portal::InputSession::Get().PointerButton(b, pressed, error);
    }
    bool Wheel(int axis, int steps, std::string& error) override {
        return pai::portal::InputSession::Get().PointerAxisDiscrete(static_cast<uint32_t>(axis), steps, error);
    }
    bool Key(int32_t keysym, bool pressed, std::string& error) override {
        return pai::portal::InputSession::Get().KeyboardKeysym(keysym, pressed, error);
    }
    bool DesktopSize(int& w, int& h) override {
        auto st = pai::portal::InputSession::Get().Status();
        if (st.streams.empty()) return false;
        int x0 = st.streams[0].x, y0 = st.streams[0].y, x1 = x0 + st.streams[0].w, y1 = y0 + st.streams[0].h;
        for (const auto& s : st.streams) {
            x0 = std::min(x0, s.x); y0 = std::min(y0, s.y);
            x1 = std::max(x1, s.x + s.w); y1 = std::max(y1, s.y + s.h);
        }
        w = x1 - x0;
        h = y1 - y0;
        return w > 0 && h > 0;
    }

private:
    static double Right(const pai::portal::InputStatus& st) {
        double r = 0;
        for (const auto& s : st.streams) r = std::max(r, double(s.x + s.w));
        return r;
    }
    static double Bottom(const pai::portal::InputStatus& st) {
        double b = 0;
        for (const auto& s : st.streams) b = std::max(b, double(s.y + s.h));
        return b;
    }
};

// ----------------------------------------------------------------------------- xtest

int ToXButton(int evdev) { return evdev == kBtnLeft ? 1 : evdev == kBtnMiddle ? 2 : evdev == kBtnRight ? 3 : 0; }

class XTestBackend : public InputBackend {
public:
    const char* Name() const override { return "xtest"; }
    bool Ready(std::string& error, std::string& hint) override {
        std::string why;
        if (pai::cc::x11::Available(&why)) return true;
        error = "xtest_unavailable";
        hint = why;
        return false;
    }
    bool Active() override { return pai::cc::x11::XTestAvailable(); }
    bool Move(double x, double y, std::string& error) override {
        int w = 0, h = 0;
        if (pai::cc::x11::ScreenSize(w, h) && (x < 0 || y < 0 || x >= w + 1e-6 || y >= h + 1e-6)) {
            error = "point_outside_screen";
            return false;
        }
        return pai::cc::x11::MotionAbsolute(x, y, error);
    }
    bool Button(int b, bool pressed, std::string& error) override {
        const int xb = ToXButton(b);
        if (!xb) { error = "invalid_button"; return false; }
        return pai::cc::x11::Button(xb, pressed, error);
    }
    bool Wheel(int axis, int steps, std::string& error) override {
        const int xb = axis == 0 ? (steps > 0 ? 5 : 4) : (steps > 0 ? 7 : 6);
        for (int i = 0; i < std::abs(steps); ++i) {
            if (!pai::cc::x11::Button(xb, true, error) || !pai::cc::x11::Button(xb, false, error)) return false;
        }
        return true;
    }
    bool Key(int32_t keysym, bool pressed, std::string& error) override { return pai::cc::x11::Key(keysym, pressed, error); }
    bool DesktopSize(int& w, int& h) override { return pai::cc::x11::ScreenSize(w, h); }
};

std::mutex g_mu;
std::unique_ptr<InputBackend> g_backend;

}  // namespace

std::string BackendName() {
    const std::string want = pai::sys::EnvString("PAI_CC_INPUT_BACKEND", "auto");
    if (want == "portal" || want == "xtest") return want;
    return SessionKind() == "x11" ? "xtest" : "portal";          // Wayland (or unknown): the portal is the sanctioned route
}

InputBackend& Backend() {
    std::lock_guard<std::mutex> lock(g_mu);
    const std::string name = BackendName();
    if (!g_backend || name != g_backend->Name()) {
        if (g_backend && std::string(g_backend->Name()) == "portal") pai::portal::InputSession::Get().Stop();
        if (name == "xtest") g_backend = std::make_unique<XTestBackend>();
        else g_backend = std::make_unique<PortalBackend>();
    }
    return *g_backend;
}

void StopBackends() {
    std::lock_guard<std::mutex> lock(g_mu);
    pai::portal::InputSession::Get().Stop();       // ends the desktop's "remote control active" indicator
}

}  // namespace pai::cc

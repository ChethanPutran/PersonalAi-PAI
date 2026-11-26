// computer_control (Linux): observe and operate the desktop for an agent, with the same capability contract
// as the Android plugin (docs/computer_control.md). Small, stateless-looking actions; the observe -> act ->
// verify loop lives in the backend agent.
//
//   perception : the accessibility tree via AT-SPI2 (primary), a screenshot on request (portal/grim/scrot)
//   action     : a semantic AT-SPI action when the element offers one, else pointer/keyboard events through
//                the desktop's remote-desktop portal (Wayland) or XTEST (X11)
//   safety     : nothing happens without the user's consent for THIS plugin (PAI_CC_INPUT_CONSENT /
//                PAI_CC_SCREEN_CONSENT, set by the app from the user's per-plugin decisions); on Wayland the
//                desktop asks as well; per-minute event cap; bounded sizes; one action at a time.
#include "cc_atspi.h"
#include "cc_input.h"
#include "cc_model.h"
#include "cc_x11.h"
#include "pai_desktop_apps.h"
#include "pai_input_keys.h"
#include "pai_plugin_common.h"
#include "pai_screen_capture.h"
#include "pai_system_helpers.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <deque>
#include <fstream>
#include <iterator>
#include <mutex>
#include <thread>

#include <unistd.h>

namespace {

using namespace pai::cc;
using pai::plugin::ResultMap;
using Clock = std::chrono::steady_clock;

constexpr size_t kMaxScreenshotBytes = 6 * 1024 * 1024;       // PNG bytes before base64
constexpr int kMaxAppsListed = 200;

std::mutex g_action;                    // one input action at a time: a typed sentence must not interleave with a click
std::mutex g_obs_mu;                    // the observation store
ObservationStore& Store() {
    static ObservationStore store([](Handle h) { atspi::Release(h); });
    return store;
}

bool Consent(const char* env) { return pai::sys::EnvString(env, "0") == "1"; }

int EnvInt(const char* name, int fallback, int lo, int hi) {
    const std::string s = pai::sys::EnvString(name, "");
    int v = s.empty() ? fallback : std::atoi(s.c_str());
    if (v == 0 && !s.empty()) v = fallback;
    return std::max(lo, std::min(hi, v));
}

void Pause(int ms) {
    if (ms > 0) std::this_thread::sleep_for(std::chrono::milliseconds(ms));
}

bool ConsumeEvents(int n) {            // sliding one-minute window of injected events
    static std::mutex m;
    static std::deque<Clock::time_point> window;
    const int cap = EnvInt("PAI_CC_MAX_EVENTS_PER_MIN", 6000, 10, 1000000);
    std::lock_guard<std::mutex> lock(m);
    const auto now = Clock::now();
    while (!window.empty() && now - window.front() > std::chrono::minutes(1)) window.pop_front();
    if (static_cast<int>(window.size()) + n > cap) return false;
    for (int i = 0; i < n; ++i) window.push_back(now);
    return true;
}

std::string Num(double v) {
    char buf[32];
    std::snprintf(buf, sizeof buf, "%.0f", v);
    return buf;
}

Fail NotConsented(const char* what, const char* env) {
    return {std::string(what) + "_not_consented",
            std::string("allow the ") + what + " permission for the computer control plugin in PAI (or set " + env + "=1)"};
}

// --------------------------------------------------------------------------------- targets

struct Resolved {
    bool by_element = false;
    Handle handle = 0;
    std::string element_id;
    double x = 0, y = 0;                 // where to click: the element's centre or the given point
    bool has_position = false;
};

Fail ResolveTarget(const Target& t, Resolved& out) {
    out = {};
    if (!t.element_id.empty()) {
        std::string err;
        {
            std::lock_guard<std::mutex> lock(g_obs_mu);
            if (!Store().Resolve(t.element_id, t.observation_id, out.handle, err)) {
                if (err == "no_observation") return {err, "call computer.observe first: element ids come from it"};
                if (err == "stale_observation")
                    return {err, "the screen was observed again since that id was issued; use ids from the latest observation (" +
                                     Store().current() + ")"};
                return {err, "no element " + t.element_id + " in the latest observation (" + Store().current() + ")"};
            }
        }
        if (!atspi::IsAlive(out.handle)) return {"element_gone", "that element no longer exists; call computer.observe again"};
        out.by_element = true;
        out.element_id = t.element_id;
        Bounds b;
        if (atspi::GetBounds(out.handle, b) && b.Valid()) {
            out.x = b.x + b.w / 2.0;
            out.y = b.y + b.h / 2.0;
            out.has_position = true;
        }
        return {};
    }
    if (t.has_point) {
        out.x = t.point.x;
        out.y = t.point.y;
        out.has_position = true;
        return {};
    }
    return {"missing_target", "pass element_id or x and y"};
}

std::string TargetJson(const Resolved& r) {
    std::string s = "{";
    if (r.by_element) s += "\"id\":" + JsonString(r.element_id) + ",";
    s += r.has_position ? "\"x\":" + Num(r.x) + ",\"y\":" + Num(r.y) : "\"x\":null,\"y\":null";
    return s + "}";
}

// The backend, ready (the desktop may ask the user the first time).
Fail ReadyBackend(InputBackend*& be) {
    be = &Backend();
    std::string error, hint;
    if (!be->Ready(error, hint)) return {error, hint};
    return {};
}

ResultMap Failed(std::string& error, const Fail& f, ResultMap extra = {}) { return FailResult(error, f, std::move(extra)); }

// ------------------------------------------------------------------------------- status

ResultMap Status() {
    std::vector<std::string> missing;
    std::string how;
    auto add = [&](const std::string& m, const std::string& fix) {
        missing.push_back(m);
        how += (how.empty() ? "" : " ") + fix;
    };

    const std::string session = SessionKind();
    const auto bus = atspi::Status();
    bool tree = bus.bus && bus.enabled;
    if (!bus.bus) {
        add("accessibility_bus", "The accessibility bus is not running: install and start at-spi2-core (at-spi-bus-launcher) in your desktop session.");
    } else if (!bus.enabled) {
        add("accessibility_enabled",
            "Turn accessibility support on: GNOME Settings > Accessibility, or `gsettings set org.gnome.desktop.interface toolkit-accessibility true`, then restart the app you want to control.");
    }

    bool screenshot = true;
    if (!Consent("PAI_CC_SCREEN_CONSENT")) {
        add("screen_permission", "Allow the screen permission for the computer control plugin in PAI.");
        tree = false;
    }
    if (session == "unknown") screenshot = false;

    const std::string backend = BackendName();
    bool input = true;
    if (!Consent("PAI_CC_INPUT_CONSENT")) {
        add("input_permission", "Allow the input permission for the computer control plugin in PAI.");
        input = false;
    }
    if (backend == "xtest") {
        std::string why;
        if (!pai::cc::x11::Available(&why)) {
            add("xtest", "X11 input needs the XTEST extension: " + why + ".");
            input = false;
        }
    } else if (session == "wayland" || backend == "portal") {
        // The portal is asked on first use; here only say whether a portal service exists at all.
        const std::string name = pai::sys::EnvString("PAI_PORTAL_BUS_NAME", "org.freedesktop.portal.Desktop");
        const auto cmd = pai::sys::RunCommand({"busctl", "--user", "--no-pager", "status", name}, 3);
        if (cmd.exit_code != 0 && pai::sys::CommandExists("busctl")) {
            add("input_portal", "No desktop portal with remote-desktop support (xdg-desktop-portal and your desktop's backend) is running, so input cannot be sent on Wayland.");
            input = false;
        }
    }

    const bool ready = missing.empty();
    ResultMap r = {
        {"ok", "true"},
        {"ready", ready ? "true" : "false"},
        {"platform", "linux"},
        {"session", session},
        {"input_backend", backend},
        {"missing", JsonStringArray(missing)},
        {"how_to_fix", how.empty() ? "null" : JsonString(how)},
        {"features", std::string("{\"tree\":") + (tree ? "true" : "false") + ",\"screenshot\":" + (screenshot ? "true" : "false") +
                         ",\"ocr\":false,\"input\":" + (input ? "true" : "false") + "}"},
    };
    return r;
}

// ------------------------------------------------------------------------------- observe

bool ReadFile(const std::string& path, std::string& out) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return false;
    out.assign(std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>());
    return true;
}

ResultMap Observe(const Params& p, std::string& error) {
    if (!Consent("PAI_CC_SCREEN_CONSENT")) return Failed(error, NotConsented("screen", "PAI_CC_SCREEN_CONSENT"));
    ObserveArgs a;
    if (Fail f = ParseObserve(p, a); !f.ok()) return Failed(error, f);

    long x11_pid = 0;
    std::string x11_title;
    pai::cc::x11::ActiveWindow(x11_pid, x11_title);

    ResultMap out = {{"ok", "true"}};
    atspi::Observation obs;
    std::string note;
    if (a.include_tree) {
        obs = atspi::ObserveActiveWindow(a.limits, x11_pid);
        if (!obs.ok && !a.include_screenshot) {
            return Failed(error, {obs.error, obs.error == "a11y_unavailable"
                                                 ? "the accessibility bus is not running; computer.status says how to fix it. Retry with include_screenshot=true to see the screen without the tree."
                                                 : "no accessible window is open; launch an app (computer.apps.launch) or retry with include_screenshot=true"},
                          {{"detail", obs.detail}});
        }
        if (!obs.ok) note = obs.detail + "; only the screenshot is available";
    }

    // Make this the current observation (ids are valid only within it).
    std::string observation_id;
    {
        std::lock_guard<std::mutex> lock(g_obs_mu);
        observation_id = Store().Begin(obs.ok ? obs.tree.handles : std::map<std::string, Handle>{});
    }
    out["observation_id"] = observation_id;

    // Screenshot (on request).
    int shot_w = 0, shot_h = 0;
    std::string screenshot_json = "null";
    if (a.include_screenshot) {
        auto shot = pai::capture::CaptureScreen(EnvInt("PAI_CC_SCREENSHOT_TIMEOUT", 60, 1, 300));
        if (!shot.ok && SessionKind() == "x11" && shot.error != "capture_denied" && shot.error != "capture_timeout") {
            // A plain X11 session without a portal or a capture tool: read the screen from the X server directly.
            // (A user's "no" in a portal dialog is final and is never sidestepped.)
            pai::capture::Shot x;
            std::string e;
            x.png_path = pai::sys::EnvString("XDG_RUNTIME_DIR", "/tmp") + "/pai_cc_x11_" + std::to_string(::getpid()) + ".png";
            if (pai::cc::x11::CaptureScreenPng(x.png_path, x.width, x.height, e)) { x.ok = true; x.tool = "x11"; shot = x; }
        }
        if (!shot.ok) {
            out["screenshot_error"] = shot.error;
            note += (note.empty() ? "" : "; ") + std::string("screenshot failed: ") + shot.error + " (" + shot.detail + ")";
        } else {
            std::string bytes;
            const bool read = ReadFile(shot.png_path, bytes);
            pai::capture::Cleanup(shot);
            if (!read || bytes.empty()) {
                out["screenshot_error"] = "capture_failed";
            } else if (bytes.size() > kMaxScreenshotBytes) {
                out["screenshot_error"] = "screenshot_too_large";
                note += (note.empty() ? "" : "; ") + std::string("the screenshot is too large to return");
            } else {
                shot_w = shot.width;
                shot_h = shot.height;
                screenshot_json = "{\"mime\":\"image/png\",\"base64\":\"" + Base64(bytes) + "\",\"width\":" +
                                  std::to_string(shot.width) + ",\"height\":" + std::to_string(shot.height) + "}";
            }
        }
    }

    // Screen size: the X server, else the input session's desktop, else the screenshot, else what the tree spans.
    int sw = 0, sh = 0;
    if (!pai::cc::x11::ScreenSize(sw, sh) || SessionKind() == "wayland") {
        int w = 0, h = 0;
        if (Backend().DesktopSize(w, h)) { sw = w; sh = h; }
        else if (shot_w > 0) { sw = shot_w; sh = shot_h; }
    }
    if (sw <= 0 && obs.ok) {
        int mx = 0, my = 0;
        std::vector<const Node*> stack;
        for (const auto& r : obs.tree.roots) stack.push_back(&r);
        while (!stack.empty()) {
            const Node* n = stack.back();
            stack.pop_back();
            mx = std::max(mx, n->bounds.x + n->bounds.w);
            my = std::max(my, n->bounds.y + n->bounds.h);
            for (const auto& c : n->children) stack.push_back(&c);
        }
        sw = mx;
        sh = my;
    }
    out["screen"] = "{\"width\":" + std::to_string(sw) + ",\"height\":" + std::to_string(sh) + "}";

    if (obs.ok) {
        out["app"] = "{\"id\":" + JsonString(obs.app.id) + ",\"name\":" + JsonString(obs.app.name) + ",\"title\":" + JsonString(obs.app.title) + "}";
        out["tree"] = TreeJson(obs.tree);
        out["truncated"] = obs.tree.truncated ? "true" : "false";
        out["node_count"] = std::to_string(obs.tree.count);
        if (obs.tree.count <= 1) note += (note.empty() ? "" : "; ") + std::string("the window exposes almost no accessibility data: use include_screenshot=true and coordinates");
    } else {
        const std::string title = x11_title;
        out["app"] = title.empty() ? "null" : "{\"id\":\"\",\"name\":\"\",\"title\":" + JsonString(title) + "}";
        out["tree"] = "null";
        out["truncated"] = "false";
    }
    out["screenshot"] = screenshot_json;
    if (!note.empty()) out["hint"] = note;
    return out;
}

// -------------------------------------------------------------------------------- input

Fail InputGuard() {
    if (!Consent("PAI_CC_INPUT_CONSENT")) {
        StopBackends();         // the user revoked it while a portal session was open: end it (drops the desktop's indicator)
        return NotConsented("input", "PAI_CC_INPUT_CONSENT");
    }
    return {};
}

ResultMap Click(const Params& p, std::string& error) {
    ClickArgs a;
    if (Fail f = ParseClick(p, a); !f.ok()) return Failed(error, f);
    if (Fail f = InputGuard(); !f.ok()) return Failed(error, f);
    Resolved r;
    if (Fail f = ResolveTarget(a.target, r); !f.ok()) return Failed(error, f);

    // 1. A semantic action: no pointer movement, works even where synthetic input is not allowed.
    if (r.by_element && a.button == "left" && a.count == 1) {
        std::string which, e;
        std::lock_guard<std::mutex> lock(g_action);
        if (ConsumeEvents(1) && atspi::DoClickAction(r.handle, which, e)) {
            return {{"ok", "true"}, {"method", "accessibility_action"}, {"action", which}, {"target", TargetJson(r)}};
        }
    }
    // 2. Pointer events at the element's centre / the given point.
    if (!r.has_position) {
        return Failed(error, {"element_has_no_position", "this element exposes no on-screen position; use coordinates from a screenshot"});
    }
    InputBackend* be = nullptr;
    if (Fail f = ReadyBackend(be); !f.ok()) return Failed(error, f);
    if (!ConsumeEvents(1 + 2 * a.count)) return Failed(error, {"rate_limited", "too much input in the last minute"});
    const int code = a.button == "left" ? kBtnLeft : a.button == "right" ? kBtnRight : kBtnMiddle;

    std::lock_guard<std::mutex> lock(g_action);
    std::string e;
    if (!be->Move(r.x, r.y, e)) return Failed(error, {e, "that point is not on a screen this plugin may use"});
    Pause(40);                                           // let the pointer arrive before pressing
    for (int i = 0; i < a.count; ++i) {
        if (!be->Button(code, true, e)) return Failed(error, {e, ""});
        Pause(30);
        if (!be->Button(code, false, e)) return Failed(error, {e, ""});         // never leave a button pressed on purpose
        if (i + 1 < a.count) Pause(60);                  // inside the double-click window
    }
    return {{"ok", "true"}, {"method", "pointer"}, {"button", a.button}, {"count", std::to_string(a.count)}, {"target", TargetJson(r)}};
}

ResultMap LongPress(const Params& p, std::string& error) {
    LongPressArgs a;
    if (Fail f = ParseLongPress(p, a); !f.ok()) return Failed(error, f);
    if (Fail f = InputGuard(); !f.ok()) return Failed(error, f);
    Resolved r;
    if (Fail f = ResolveTarget(a.target, r); !f.ok()) return Failed(error, f);
    if (!r.has_position) return Failed(error, {"element_has_no_position", "this element exposes no on-screen position; use coordinates"});
    InputBackend* be = nullptr;
    if (Fail f = ReadyBackend(be); !f.ok()) return Failed(error, f);
    if (!ConsumeEvents(3)) return Failed(error, {"rate_limited", "too much input in the last minute"});

    std::lock_guard<std::mutex> lock(g_action);
    std::string e;
    if (!be->Move(r.x, r.y, e)) return Failed(error, {e, "that point is not on a screen this plugin may use"});
    Pause(40);
    if (!be->Button(kBtnLeft, true, e)) return Failed(error, {e, ""});
    Pause(a.duration_ms);
    std::string e2;
    if (!be->Button(kBtnLeft, false, e2)) return Failed(error, {e2, "the button may still be held: press and release it by clicking"});
    return {{"ok", "true"}, {"duration_ms", std::to_string(a.duration_ms)}, {"target", TargetJson(r)}};
}

bool PressKeysym(InputBackend* be, int32_t sym, std::string& e) {
    return be->Key(sym, true, e) && be->Key(sym, false, e);
}

ResultMap Type(const Params& p, std::string& error) {
    TypeArgs a;
    if (Fail f = ParseType(p, a); !f.ok()) return Failed(error, f);
    if (Fail f = InputGuard(); !f.ok()) return Failed(error, f);

    std::vector<uint32_t> cps;
    pai::keys::DecodeUtf8(a.text, cps);                  // validated by ParseType
    Resolved r;
    if (!a.element_id.empty()) {
        Target t;
        t.element_id = a.element_id;
        t.observation_id = a.observation_id;
        if (Fail f = ResolveTarget(t, r); !f.ok()) return Failed(error, f);
        std::string e;
        // A text field takes the text directly: no focus tricks, no keymap, works for any character.
        if (atspi::IsEditable(r.handle)) {
            std::lock_guard<std::mutex> lock(g_action);
            atspi::Focus(r.handle, e);
            if (ConsumeEvents(2) && atspi::InsertText(r.handle, a.text, a.clear_first, e)) {
                return {{"ok", "true"}, {"method", "accessibility_text"}, {"typed", std::to_string(cps.size())}, {"target", TargetJson(r)}};
            }
        }
        atspi::Focus(r.handle, e);                       // not an editable text: focus it, then use the keyboard
    }

    InputBackend* be = nullptr;
    if (Fail f = ReadyBackend(be); !f.ok()) return Failed(error, f);
    if (!ConsumeEvents(static_cast<int>(cps.size()) * 2 + (a.clear_first ? 6 : 0)))
        return Failed(error, {"rate_limited", "too much input in the last minute"});

    std::lock_guard<std::mutex> lock(g_action);
    std::string e;
    if (a.clear_first) {                                 // select all, then the typing replaces it
        if (!be->Key(0xFFE3, true, e) || !PressKeysym(be, 'a', e)) { std::string ig; be->Key(0xFFE3, false, ig); return Failed(error, {e, ""}); }
        be->Key(0xFFE3, false, e);
        PressKeysym(be, 0xFF08, e);                      // BackSpace
    }
    size_t typed = 0;
    for (uint32_t cp : cps) {
        int32_t k;
        pai::keys::KeysymForChar(cp, k);
        if (!PressKeysym(be, k, e)) {
            ResultMap partial = {{"typed", std::to_string(typed)}};
            return Failed(error, {e, "typing stopped part way"}, partial);
        }
        ++typed;
        Pause(8);
    }
    return {{"ok", "true"}, {"method", "keyboard"}, {"typed", std::to_string(typed)}};
}

ResultMap Key(const Params& p, std::string& error) {
    std::vector<int32_t> mods;
    int32_t key = 0;
    std::string spec;
    if (Fail f = ParseKey(p, mods, key, spec); !f.ok()) return Failed(error, f);
    if (Fail f = InputGuard(); !f.ok()) return Failed(error, f);
    InputBackend* be = nullptr;
    if (Fail f = ReadyBackend(be); !f.ok()) return Failed(error, f);
    if (!ConsumeEvents(static_cast<int>(2 + 2 * mods.size()))) return Failed(error, {"rate_limited", "too much input in the last minute"});

    std::lock_guard<std::mutex> lock(g_action);
    std::string e;
    size_t held = 0;
    bool ok = true;
    for (int32_t m : mods) { if (!(ok = be->Key(m, true, e))) break; ++held; }
    if (ok) ok = be->Key(key, true, e) && be->Key(key, false, e);
    for (size_t i = held; i-- > 0;) {                    // ALWAYS release what we pressed, even after a failure
        std::string ignored;
        be->Key(mods[i], false, ignored);
    }
    if (!ok) return Failed(error, {e, ""});
    return {{"ok", "true"}, {"keys", spec}};
}

ResultMap Scroll(const Params& p, std::string& error) {
    ScrollArgs a;
    if (Fail f = ParseScroll(p, a); !f.ok()) return Failed(error, f);
    if (Fail f = InputGuard(); !f.ok()) return Failed(error, f);
    Resolved r;
    const bool targeted = !a.target.element_id.empty() || a.target.has_point;
    if (targeted) {
        if (Fail f = ResolveTarget(a.target, r); !f.ok()) return Failed(error, f);
        if (!r.has_position) return Failed(error, {"element_has_no_position", "this element exposes no on-screen position; use coordinates"});
    }
    InputBackend* be = nullptr;
    if (Fail f = ReadyBackend(be); !f.ok()) return Failed(error, f);
    if (!ConsumeEvents(2 + a.amount)) return Failed(error, {"rate_limited", "too much input in the last minute"});
    const int axis = (a.direction == "up" || a.direction == "down") ? 0 : 1;
    const int steps = (a.direction == "down" || a.direction == "right") ? a.amount : -a.amount;

    std::lock_guard<std::mutex> lock(g_action);
    std::string e;
    if (targeted) {                                      // the wheel acts on whatever is under the pointer
        if (!be->Move(r.x, r.y, e)) return Failed(error, {e, "that point is not on a screen this plugin may use"});
        Pause(40);
    }
    if (!be->Wheel(axis, steps, e)) return Failed(error, {e, ""});
    return {{"ok", "true"}, {"direction", a.direction}, {"amount", std::to_string(a.amount)}};
}

ResultMap Swipe(const Params& p, std::string& error) {
    SwipeArgs a;
    if (Fail f = ParseSwipe(p, a); !f.ok()) return Failed(error, f);
    if (Fail f = InputGuard(); !f.ok()) return Failed(error, f);
    InputBackend* be = nullptr;
    if (Fail f = ReadyBackend(be); !f.ok()) return Failed(error, f);
    const int steps = std::max(4, std::min(120, a.duration_ms / 16));
    if (!ConsumeEvents(steps + 3)) return Failed(error, {"rate_limited", "too much input in the last minute"});

    std::lock_guard<std::mutex> lock(g_action);
    std::string e;
    if (!be->Move(a.from.x, a.from.y, e)) return Failed(error, {e, "the start point is not on a screen this plugin may use"});
    Pause(30);
    if (!be->Button(kBtnLeft, true, e)) return Failed(error, {e, ""});
    bool ok = true;
    for (int i = 1; i <= steps && ok; ++i) {
        const double t = static_cast<double>(i) / steps;
        ok = be->Move(a.from.x + (a.to.x - a.from.x) * t, a.from.y + (a.to.y - a.from.y) * t, e);
        Pause(a.duration_ms / steps);
    }
    Pause(30);
    std::string e2;
    be->Button(kBtnLeft, false, e2);                     // ALWAYS release, even if the drag failed on the way
    if (!ok) return Failed(error, {e, "the drag stopped part way (the end point may be off the screen)"});
    return {{"ok", "true"}, {"from", "{\"x\":" + Num(a.from.x) + ",\"y\":" + Num(a.from.y) + "}"},
            {"to", "{\"x\":" + Num(a.to.x) + ",\"y\":" + Num(a.to.y) + "}"}};
}

// ---------------------------------------------------------------------------------- apps

ResultMap AppsList(const Params& p, std::string& error) {
    (void)error;
    auto it = p.find("query");
    const auto all = pai::apps::LoadEntries();
    auto found = pai::apps::Filter(all, it == p.end() ? "" : it->second, false);
    const bool truncated = static_cast<int>(found.size()) > kMaxAppsListed;
    if (truncated) found.resize(kMaxAppsListed);
    std::string json = "[";
    for (size_t i = 0; i < found.size(); ++i)
        json += (i ? "," : "") + std::string("{\"id\":") + JsonString(found[i].id) + ",\"name\":" + JsonString(found[i].name) + "}";
    json += "]";
    ResultMap r = {{"ok", "true"}, {"apps", json}, {"count", std::to_string(found.size())}};
    if (truncated) r["hint"] = "more than " + std::to_string(kMaxAppsListed) + " apps match; pass query to narrow it down";
    return r;
}

ResultMap AppsLaunch(const Params& p, std::string& error) {
    if (Fail f = InputGuard(); !f.ok()) return Failed(error, f);       // starting programs on the user's behalf is control
    auto get = [&](const char* k) { auto i = p.find(k); return i == p.end() ? std::string() : i->second; };
    std::string query = get("id").empty() ? get("name") : get("id");
    if (query.empty()) return Failed(error, {"missing_required_param", "id or name"});
    const auto all = pai::apps::LoadEntries();
    pai::apps::Entry e;
    if (!pai::apps::FindEntry(all, query, e)) {
        auto hits = pai::apps::Filter(all, query, false);
        if (hits.size() == 1) e = hits[0];
        else if (hits.empty()) return Failed(error, {"app_not_found", "no installed application matches '" + query + "'; computer.apps.list shows what is installed"});
        else {
            std::string names;
            for (size_t i = 0; i < hits.size() && i < 5; ++i) names += (i ? ", " : "") + hits[i].id;
            return Failed(error, {"app_ambiguous", "several applications match '" + query + "': " + names + "; pass an exact id"});
        }
    }
    if (e.terminal) return Failed(error, {"terminal_app_not_supported", e.name + " is a terminal program and needs a terminal to run in"});
    const auto argv = pai::apps::LaunchArgv(e);
    if (argv.empty() || !pai::sys::SpawnDetached(argv)) return Failed(error, {"launch_failed", "could not start " + e.name});
    return {{"ok", "true"}, {"launched", "{\"id\":" + JsonString(e.id) + ",\"name\":" + JsonString(e.name) + "}"},
            {"hint", "the window may take a moment: call computer.observe to confirm it opened"}};
}

ResultMap AppsForeground(std::string& error) {
    long pid = 0;
    std::string title;
    pai::cc::x11::ActiveWindow(pid, title);
    atspi::AppInfo app;
    std::string e;
    if (atspi::Foreground(app, pid, e)) {
        return {{"ok", "true"}, {"id", app.id}, {"name", app.name}, {"title", app.title}};
    }
    if (!title.empty()) return {{"ok", "true"}, {"id", ""}, {"name", ""}, {"title", title}};
    return Failed(error, {e.empty() ? "no_window" : e, "no foreground window could be found; computer.status says whether accessibility is available"});
}

// ------------------------------------------------------------------------------- dispatch

ResultMap Dispatch(const std::string& capability, const Params& params, std::string& error) {
    if (capability == "computer.status") return Status();
    if (capability == "computer.observe") return Observe(params, error);
    if (capability == "computer.click") return Click(params, error);
    if (capability == "computer.long_press") return LongPress(params, error);
    if (capability == "computer.type") return Type(params, error);
    if (capability == "computer.key") return Key(params, error);
    if (capability == "computer.scroll") return Scroll(params, error);
    if (capability == "computer.swipe") return Swipe(params, error);
    if (capability == "computer.apps.list") return AppsList(params, error);
    if (capability == "computer.apps.launch") return AppsLaunch(params, error);
    if (capability == "computer.apps.foreground") return AppsForeground(error);
    error = "unknown_capability";
    return {};
}

}  // namespace

PAI_PLUGIN_REGISTER(
    "pai.computer_control",
    ({"computer.status", "computer.observe", "computer.click", "computer.long_press", "computer.type", "computer.key",
      "computer.scroll", "computer.swipe", "computer.apps.list", "computer.apps.launch", "computer.apps.foreground"}),
    ({"screen", "input"}),
    &Dispatch
)

// Keyboard and mouse input on the user's behalf, through the desktop's remote-desktop portal.
//
// This can do anything the user can do on screen, so the safety is in here and not left to callers:
//   * the app's consent (PAI_INPUT_CONSENT=1, set when the user allows the "input" permission),
//   * no events at all until input.start, where the DESKTOP shows its own approval dialog (and its
//     "remote control active" indicator; the user can revoke it there at any time),
//   * the devices/screens the user actually granted are all that can be used,
//   * every size, count and coordinate is bounded; clicks outside the shared screens are refused,
//   * a per-minute cap on events, so a runaway agent cannot type forever.
#include "pai_plugin_common.h"
#include "pai_portal.h"
#include "pai_system_helpers.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <deque>
#include <map>
#include <mutex>
#include <thread>

namespace {

using pai::plugin::ResultMap;
using Clock = std::chrono::steady_clock;

constexpr size_t kMaxTextChars = 500;
constexpr int kMaxKeyRepeat = 50;
constexpr int kMaxClicks = 3;
constexpr int kMaxScrollSteps = 50;
constexpr int kMaxDelayMs = 200;
constexpr double kMaxSpace = 100000;

constexpr int kBtnLeft = 0x110, kBtnRight = 0x111, kBtnMiddle = 0x112;     // linux/input-event-codes.h
constexpr int32_t kKeysymReturn = 0xFF0D, kKeysymTab = 0xFF09;

// One action at a time: a typed sentence must not interleave with a click from a concurrent call.
std::mutex g_action;

ResultMap Fail(std::string& error, const std::string& code, ResultMap extra = {}) {
    error = code;
    extra["ok"] = "false";
    extra["error"] = code;
    return extra;
}

bool EnvEnabled(const char* name) { return pai::sys::EnvString(name, "0") == "1"; }

int EnvInt(const char* name, int fallback, int lo, int hi) {
    const std::string s = pai::sys::EnvString(name, "");
    int v = s.empty() ? fallback : std::atoi(s.c_str());
    if (v == 0 && !s.empty()) v = fallback;                 // not a number
    return std::max(lo, std::min(hi, v));
}

// ----------------------------------------------------------------------- rate limit

// Sliding one-minute window of injected events.
bool ConsumeEvents(int n) {
    static std::mutex m;
    static std::deque<Clock::time_point> window;
    const int cap = EnvInt("PAI_INPUT_MAX_EVENTS_PER_MIN", 6000, 10, 1000000);
    std::lock_guard<std::mutex> lock(m);
    const auto now = Clock::now();
    while (!window.empty() && now - window.front() > std::chrono::minutes(1)) window.pop_front();
    if (static_cast<int>(window.size()) + n > cap) return false;
    for (int i = 0; i < n; ++i) window.push_back(now);
    return true;
}

void Pause(int ms) {
    if (ms > 0) std::this_thread::sleep_for(std::chrono::milliseconds(ms));
}

// ------------------------------------------------------------------------- parameters

bool ParseNumber(const ResultMap& p, const char* key, double& out) {
    auto it = p.find(key);
    if (it == p.end() || it->second.empty()) return false;
    char* end = nullptr;
    out = std::strtod(it->second.c_str(), &end);
    return end && *end == '\0' && std::isfinite(out);
}

bool ParseInt(const ResultMap& p, const char* key, int& out, int fallback) {
    auto it = p.find(key);
    if (it == p.end() || it->second.empty()) { out = fallback; return true; }
    char* end = nullptr;
    long v = std::strtol(it->second.c_str(), &end, 10);
    if (!end || *end != '\0') return false;
    out = static_cast<int>(std::max(-1000000L, std::min(1000000L, v)));
    return true;
}

// ----------------------------------------------------------------------------- keys

std::string Lower(std::string s) {
    for (auto& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return s;
}

// Strict UTF-8 -> code points. false on malformed input (overlongs, surrogates, > U+10FFFF).
bool DecodeUtf8(const std::string& s, std::vector<uint32_t>& out) {
    size_t i = 0;
    while (i < s.size()) {
        unsigned char c = static_cast<unsigned char>(s[i]);
        uint32_t cp;
        int extra;
        if (c < 0x80) { cp = c; extra = 0; }
        else if ((c & 0xE0) == 0xC0) { cp = c & 0x1F; extra = 1; }
        else if ((c & 0xF0) == 0xE0) { cp = c & 0x0F; extra = 2; }
        else if ((c & 0xF8) == 0xF0) { cp = c & 0x07; extra = 3; }
        else return false;
        for (int k = 1; k <= extra; ++k) {
            if (i + k >= s.size()) return false;
            unsigned char cc = static_cast<unsigned char>(s[i + k]);
            if ((cc & 0xC0) != 0x80) return false;
            cp = (cp << 6) | (cc & 0x3F);
        }
        static const uint32_t min_for[4] = {0, 0x80, 0x800, 0x10000};
        if (cp < min_for[extra] || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) return false;
        out.push_back(cp);
        i += extra + 1;
    }
    return true;
}

// X11 keysym for a typed character: Latin-1 is itself; everything else is 0x01000000 + code point.
bool KeysymForChar(uint32_t cp, int32_t& keysym) {
    if (cp == '\n') { keysym = kKeysymReturn; return true; }
    if (cp == '\t') { keysym = kKeysymTab; return true; }
    if (cp < 0x20 || cp == 0x7F || (cp >= 0x80 && cp < 0xA0)) return false;      // control characters
    keysym = (cp <= 0xFF) ? static_cast<int32_t>(cp) : static_cast<int32_t>(0x01000000 | cp);
    return true;
}

const std::map<std::string, int32_t>& KeyNames() {
    static const std::map<std::string, int32_t> names = [] {
        std::map<std::string, int32_t> m = {
            {"return", 0xFF0D}, {"enter", 0xFF0D}, {"escape", 0xFF1B}, {"esc", 0xFF1B}, {"tab", 0xFF09},
            {"backspace", 0xFF08}, {"delete", 0xFFFF}, {"del", 0xFFFF}, {"insert", 0xFF63},
            {"home", 0xFF50}, {"end", 0xFF57}, {"pageup", 0xFF55}, {"page_up", 0xFF55},
            {"pagedown", 0xFF56}, {"page_down", 0xFF56},
            {"left", 0xFF51}, {"up", 0xFF52}, {"right", 0xFF53}, {"down", 0xFF54},
            {"space", 0x20}, {"plus", 0x2B}, {"minus", 0x2D}, {"comma", 0x2C}, {"period", 0x2E},
            {"slash", 0x2F}, {"backslash", 0x5C},
        };
        for (int i = 1; i <= 12; ++i) m["f" + std::to_string(i)] = 0xFFBE + i - 1;
        return m;
    }();
    return names;
}

const std::map<std::string, int32_t>& ModifierNames() {
    static const std::map<std::string, int32_t> m = {
        {"ctrl", 0xFFE3}, {"control", 0xFFE3}, {"shift", 0xFFE1}, {"alt", 0xFFE9},
        {"super", 0xFFEB}, {"meta", 0xFFEB}, {"win", 0xFFEB}, {"cmd", 0xFFEB},
    };
    return m;
}

bool ResolveKey(const std::string& token, int32_t& keysym) {
    const std::string t = Lower(token);
    auto it = KeyNames().find(t);
    if (it != KeyNames().end()) { keysym = it->second; return true; }
    std::vector<uint32_t> cps;
    if (DecodeUtf8(token, cps) && cps.size() == 1) return KeysymForChar(cps[0], keysym);   // "a", "é", "5"
    return false;
}

// ---------------------------------------------------------------------------- guards

// Common preconditions of every capability that injects events.
bool Guard(std::string& error, ResultMap& out, bool need_keyboard, bool need_pointer) {
    if (!EnvEnabled("PAI_INPUT_CONSENT")) {
        // The user revoked the permission while a session was open: end it now (this also drops the
        // desktop's "remote control active" indicator) instead of leaving it open but unusable.
        pai::portal::InputSession::Get().Stop();
        out = Fail(error, "input_not_consented",
                   {{"hint", "allow the input permission for PAI (or set PAI_INPUT_CONSENT=1)"}});
        return false;
    }
    auto st = pai::portal::InputSession::Get().Status();
    if (!st.active) {
        out = Fail(error, "input_not_started", {{"hint", "call input.start first; the desktop will ask the user to approve"}});
        return false;
    }
    if (need_keyboard && !(st.devices & 1)) { out = Fail(error, "keyboard_not_granted", {{"hint", "the user did not allow the keyboard"}}); return false; }
    if (need_pointer && !(st.devices & 2)) { out = Fail(error, "pointer_not_granted", {{"hint", "the user did not allow the pointer"}}); return false; }
    return true;
}

std::string ScreensJson(const std::vector<pai::portal::Stream>& streams) {
    std::string s = "[";
    for (size_t i = 0; i < streams.size(); ++i) {
        const auto& t = streams[i];
        s += (i ? "," : "");
        s += "{\"x\":" + std::to_string(t.x) + ",\"y\":" + std::to_string(t.y) + ",\"w\":" + std::to_string(t.w) +
             ",\"h\":" + std::to_string(t.h) + "}";
    }
    return s + "]";
}

struct Box { int x = 0, y = 0, w = 0, h = 0; };

Box DesktopBox(const std::vector<pai::portal::Stream>& streams) {
    if (streams.empty()) return {};
    int x0 = streams[0].x, y0 = streams[0].y, x1 = streams[0].x + streams[0].w, y1 = streams[0].y + streams[0].h;
    for (const auto& s : streams) {
        x0 = std::min(x0, s.x); y0 = std::min(y0, s.y);
        x1 = std::max(x1, s.x + s.w); y1 = std::max(y1, s.y + s.h);
    }
    return {x0, y0, x1 - x0, y1 - y0};
}

// A point given in the caller's coordinate space -> (stream, stream-local coordinates).
// With spaceWidth/spaceHeight the point is in that space (e.g. the screenshot's pixels) and is scaled
// onto the whole desktop; without them it is in the desktop's logical pixels.
bool ResolvePoint(const ResultMap& p, ResultMap& fail, std::string& error, uint32_t& node, double& lx, double& ly) {
    double x = 0, y = 0;
    if (!ParseNumber(p, "x", x) || !ParseNumber(p, "y", y)) {
        fail = Fail(error, "missing_coordinates", {{"hint", "pass numeric x and y"}});
        return false;
    }
    auto st = pai::portal::InputSession::Get().Status();
    if (st.streams.empty()) {
        fail = Fail(error, "no_screens_shared", {{"hint", "the user shared no screen; call input.start again and select a screen"}});
        return false;
    }
    const Box box = DesktopBox(st.streams);

    double sw = 0, sh = 0;
    const bool has_w = ParseNumber(p, "spaceWidth", sw), has_h = ParseNumber(p, "spaceHeight", sh);
    if (has_w != has_h || (has_w && (sw <= 0 || sh <= 0 || sw > kMaxSpace || sh > kMaxSpace))) {
        fail = Fail(error, "invalid_space", {{"hint", "spaceWidth and spaceHeight must both be given, positive, and sane"}});
        return false;
    }
    double gx = x, gy = y;
    if (has_w) {
        if (x < 0 || y < 0 || x > sw || y > sh) {
            fail = Fail(error, "point_outside_screen", {{"hint", "x/y must lie inside spaceWidth x spaceHeight"}});
            return false;
        }
        gx = box.x + x * box.w / sw;
        gy = box.y + y * box.h / sh;
    }
    // A stream's far edges are exclusive, so the exact edge (e.g. the last pixel of a screenshot, scaled)
    // belongs to the last pixel. ONLY the exact edge: anything further out is off the shared screens and
    // must be refused, never quietly moved onto them (a click at the wrong place does the wrong thing).
    const double right = box.x + box.w, bottom = box.y + box.h;
    if (gx >= right && gx <= right + 1e-6) gx = right - 0.5;
    if (gy >= bottom && gy <= bottom + 1e-6) gy = bottom - 0.5;
    for (const auto& s : st.streams) {
        if (gx >= s.x && gx < s.x + s.w && gy >= s.y && gy < s.y + s.h) {
            node = s.node_id;
            lx = gx - s.x;
            ly = gy - s.y;
            return true;
        }
    }
    fail = Fail(error, "point_outside_shared_screens",
                {{"hint", "that point is on a screen the user did not share; input.status lists the shared screens"}});
    return false;
}

// ------------------------------------------------------------------------ capabilities

ResultMap StatusMap() {
    auto st = pai::portal::InputSession::Get().Status();
    const Box b = DesktopBox(st.streams);
    return {
        {"ok", "true"},
        {"active", st.active ? "true" : "false"},
        {"keyboard", (st.devices & 1) ? "true" : "false"},
        {"pointer", (st.devices & 2) ? "true" : "false"},
        {"screens", ScreensJson(st.streams)},
        {"desktop", std::to_string(b.x) + "," + std::to_string(b.y) + "," + std::to_string(b.w) + "," + std::to_string(b.h)},
    };
}

ResultMap InputStart(std::string& error) {
    if (!EnvEnabled("PAI_INPUT_CONSENT")) {
        return Fail(error, "input_not_consented",
                    {{"hint", "allow the input permission for PAI (or set PAI_INPUT_CONSENT=1)"}});
    }
    const int timeout = EnvInt("PAI_INPUT_START_TIMEOUT", 120, 1, 600);
    std::string code, detail;
    const bool was_active = pai::portal::InputSession::Get().Status().active;
    if (!pai::portal::InputSession::Get().Start(timeout, code, detail)) {
        std::string hint = "the desktop's remote-control permission could not be obtained";
        std::string err = "input_failed";
        if (code == "cancelled") { err = "input_denied"; hint = "the user declined remote control in the desktop's dialog"; }
        else if (code == "timeout") { err = "input_timeout"; hint = "no answer to the desktop's permission dialog"; }
        else if (code == "portal_unavailable") { err = "portal_unavailable"; hint = "no desktop portal with remote-desktop support (xdg-desktop-portal) is running"; }
        return Fail(error, err, {{"detail", detail}, {"hint", hint}});
    }
    ResultMap r = StatusMap();
    r["already_active"] = was_active ? "true" : "false";
    if (r["screens"] == "[]") r["hint"] = "no screen was shared: absolute mouse positioning is unavailable (keyboard still works)";
    else r["hint"] = "if you have several monitors the user should share all of them; input.status lists what is shared";
    return r;
}

ResultMap KeyboardType(const ResultMap& p, std::string& error) {
    ResultMap guard;
    if (!Guard(error, guard, true, false)) return guard;
    if (!p.count("text") || p.at("text").empty()) { return Fail(error, "missing_required_param", {{"param", "text"}}); }
    std::vector<uint32_t> cps;
    if (!DecodeUtf8(p.at("text"), cps)) return Fail(error, "invalid_text", {{"hint", "text must be valid UTF-8"}});
    if (cps.size() > kMaxTextChars) {
        return Fail(error, "text_too_long", {{"max", std::to_string(kMaxTextChars)}, {"length", std::to_string(cps.size())}});
    }
    std::vector<int32_t> syms;
    for (uint32_t cp : cps) {
        int32_t k;
        if (!KeysymForChar(cp, k)) return Fail(error, "invalid_text", {{"hint", "control characters other than newline and tab cannot be typed"}});
        syms.push_back(k);
    }
    int delay = 8;
    if (!ParseInt(p, "delayMs", delay, 8) || delay < 0 || delay > kMaxDelayMs) {
        return Fail(error, "invalid_delay", {{"hint", "delayMs must be 0-" + std::to_string(kMaxDelayMs)}});
    }
    if (!ConsumeEvents(static_cast<int>(syms.size()) * 2)) return Fail(error, "rate_limited", {{"hint", "too much input in the last minute"}});

    std::lock_guard<std::mutex> lock(g_action);
    auto& session = pai::portal::InputSession::Get();
    size_t typed = 0;
    for (int32_t k : syms) {
        std::string e;
        if (!session.KeyboardKeysym(k, true, e)) return Fail(error, e, {{"typed", std::to_string(typed)}});
        const bool released = session.KeyboardKeysym(k, false, e);
        if (!released) return Fail(error, e, {{"typed", std::to_string(typed)}});
        ++typed;
        Pause(delay);
    }
    return {{"ok", "true"}, {"typed", std::to_string(typed)}};
}

ResultMap KeyboardKey(const ResultMap& p, std::string& error) {
    ResultMap guard;
    if (!Guard(error, guard, true, false)) return guard;
    if (!p.count("keys") || p.at("keys").empty()) return Fail(error, "missing_required_param", {{"param", "keys"}});

    // "ctrl+shift+t": modifiers, then one key.
    std::vector<std::string> tokens;
    size_t pos = 0;
    const std::string& spec = p.at("keys");
    while (true) {
        size_t plus = spec.find('+', pos);
        // a lone "+" at the end means the plus KEY (use "plus"), so an empty token is an error
        tokens.push_back(spec.substr(pos, plus == std::string::npos ? std::string::npos : plus - pos));
        if (plus == std::string::npos) break;
        pos = plus + 1;
    }
    std::vector<int32_t> mods;
    int32_t key = 0;
    for (size_t i = 0; i < tokens.size(); ++i) {
        const std::string t = tokens[i];
        if (t.empty()) return Fail(error, "invalid_keys", {{"hint", "use names like ctrl+shift+t; write the plus key as 'plus'"}});
        if (i + 1 < tokens.size()) {
            auto m = ModifierNames().find(Lower(t));
            if (m == ModifierNames().end()) return Fail(error, "unknown_modifier", {{"key", t}, {"hint", "modifiers: ctrl, shift, alt, super"}});
            mods.push_back(m->second);
        } else if (!ResolveKey(t, key)) {
            auto m = ModifierNames().find(Lower(t));          // a bare modifier press, e.g. "shift" or "super"
            if (m == ModifierNames().end()) return Fail(error, "unknown_key", {{"key", t}, {"hint", "e.g. Return, Escape, Tab, Left, F5, a, 5"}});
            key = m->second;
        }
    }
    int count = 1;
    if (!ParseInt(p, "count", count, 1) || count < 1 || count > kMaxKeyRepeat) {
        return Fail(error, "invalid_count", {{"hint", "count must be 1-" + std::to_string(kMaxKeyRepeat)}});
    }
    if (!ConsumeEvents(count * static_cast<int>(2 + 2 * mods.size()))) return Fail(error, "rate_limited", {{"hint", "too much input in the last minute"}});

    std::lock_guard<std::mutex> lock(g_action);
    auto& session = pai::portal::InputSession::Get();
    std::string e;
    for (int n = 0; n < count; ++n) {
        size_t held = 0;
        bool ok = true;
        for (int32_t m : mods) { if (!(ok = session.KeyboardKeysym(m, true, e))) break; ++held; }
        if (ok) ok = session.KeyboardKeysym(key, true, e) && session.KeyboardKeysym(key, false, e);
        for (size_t i = held; i-- > 0;) {                      // ALWAYS release what we pressed, even after a failure
            std::string ignored;
            session.KeyboardKeysym(mods[i], false, ignored);
        }
        if (!ok) return Fail(error, e);
        Pause(15);
    }
    return {{"ok", "true"}, {"keys", spec}, {"count", std::to_string(count)}};
}

ResultMap MouseMove(const ResultMap& p, std::string& error) {
    ResultMap guard;
    if (!Guard(error, guard, false, true)) return guard;
    uint32_t node = 0;
    double lx = 0, ly = 0;
    if (!ResolvePoint(p, guard, error, node, lx, ly)) return guard;
    if (!ConsumeEvents(1)) return Fail(error, "rate_limited");
    std::lock_guard<std::mutex> lock(g_action);
    std::string e;
    if (!pai::portal::InputSession::Get().PointerMotionAbsolute(node, lx, ly, e)) return Fail(error, e);
    return {{"ok", "true"}, {"screen_x", std::to_string(static_cast<int>(lx))}, {"screen_y", std::to_string(static_cast<int>(ly))}};
}

ResultMap MouseClick(const ResultMap& p, std::string& error) {
    ResultMap guard;
    if (!Guard(error, guard, false, true)) return guard;
    const std::string button = Lower(p.count("button") && !p.at("button").empty() ? p.at("button") : "left");
    int code = button == "left" ? kBtnLeft : button == "right" ? kBtnRight : button == "middle" ? kBtnMiddle : 0;
    if (!code) return Fail(error, "invalid_button", {{"hint", "left, right or middle"}});
    int count = 1;
    if (!ParseInt(p, "count", count, 1) || count < 1 || count > kMaxClicks) {
        return Fail(error, "invalid_count", {{"hint", "count must be 1-" + std::to_string(kMaxClicks)}});
    }
    const bool move = p.count("x") || p.count("y");
    uint32_t node = 0;
    double lx = 0, ly = 0;
    if (move && !ResolvePoint(p, guard, error, node, lx, ly)) return guard;
    if (!ConsumeEvents(1 + 2 * count)) return Fail(error, "rate_limited");

    std::lock_guard<std::mutex> lock(g_action);
    auto& session = pai::portal::InputSession::Get();
    std::string e;
    if (move) {
        if (!session.PointerMotionAbsolute(node, lx, ly, e)) return Fail(error, e);
        Pause(40);                                            // let the pointer arrive before pressing
    }
    for (int i = 0; i < count; ++i) {
        if (!session.PointerButton(code, true, e)) return Fail(error, e);
        Pause(30);
        if (!session.PointerButton(code, false, e)) return Fail(error, e);   // never leave a button pressed on purpose
        if (i + 1 < count) Pause(60);                          // inside the double-click window
    }
    return {{"ok", "true"}, {"button", button}, {"count", std::to_string(count)}, {"moved", move ? "true" : "false"}};
}

ResultMap MouseScroll(const ResultMap& p, std::string& error) {
    ResultMap guard;
    if (!Guard(error, guard, false, true)) return guard;
    int dy = 0, dx = 0;
    if (!ParseInt(p, "dy", dy, 0) || !ParseInt(p, "dx", dx, 0) || std::abs(dy) > kMaxScrollSteps || std::abs(dx) > kMaxScrollSteps) {
        return Fail(error, "invalid_scroll", {{"hint", "dx/dy are whole wheel steps, at most " + std::to_string(kMaxScrollSteps)}});
    }
    if (dx == 0 && dy == 0) return Fail(error, "missing_required_param", {{"param", "dy or dx"}});
    const bool move = p.count("x") || p.count("y");
    uint32_t node = 0;
    double lx = 0, ly = 0;
    if (move && !ResolvePoint(p, guard, error, node, lx, ly)) return guard;
    if (!ConsumeEvents(2)) return Fail(error, "rate_limited");

    std::lock_guard<std::mutex> lock(g_action);
    auto& session = pai::portal::InputSession::Get();
    std::string e;
    if (move) {                                               // scroll lands on whatever is under the pointer
        if (!session.PointerMotionAbsolute(node, lx, ly, e)) return Fail(error, e);
        Pause(40);
    }
    if (dy && !session.PointerAxisDiscrete(0, dy, e)) return Fail(error, e);
    if (dx && !session.PointerAxisDiscrete(1, dx, e)) return Fail(error, e);
    return {{"ok", "true"}, {"dy", std::to_string(dy)}, {"dx", std::to_string(dx)}};
}

ResultMap DispatchInput(const std::string& capability, const ResultMap& params, std::string& error) {
    if (capability == "input.start") return InputStart(error);
    if (capability == "input.status") return StatusMap();
    if (capability == "input.stop") {
        pai::portal::InputSession::Get().Stop();
        return {{"ok", "true"}, {"active", "false"}};
    }
    if (capability == "keyboard.type") return KeyboardType(params, error);
    if (capability == "keyboard.key") return KeyboardKey(params, error);
    if (capability == "mouse.move") return MouseMove(params, error);
    if (capability == "mouse.click") return MouseClick(params, error);
    if (capability == "mouse.scroll") return MouseScroll(params, error);
    error = "unknown_capability";
    return {};
}

}  // namespace

PAI_PLUGIN_REGISTER(
    "pai.input",
    ({"input.start", "input.status", "input.stop", "keyboard.type", "keyboard.key",
      "mouse.move", "mouse.click", "mouse.scroll"}),
    ({"input"}),
    &DispatchInput
)

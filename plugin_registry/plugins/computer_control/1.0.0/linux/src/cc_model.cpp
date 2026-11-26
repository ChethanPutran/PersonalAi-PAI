#include "cc_model.h"

#include "pai_input_keys.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <deque>

namespace pai::cc {

// ================================================================================== text helpers

namespace {

// Length in bytes of the valid UTF-8 sequence starting at s[i], or 0 if it is malformed.
size_t Utf8SeqLen(const std::string& s, size_t i) {
    unsigned char c = static_cast<unsigned char>(s[i]);
    size_t n = c < 0x80 ? 1 : (c & 0xE0) == 0xC0 ? 2 : (c & 0xF0) == 0xE0 ? 3 : (c & 0xF8) == 0xF0 ? 4 : 0;
    if (n == 0 || i + n > s.size()) return 0;
    uint32_t cp = n == 1 ? c : n == 2 ? (c & 0x1F) : n == 3 ? (c & 0x0F) : (c & 0x07);
    for (size_t k = 1; k < n; ++k) {
        unsigned char cc = static_cast<unsigned char>(s[i + k]);
        if ((cc & 0xC0) != 0x80) return 0;
        cp = (cp << 6) | (cc & 0x3F);
    }
    static const uint32_t min_for[5] = {0, 0, 0x80, 0x800, 0x10000};
    if (n > 1 && cp < min_for[n]) return 0;                   // overlong
    if (cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) return 0;
    return n;
}

std::string Lower(std::string s) {
    for (auto& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return s;
}

bool ParseNumber(const Params& p, const char* key, double& out) {
    auto it = p.find(key);
    if (it == p.end() || it->second.empty()) return false;
    char* end = nullptr;
    out = std::strtod(it->second.c_str(), &end);
    return end && *end == '\0' && std::isfinite(out);
}

// absent -> fallback; present but not an integer -> false
bool ParseInt(const Params& p, const char* key, int& out, int fallback) {
    auto it = p.find(key);
    if (it == p.end() || it->second.empty()) { out = fallback; return true; }
    char* end = nullptr;
    long v = std::strtol(it->second.c_str(), &end, 10);
    if (!end || *end != '\0') return false;
    out = static_cast<int>(std::max(-1000000L, std::min(1000000L, v)));
    return true;
}

bool ParseBool(const Params& p, const char* key, bool& out, bool fallback) {
    auto it = p.find(key);
    if (it == p.end() || it->second.empty()) { out = fallback; return true; }
    const std::string v = Lower(it->second);
    if (v == "true" || v == "1" || v == "yes") { out = true; return true; }
    if (v == "false" || v == "0" || v == "no") { out = false; return true; }
    return false;
}

std::string Get(const Params& p, const char* key) {
    auto it = p.find(key);
    return it == p.end() ? "" : it->second;
}

}  // namespace

std::string SanitizeUtf8(const std::string& s) {
    std::string out;
    out.reserve(s.size());
    for (size_t i = 0; i < s.size();) {
        size_t n = Utf8SeqLen(s, i);
        if (n == 0) { out += "\xEF\xBF\xBD"; ++i; }
        else { out.append(s, i, n); i += n; }
    }
    return out;
}

std::string TruncateUtf8(const std::string& s, size_t max_chars) {
    const std::string clean = SanitizeUtf8(s);
    size_t chars = 0, i = 0;
    while (i < clean.size()) {
        if (chars == max_chars) return clean.substr(0, i) + "\xE2\x80\xA6";            // "…"
        i += Utf8SeqLen(clean, i);
        ++chars;
    }
    return clean;
}

std::string JsonEscape(const std::string& s) {
    static const char* hex = "0123456789abcdef";
    std::string out;
    for (unsigned char c : SanitizeUtf8(s)) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) { out += "\\u00"; out += hex[c >> 4]; out += hex[c & 15]; }
                else out += static_cast<char>(c);
        }
    }
    return out;
}

std::string JsonString(const std::string& s) { return "\"" + JsonEscape(s) + "\""; }

std::string Base64(const std::string& in) {
    static const char* t = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    out.reserve((in.size() + 2) / 3 * 4);
    size_t i = 0;
    for (; i + 2 < in.size(); i += 3) {
        uint32_t v = (static_cast<unsigned char>(in[i]) << 16) | (static_cast<unsigned char>(in[i + 1]) << 8) |
                     static_cast<unsigned char>(in[i + 2]);
        out += t[v >> 18]; out += t[(v >> 12) & 63]; out += t[(v >> 6) & 63]; out += t[v & 63];
    }
    if (i + 1 == in.size()) {
        uint32_t v = static_cast<unsigned char>(in[i]) << 16;
        out += t[v >> 18]; out += t[(v >> 12) & 63]; out += "==";
    } else if (i + 2 == in.size()) {
        uint32_t v = (static_cast<unsigned char>(in[i]) << 16) | (static_cast<unsigned char>(in[i + 1]) << 8);
        out += t[v >> 18]; out += t[(v >> 12) & 63]; out += t[(v >> 6) & 63]; out += '=';
    }
    return out;
}

// ======================================================================================== roles

std::string NormalizeRole(const std::string& atspi_role, bool editable) {
    std::string r = Lower(atspi_role);
    for (auto& c : r) if (c == '_' || c == '-') c = ' ';
    static const std::map<std::string, std::string> m = {
        {"push button", "button"}, {"push button menu", "button"}, {"button", "button"},
        {"link", "link"},
        {"label", "text"}, {"static", "text"}, {"heading", "text"}, {"paragraph", "text"}, {"caption", "text"},
        {"footer", "text"}, {"block quote", "text"}, {"description value", "text"}, {"description term", "text"},
        {"entry", "input"}, {"password text", "input"}, {"spin button", "input"}, {"terminal", "input"},
        {"check box", "checkbox"},
        {"toggle button", "switch"}, {"switch", "switch"},
        {"radio button", "radio"},
        {"menu", "menu"}, {"menu bar", "menu"}, {"popup menu", "menu"},
        {"menu item", "menu_item"}, {"check menu item", "menu_item"}, {"radio menu item", "menu_item"},
        {"tearoff menu item", "menu_item"},
        {"page tab", "tab"},
        {"list", "list"}, {"list box", "list"}, {"tree", "list"}, {"tree table", "list"}, {"table", "list"},
        {"list item", "list_item"}, {"tree item", "list_item"}, {"table cell", "list_item"}, {"table row", "list_item"},
        {"image", "image"}, {"icon", "image"}, {"animation", "image"}, {"desktop icon", "image"}, {"image map", "image"},
        {"frame", "window"}, {"window", "window"}, {"internal frame", "window"},
        {"dialog", "dialog"}, {"alert", "dialog"}, {"file chooser", "dialog"}, {"color chooser", "dialog"},
        {"font chooser", "dialog"}, {"option pane", "dialog"}, {"notification", "dialog"},
        {"scroll pane", "scroll"}, {"scroll bar", "scroll"},
    };
    if (r == "text") return editable ? "input" : "text";
    if (r == "combo box") return editable ? "input" : "button";           // a dropdown opens a popup when clicked
    auto it = m.find(r);
    return it == m.end() ? "other" : it->second;
}

// ================================================================================== the UI tree

namespace {

bool Transparent(const NodeInfo& i, const std::string& role) {
    return role == "other" && i.name.empty() && i.text.empty() && !i.clickable && !i.editable && !i.scrollable &&
           !i.focused && i.checked < 0;
}

struct Slot {
    Node node;
    Handle handle = 0;
    std::vector<size_t> kids;
};

Node Assemble(std::vector<Slot>& slots, size_t i) {
    Node n = std::move(slots[i].node);
    n.children.reserve(slots[i].kids.size());
    for (size_t k : slots[i].kids) n.children.push_back(Assemble(slots, k));
    return n;
}

}  // namespace

Tree BuildTree(NodeSource& source, Handle root, const Limits& limits) {
    Tree tree;
    std::vector<Slot> slots;
    std::vector<size_t> top;
    struct Item { Handle h; long parent; int depth; };
    std::deque<Item> queue;
    queue.push_back({root, -1, 0});
    const size_t raw_cap = static_cast<size_t>(std::max(1, limits.max_nodes)) * 20 + 200;   // a tree of nothing but
    size_t raw = 0;                                                                         // empty containers is bounded too

    while (!queue.empty()) {
        Item it = queue.front();
        queue.pop_front();
        if (++raw > raw_cap) { tree.truncated = true; break; }
        NodeInfo info;
        if (!source.Info(it.h, info) || !info.showing) continue;
        const std::string role = NormalizeRole(info.role, info.editable);
        if (it.parent >= 0 && Transparent(info, role)) {                     // splice: its children take its place
            for (Handle c : source.Children(it.h)) queue.push_back({c, it.parent, it.depth});
            continue;
        }
        if (static_cast<int>(slots.size()) >= limits.max_nodes) { tree.truncated = true; break; }

        Slot s;
        s.handle = it.h;
        s.node.id = "e" + std::to_string(slots.size() + 1);
        s.node.role = role;
        s.node.name = TruncateUtf8(info.name, 200);
        s.node.text = TruncateUtf8(info.text, 300);
        s.node.bounds = info.bounds;
        s.node.enabled = info.enabled;
        s.node.focused = info.focused;
        s.node.clickable = info.clickable;
        s.node.scrollable = info.scrollable;
        s.node.editable = info.editable;
        s.node.checked = info.checked;
        const size_t index = slots.size();
        tree.handles[s.node.id] = it.h;
        slots.push_back(std::move(s));
        if (it.parent < 0) top.push_back(index);
        else slots[static_cast<size_t>(it.parent)].kids.push_back(index);

        if (it.depth + 1 >= limits.max_depth) {                               // at the depth limit: note what is cut off
            if (!source.Children(it.h).empty()) tree.truncated = true;
        } else {
            for (Handle c : source.Children(it.h)) queue.push_back({c, static_cast<long>(index), it.depth + 1});
        }
    }
    tree.count = static_cast<int>(slots.size());
    for (size_t t : top) tree.roots.push_back(Assemble(slots, t));
    return tree;
}

std::string BoundsJson(const Bounds& b) {
    return "[" + std::to_string(b.x) + "," + std::to_string(b.y) + "," + std::to_string(b.w) + "," + std::to_string(b.h) + "]";
}

std::string NodeJson(const Node& n) {
    std::string s = "{\"id\":" + JsonString(n.id) + ",\"role\":" + JsonString(n.role) + ",\"name\":" + JsonString(n.name) +
                    ",\"text\":" + JsonString(n.text) + ",\"bounds\":" + BoundsJson(n.bounds) +
                    ",\"enabled\":" + (n.enabled ? "true" : "false") + ",\"focused\":" + (n.focused ? "true" : "false") +
                    ",\"checked\":" + (n.checked < 0 ? "null" : n.checked ? "true" : "false") +
                    ",\"clickable\":" + (n.clickable ? "true" : "false") + ",\"scrollable\":" + (n.scrollable ? "true" : "false") +
                    ",\"editable\":" + (n.editable ? "true" : "false") + ",\"children\":[";
    for (size_t i = 0; i < n.children.size(); ++i) s += (i ? "," : "") + NodeJson(n.children[i]);
    return s + "]}";
}

std::string TreeJson(const Tree& t) {
    std::string s = "[";
    for (size_t i = 0; i < t.roots.size(); ++i) s += (i ? "," : "") + NodeJson(t.roots[i]);
    return s + "]";
}

// ============================================================================ observation store

std::string ObservationStore::Begin(const std::map<std::string, Handle>& handles) {
    Clear();
    handles_ = handles;
    current_ = "obs-" + std::to_string(++counter_);
    return current_;
}

void ObservationStore::Clear() {
    if (release_) for (const auto& [id, h] : handles_) release_(h);
    handles_.clear();
    current_.clear();
}

bool ObservationStore::Resolve(const std::string& element_id, const std::string& observation_id, Handle& out,
                               std::string& error) const {
    if (current_.empty()) { error = "no_observation"; return false; }
    if (!observation_id.empty() && observation_id != current_) { error = "stale_observation"; return false; }
    auto it = handles_.find(element_id);
    if (it == handles_.end()) { error = "unknown_element"; return false; }
    out = it->second;
    return true;
}

// ================================================================================== parameters

bool ParseFlatJsonObject(const std::string& s, std::map<std::string, std::string>& out) {
    out.clear();
    size_t i = 0;
    auto ws = [&] { while (i < s.size() && std::isspace(static_cast<unsigned char>(s[i]))) ++i; };
    auto str = [&](std::string& v) {
        if (i >= s.size() || s[i] != '"') return false;
        ++i;
        v.clear();
        while (i < s.size() && s[i] != '"') {
            if (s[i] == '\\' && i + 1 < s.size()) { v += s[i + 1]; i += 2; }
            else v += s[i++];
        }
        if (i >= s.size()) return false;
        ++i;
        return true;
    };
    ws();
    if (i >= s.size() || s[i] != '{') return false;
    ++i;
    ws();
    if (i < s.size() && s[i] == '}') return true;
    for (;;) {
        ws();
        std::string k, v;
        if (!str(k)) return false;
        ws();
        if (i >= s.size() || s[i] != ':') return false;
        ++i;
        ws();
        if (i < s.size() && s[i] == '"') { if (!str(v)) return false; }
        else {
            size_t start = i;
            while (i < s.size() && s[i] != ',' && s[i] != '}' && !std::isspace(static_cast<unsigned char>(s[i]))) {
                if (s[i] == '{' || s[i] == '[') return false;                    // flat objects only
                ++i;
            }
            v = s.substr(start, i - start);
            if (v.empty()) return false;
        }
        out[k] = v;
        ws();
        if (i < s.size() && s[i] == ',') { ++i; continue; }
        if (i < s.size() && s[i] == '}') return true;
        return false;
    }
}

namespace {

// A point: from "x"/"y" keys, or a nested object passed as JSON in `name`, or name_x / name_y, or name.x / name.y.
bool GetPoint(const Params& p, const std::string& name, Point& out, bool& present) {
    present = false;
    double x = 0, y = 0;
    auto it = p.find(name);
    if (it != p.end() && !it->second.empty()) {
        std::map<std::string, std::string> o;
        if (!ParseFlatJsonObject(it->second, o) || !o.count("x") || !o.count("y")) { present = true; return false; }
        Params q = {{"x", o["x"]}, {"y", o["y"]}};
        present = true;
        if (!ParseNumber(q, "x", x) || !ParseNumber(q, "y", y)) return false;
        out = {x, y};
        return true;
    }
    for (const char* sep : {"_", "."}) {
        const std::string kx = name + sep + "x", ky = name + sep + "y";
        if (p.count(kx) || p.count(ky)) {
            present = true;
            if (!ParseNumber(p, kx.c_str(), x) || !ParseNumber(p, ky.c_str(), y)) return false;
            out = {x, y};
            return true;
        }
    }
    return true;
}

constexpr double kMaxCoord = 100000;

// element_id (wins) or x+y. `required`: one of them must be present.
Fail ParseTarget(const Params& p, bool required, Target& t) {
    t = {};
    t.element_id = Get(p, "element_id");
    t.observation_id = Get(p, "observation_id");
    if (!t.element_id.empty()) return {};
    const bool has_x = p.count("x") && !p.at("x").empty(), has_y = p.count("y") && !p.at("y").empty();
    if (has_x || has_y) {
        double x = 0, y = 0;
        if (!ParseNumber(p, "x", x) || !ParseNumber(p, "y", y))
            return {"missing_coordinates", "pass both numeric x and y (pixels of the latest observation)"};
        if (std::fabs(x) > kMaxCoord || std::fabs(y) > kMaxCoord) return {"invalid_coordinates", "x and y are out of range"};
        t.has_point = true;
        t.point = {x, y};
        return {};
    }
    if (required) return {"missing_target", "pass element_id (from computer.observe) or x and y"};
    return {};
}

}  // namespace

Fail ParseObserve(const Params& p, ObserveArgs& out) {
    out = {};
    if (!ParseBool(p, "include_tree", out.include_tree, true)) return {"invalid_include_tree", "include_tree must be true or false"};
    if (!ParseBool(p, "include_screenshot", out.include_screenshot, false))
        return {"invalid_include_screenshot", "include_screenshot must be true or false"};
    if (!ParseInt(p, "max_nodes", out.limits.max_nodes, 150) || out.limits.max_nodes < 1 || out.limits.max_nodes > 500)
        return {"invalid_max_nodes", "max_nodes must be 1-500"};
    if (!ParseInt(p, "max_depth", out.limits.max_depth, 12) || out.limits.max_depth < 1 || out.limits.max_depth > 40)
        return {"invalid_max_depth", "max_depth must be 1-40"};
    if (!out.include_tree && !out.include_screenshot)
        return {"nothing_to_observe", "set include_tree or include_screenshot"};
    return {};
}

Fail ParseClick(const Params& p, ClickArgs& out) {
    out = {};
    if (Fail f = ParseTarget(p, true, out.target); !f.ok()) return f;
    out.button = Lower(Get(p, "button").empty() ? "left" : Get(p, "button"));
    if (out.button != "left" && out.button != "right" && out.button != "middle")
        return {"invalid_button", "button must be left, right or middle"};
    if (!ParseInt(p, "count", out.count, 1) || out.count < 1 || out.count > 2) return {"invalid_count", "count must be 1 or 2"};
    return {};
}

Fail ParseLongPress(const Params& p, LongPressArgs& out) {
    out = {};
    if (Fail f = ParseTarget(p, true, out.target); !f.ok()) return f;
    if (!ParseInt(p, "duration_ms", out.duration_ms, 600) || out.duration_ms < 100 || out.duration_ms > 5000)
        return {"invalid_duration", "duration_ms must be 100-5000"};
    return {};
}

Fail ParseType(const Params& p, TypeArgs& out) {
    out = {};
    out.text = Get(p, "text");
    if (out.text.empty()) return {"missing_required_param", "text"};
    std::vector<uint32_t> cps;
    if (!pai::keys::DecodeUtf8(out.text, cps)) return {"invalid_text", "text must be valid UTF-8"};
    if (cps.size() > kMaxTextChars) return {"text_too_long", "at most " + std::to_string(kMaxTextChars) + " characters per call"};
    for (uint32_t cp : cps) {
        int32_t k;
        if (!pai::keys::KeysymForChar(cp, k)) return {"invalid_text", "control characters other than newline and tab cannot be typed"};
    }
    out.element_id = Get(p, "element_id");
    out.observation_id = Get(p, "observation_id");
    if (!ParseBool(p, "clear_first", out.clear_first, false)) return {"invalid_clear_first", "clear_first must be true or false"};
    return {};
}

Fail ParseKey(const Params& p, std::vector<int32_t>& modifiers, int32_t& key, std::string& spec) {
    spec = Get(p, "keys");
    if (spec.empty()) return {"missing_required_param", "keys"};
    pai::keys::KeySpec ks;
    std::string error, culprit;
    if (!pai::keys::ParseKeySpec(spec, ks, error, culprit)) {
        if (error == "invalid_keys") return {error, "use names like ctrl+shift+t; write the plus key as 'plus'"};
        if (error == "unknown_modifier") return {error, "'" + culprit + "' is not a modifier: ctrl, shift, alt, super"};
        return {error, "'" + culprit + "' is not a key name: e.g. Return, Escape, Tab, Left, F5, a, 5"};
    }
    modifiers = ks.modifiers;
    key = ks.key;
    return {};
}

Fail ParseScroll(const Params& p, ScrollArgs& out) {
    out = {};
    out.direction = Lower(Get(p, "direction"));
    if (out.direction.empty()) return {"missing_required_param", "direction"};
    if (out.direction != "up" && out.direction != "down" && out.direction != "left" && out.direction != "right")
        return {"invalid_direction", "direction must be up, down, left or right"};
    if (!ParseInt(p, "amount", out.amount, 3) || out.amount < 1 || out.amount > kMaxScrollAmount)
        return {"invalid_amount", "amount must be 1-" + std::to_string(kMaxScrollAmount)};
    return ParseTarget(p, false, out.target);
}

Fail ParseSwipe(const Params& p, SwipeArgs& out) {
    out = {};
    bool has_from = false, has_to = false;
    if (!GetPoint(p, "from", out.from, has_from)) return {"invalid_from", "from must be {\"x\": number, \"y\": number}"};
    if (!GetPoint(p, "to", out.to, has_to)) return {"invalid_to", "to must be {\"x\": number, \"y\": number}"};
    if (!has_from || !has_to) return {"missing_required_param", "from and to"};
    for (double v : {out.from.x, out.from.y, out.to.x, out.to.y})
        if (std::fabs(v) > kMaxCoord) return {"invalid_coordinates", "coordinates are out of range"};
    if (!ParseInt(p, "duration_ms", out.duration_ms, 300) || out.duration_ms < 50 || out.duration_ms > 5000)
        return {"invalid_duration", "duration_ms must be 50-5000"};
    return {};
}

// ===================================================================================== results

ResultMap FailResult(std::string& error, const Fail& f, ResultMap extra) {
    error = f.code;
    extra["ok"] = "false";
    extra["error"] = f.code;
    if (!f.hint.empty()) extra["hint"] = f.hint;
    return extra;
}

std::string JsonStringArray(const std::vector<std::string>& v) {
    std::string s = "[";
    for (size_t i = 0; i < v.size(); ++i) s += (i ? "," : "") + JsonString(v[i]);
    return s + "]";
}

}  // namespace pai::cc

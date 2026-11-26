#pragma once
// Key names, UTF-8 and X11 keysyms for synthetic keyboard input.
//
// Extracted from the input plugin (input 1.0.0 keeps its own copy: a released version is immutable) so
// that other plugins (computer_control) parse "ctrl+shift+t", "Return" or "é" exactly the same way.
// Header-only, no dependencies: it is unit-tested directly.
#include <cctype>
#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace pai::keys {

constexpr int32_t kKeysymReturn = 0xFF0D, kKeysymTab = 0xFF09;

inline std::string Lower(std::string s) {
    for (auto& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return s;
}

// Strict UTF-8 -> code points. false on malformed input (overlongs, surrogates, > U+10FFFF).
inline bool DecodeUtf8(const std::string& s, std::vector<uint32_t>& out) {
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
inline bool KeysymForChar(uint32_t cp, int32_t& keysym) {
    if (cp == '\n') { keysym = kKeysymReturn; return true; }
    if (cp == '\t') { keysym = kKeysymTab; return true; }
    if (cp < 0x20 || cp == 0x7F || (cp >= 0x80 && cp < 0xA0)) return false;      // control characters
    keysym = (cp <= 0xFF) ? static_cast<int32_t>(cp) : static_cast<int32_t>(0x01000000 | cp);
    return true;
}

inline const std::map<std::string, int32_t>& KeyNames() {
    static const std::map<std::string, int32_t> names = [] {
        std::map<std::string, int32_t> m = {
            {"return", 0xFF0D}, {"enter", 0xFF0D}, {"kp_enter", 0xFF8D}, {"escape", 0xFF1B}, {"esc", 0xFF1B},
            {"tab", 0xFF09}, {"backspace", 0xFF08}, {"delete", 0xFFFF}, {"del", 0xFFFF}, {"insert", 0xFF63},
            {"home", 0xFF50}, {"end", 0xFF57}, {"pageup", 0xFF55}, {"page_up", 0xFF55}, {"prior", 0xFF55},
            {"pagedown", 0xFF56}, {"page_down", 0xFF56}, {"next", 0xFF56},
            {"left", 0xFF51}, {"up", 0xFF52}, {"right", 0xFF53}, {"down", 0xFF54},
            {"space", 0x20}, {"plus", 0x2B}, {"minus", 0x2D}, {"comma", 0x2C}, {"period", 0x2E},
            {"slash", 0x2F}, {"backslash", 0x5C}, {"menu", 0xFF67}, {"print", 0xFF61},
        };
        for (int i = 1; i <= 12; ++i) m["f" + std::to_string(i)] = 0xFFBE + i - 1;
        return m;
    }();
    return names;
}

inline const std::map<std::string, int32_t>& ModifierNames() {
    static const std::map<std::string, int32_t> m = {
        {"ctrl", 0xFFE3}, {"control", 0xFFE3}, {"control_l", 0xFFE3}, {"ctrl_l", 0xFFE3},
        {"shift", 0xFFE1}, {"shift_l", 0xFFE1},
        {"alt", 0xFFE9}, {"alt_l", 0xFFE9},
        {"super", 0xFFEB}, {"super_l", 0xFFEB}, {"meta", 0xFFEB}, {"win", 0xFFEB}, {"cmd", 0xFFEB},
    };
    return m;
}

// One key token ("Return", "F5", "a", "é", "5") -> keysym.
inline bool ResolveKey(const std::string& token, int32_t& keysym) {
    const std::string t = Lower(token);
    auto it = KeyNames().find(t);
    if (it != KeyNames().end()) { keysym = it->second; return true; }
    std::vector<uint32_t> cps;
    if (DecodeUtf8(token, cps) && cps.size() == 1) return KeysymForChar(cps[0], keysym);
    return false;
}

struct KeySpec {
    std::vector<int32_t> modifiers;   // pressed in order, released in reverse
    int32_t key = 0;
};

// "ctrl+shift+t" -> modifiers + one key. A bare modifier ("shift", "super") is a valid key press.
// On failure returns false with `error` = invalid_keys | unknown_modifier | unknown_key and `culprit`.
inline bool ParseKeySpec(const std::string& spec, KeySpec& out, std::string& error, std::string& culprit) {
    out = {};
    std::vector<std::string> tokens;
    size_t pos = 0;
    while (true) {
        size_t plus = spec.find('+', pos);
        tokens.push_back(spec.substr(pos, plus == std::string::npos ? std::string::npos : plus - pos));
        if (plus == std::string::npos) break;
        pos = plus + 1;
    }
    for (size_t i = 0; i < tokens.size(); ++i) {
        const std::string& t = tokens[i];
        if (t.empty()) { error = "invalid_keys"; culprit = spec; return false; }     // the plus KEY is spelled "plus"
        if (i + 1 < tokens.size()) {
            auto m = ModifierNames().find(Lower(t));
            if (m == ModifierNames().end()) { error = "unknown_modifier"; culprit = t; return false; }
            out.modifiers.push_back(m->second);
        } else if (!ResolveKey(t, out.key)) {
            auto m = ModifierNames().find(Lower(t));
            if (m == ModifierNames().end()) { error = "unknown_key"; culprit = t; return false; }
            out.key = m->second;
        }
    }
    // "Ctrl+Shift+T": with modifiers held the key is the LETTER's key; the shift modifier makes it a capital.
    // Sending keysym 'T' on top of a held Shift would depend on how the keymap resolves it.
    if (!out.modifiers.empty() && out.key >= 'A' && out.key <= 'Z') out.key += 'a' - 'A';
    return true;
}

}  // namespace pai::keys

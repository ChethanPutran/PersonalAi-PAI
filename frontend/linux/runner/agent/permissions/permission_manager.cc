#include "permission_manager.h"

#include <unistd.h>
#include <sys/stat.h>

#include <cctype>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <set>
#include <sstream>

namespace pai::agent {

namespace {

bool FileExists(const std::string& p) {
  struct stat st{};
  return ::stat(p.c_str(), &st) == 0;
}

bool Accessible(const std::string& p, int mode) {
  return ::access(p.c_str(), mode) == 0;
}

bool AnyVideoDevice() {
  for (int i = 0; i < 20; ++i) {
    const std::string n = "/dev/video" + std::to_string(i);
    if (Accessible(n, R_OK | W_OK)) return true;
  }
  return false;
}

bool AnyAudioDevice() {
  const char* runtime = std::getenv("XDG_RUNTIME_DIR");
  if (runtime && FileExists(std::string(runtime) + "/pulse/native")) return true;
  if (FileExists("/dev/snd/controlC0")) return true;
  if (FileExists("/proc/asound/card0")) return true;
  return false;
}

bool FilesystemAccessible() {
  const char* home = std::getenv("HOME");
  return home && Accessible(home, R_OK | W_OK);
}


// Permissions that require explicit user consent (dialog on first use, once PER PLUGIN).
const std::set<std::string> kConsentRequired = {
    "screen", "camera", "microphone", "clipboard", "notifications",
    // Every plugin asks for what it declares, including these: nothing is granted silently.
    "filesystem", "network", "accessibility",
    // Typing and clicking on the user's behalf: can do anything the user can do on screen.
    "input",
    // Privileged operations — must always prompt. Never auto-grant,
    // never silently deny.
    "shell", "exec", "subprocess", "command", "process.spawn",
};

// A plugin whose consent gate is its OWN env var (not the shared one above): computer_control drives the
// whole desktop, so what the user allowed for some other plugin must never count for it.
struct PluginBridge { const char* plugin; const char* perm; const char* env; };
const PluginBridge kPluginBridges[] = {
    {"pai.computer_control", "input", "PAI_CC_INPUT_CONSENT"},
    {"pai.computer_control", "screen", "PAI_CC_SCREEN_CONSENT"},
};

// True for a stored "<plugin>/<permission>" key that belongs to a plugin with its own gate.
bool HasOwnGate(const std::string& key) {
  for (const auto& b : kPluginBridges) {
    const std::string prefix = std::string(b.plugin) + "/";
    if (key.compare(0, prefix.size(), prefix) == 0) return true;
  }
  return false;
}

// Nothing is auto-granted any more: a plugin gets only what the user allowed for THAT plugin.
const std::set<std::string> kAutoGranted = {};

// "available" | "unavailable" — never "needs prompt"; that's decided later.
std::string Probe(const std::string& logical) {
  if (logical == "camera")     return AnyVideoDevice()       ? "available" : "unavailable";
  if (logical == "microphone") return AnyAudioDevice()       ? "available" : "unavailable";
  if (logical == "filesystem") return FilesystemAccessible() ? "available" : "unavailable";
  if (logical == "location" || logical == "contacts") return "unavailable";
  if (logical == "screen" || logical == "input") {
    return (std::getenv("DISPLAY") || std::getenv("WAYLAND_DISPLAY"))
        ? "available" : "unavailable";
  }
  if (kAutoGranted.count(logical))   return "available";
  if (kConsentRequired.count(logical)) return "available";
  return "unavailable";
}

std::string Join(const std::vector<std::string>& v) {
  std::ostringstream oss;
  for (size_t i = 0; i < v.size(); ++i) { if (i) oss << ','; oss << v[i]; }
  return oss.str();
}

std::string ConfigPath() {
  const char* xdg = std::getenv("XDG_CONFIG_HOME");
  const char* home = std::getenv("HOME");
  std::string base = (xdg && *xdg)
      ? xdg
      : (home ? std::string(home) + "/.config" : "/tmp");
  std::string dir = base + "/pai";
  ::mkdir(base.c_str(), 0700);
  ::mkdir(dir.c_str(), 0700);
  return dir + "/permissions.json";
}

// Minimal flat-object JSON: {"screen":"allow","camera":"deny"}. No dep.
std::map<std::string, std::string> ParseDecisions(const std::string& j) {
  std::map<std::string, std::string> out;
  size_t i = 0;
  auto ws = [&](){ while (i < j.size() && std::isspace((unsigned char)j[i])) ++i; };
  auto str = [&](std::string& s) -> bool {
    if (i >= j.size() || j[i] != '"') return false;
    ++i; s.clear();
    while (i < j.size() && j[i] != '"') {
      if (j[i] == '\\' && i+1 < j.size()) { s += j[i+1]; i += 2; }
      else { s += j[i]; ++i; }
    }
    if (i >= j.size()) return false;
    ++i; return true;
  };
  ws();
  if (i >= j.size() || j[i] != '{') return out;
  ++i;
  for (;;) {
    ws();
    if (i < j.size() && j[i] == '}') break;
    std::string k, v;
    if (!str(k)) break;
    ws();
    if (i >= j.size() || j[i] != ':') break;
    ++i; ws();
    if (!str(v)) break;
    out[k] = v;
    ws();
    if (i < j.size() && j[i] == ',') ++i;
  }
  return out;
}

std::string Serialize(const std::map<std::string, std::string>& m) {
  std::string out = "{\n";
  bool first = true;
  for (const auto& [k, v] : m) {
    if (!first) out += ",\n";
    first = false;
    out += "  \"" + k + "\": \"" + v + "\"";
  }
  out += "\n}\n";
  return out;
}

}  // namespace

PermissionManager::PermissionManager() { Load(); }

std::string PermissionManager::Key(const std::string& plugin, const std::string& permission) {
  return plugin + "/" + permission;
}

void PermissionManager::Load() {
  decisions_.clear();
  std::ifstream f(ConfigPath());
  if (!f) return;
  std::stringstream ss; ss << f.rdbuf();
  size_t legacy = 0;
  for (const auto& [k, v] : ParseDecisions(ss.str())) {
    // Old releases stored one decision per permission for ALL plugins ("camera":"allow"). Those cannot
    // be attributed to any plugin, so they are dropped: each plugin is asked again, on its own.
    if (k.find('/') == std::string::npos) { ++legacy; continue; }
    decisions_[k] = v;
  }
  if (legacy > 0) {
    fprintf(stderr, "[perm] ignoring %zu legacy shared decision(s): every plugin will ask for its own\n", legacy);
  }
}

void PermissionManager::Save() const {
  std::ofstream f(ConfigPath(), std::ios::trunc);
  if (f) f << Serialize(decisions_);
}

void PermissionManager::SetDecisions(
    const std::string& plugin, const std::vector<std::string>& permissions, bool allow) {
  if (plugin.empty()) return;          // never store a decision that belongs to no plugin
  for (const auto& p : permissions) {
    decisions_[Key(plugin, p)] = allow ? "allow" : "deny";
  }
  Save();
}

void PermissionManager::Forget(const std::string& plugin) {
  if (plugin.empty()) return;
  const std::string prefix = plugin + "/";
  for (auto it = decisions_.begin(); it != decisions_.end();) {
    it = (it->first.rfind(prefix, 0) == 0) ? decisions_.erase(it) : std::next(it);
  }
  Save();
  ApplyBridges(/*unset_when_none_allowed=*/true);     // a plugin that is gone must not keep a gate open
}

std::string PermissionManager::Classify(const std::string& plugin, const std::string& p) const {
  auto it = decisions_.find(Key(plugin, p));
  if (plugin.empty()) it = decisions_.end();
  const std::string decision = (it == decisions_.end()) ? "prompt" : it->second;
  // An explicit deny is always respected — never re-prompt.
  if (decision == "deny") return "denied";
  // An explicit allow is always honored. We deliberately skip the OS probe here so hot-plugged
  // hardware keeps working without a re-prompt.
  if (decision == "allow") return "granted";
  // Consent-required permissions ALWAYS go to the user the first time, regardless of what the OS
  // probe says (the probe can false-negative: no DISPLAY in a container, camera hot-plugged later).
  if (kConsentRequired.count(p) > 0) return "pending";
  // Non-consent permissions: auto-grant once the probe passes.
  return Probe(p) == "available" ? "granted" : "denied";
}

std::map<std::string, std::string> PermissionManager::PermissionStatus(
    const std::string& plugin, const std::vector<std::string>& permissions) const {
  std::map<std::string, std::string> out;
  for (const auto& p : permissions) out[p] = Classify(plugin, p);
  return out;
}

std::map<std::string, std::string> PermissionManager::Request(
    const std::string& plugin, const std::vector<std::string>& permissions) {
  std::vector<std::string> granted, denied, pending;

  for (const auto& p : permissions) {
    const std::string state = Classify(plugin, p);
    auto it = decisions_.find(Key(plugin, p));
    fprintf(stderr, "[perm] %-18s %-12s probe=%-11s decision=%s\n", plugin.c_str(), p.c_str(), Probe(p).c_str(),
            it == decisions_.end() ? "prompt" : it->second.c_str());
    if (state == "granted") granted.push_back(p);
    else if (state == "denied") denied.push_back(p);
    else pending.push_back(p);
  }

  std::map<std::string, std::string> out = {
      {"ok",      (denied.empty() && pending.empty()) ? "true" : "false"},
      {"granted", Join(granted)},
      {"denied",  Join(denied)},
  };
  if (!pending.empty()) out["pending"] = Join(pending);
  return out;
}

void PermissionManager::ApplyEnvBridges() { ApplyBridges(/*unset_when_none_allowed=*/false); }

void PermissionManager::ApplyBridges(bool unset_when_none_allowed) {
  // Plugins check their own env vars (PAI_SCREEN_CONSENT=1, PAI_INPUT_CONSENT=1). Reflect the user's
  // decision into those env vars: "allow" sets it so the plugin's gate passes without the user
  // setting anything by hand; "deny" clears it.
  struct Bridge { const char* perm; const char* env; };
  static const Bridge kBridges[] = {
      {"screen", "PAI_SCREEN_CONSENT"},
      {"input", "PAI_INPUT_CONSENT"},
      // Add when other plugins adopt their own consent gates:
      // {"camera", "PAI_CAMERA_CONSENT"},
  };
  for (const auto& b : kBridges) {
    // Decisions are per plugin but the variable is process-wide: open while any plugin allows it.
    // Plugins with their OWN gate (below) are left out: allowing "input" for the input plugin must not open
    // computer_control's gate, nor the other way round.
    bool any_allow = false, any_deny = false;
    const std::string suffix = std::string("/") + b.perm;
    for (const auto& [k, v] : decisions_) {
      if (HasOwnGate(k)) continue;
      if (k.size() > suffix.size() && k.compare(k.size() - suffix.size(), suffix.size(), suffix) == 0) {
        if (v == "allow") any_allow = true;
        else if (v == "deny") any_deny = true;
      }
    }
    if (any_allow) {
      ::setenv(b.env, "1", 1);
    } else if (any_deny || unset_when_none_allowed) {
      // A revoke must take effect NOW. Only ever setting the variable left it "1" until the app was
      // restarted, so a plugin kept passing its own consent gate after the user said no.
      ::unsetenv(b.env);
    }
  }

  // Plugin-specific gates: the variable follows THIS plugin's decision only. Allowed -> open; denied (or
  // forgotten on uninstall) -> closed right now.
  for (const auto& b : kPluginBridges) {
    auto it = decisions_.find(Key(b.plugin, b.perm));
    if (it != decisions_.end() && it->second == "allow") ::setenv(b.env, "1", 1);
    else if (it != decisions_.end() || unset_when_none_allowed) ::unsetenv(b.env);
  }
}

}  // namespace pai::agent
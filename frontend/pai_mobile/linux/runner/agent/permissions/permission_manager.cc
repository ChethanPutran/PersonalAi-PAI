// linux/runner/agent/permissions/permission_manager.cc
#include "permission_manager.h"

#include <unistd.h>
#include <sys/stat.h>

#include <cstdlib>
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
  // PipeWire / PulseAudio socket
  const char* runtime = std::getenv("XDG_RUNTIME_DIR");
  if (runtime) {
    const std::string pulse = std::string(runtime) + "/pulse/native";
    if (FileExists(pulse)) return true;
  }
  // ALSA fallback
  if (FileExists("/dev/snd/controlC0")) return true;
  if (FileExists("/proc/asound/card0")) return true;
  return false;
}

bool FilesystemAccessible() {
  const char* home = std::getenv("HOME");
  return home && Accessible(home, R_OK | W_OK);
}

/// Real check per logical permission. "true" means the capability is
/// usable *right now* on this machine.
bool Check(const std::string& logical) {
  if (logical == "camera")         return AnyVideoDevice();
  if (logical == "microphone")     return AnyAudioDevice();
  if (logical == "filesystem")     return FilesystemAccessible();

  // No OS-level gate on Linux — the user already proved access by logging in.
  if (logical == "network")        return true;
  if (logical == "notifications")  return true;   // dbus, always up in a desktop session
  if (logical == "clipboard")      return true;
  if (logical == "process")        return true;
  if (logical == "accessibility")  return true;   // AT-SPI on any GNOME/KDE

  // No sensor / contacts store on a typical desktop.
  if (logical == "location")       return false;
  if (logical == "contacts")       return false;

  return false;   // unknown permission → deny
}

std::string Join(const std::vector<std::string>& v) {
  std::ostringstream oss;
  for (size_t i = 0; i < v.size(); ++i) {
    if (i) oss << ',';
    oss << v[i];
  }
  return oss.str();
}

}  // namespace

std::map<std::string, std::string> PermissionManager::Request(
    const std::vector<std::string>& permissions) {

  std::vector<std::string> granted;
  std::vector<std::string> denied;
  for (const auto& p : permissions) {
    (Check(p) ? granted : denied).push_back(p);
  }

  return {
      {"ok",      denied.empty() ? "true" : "false"},
      {"granted", Join(granted)},
      {"denied",  Join(denied)},
  };
}

}  // namespace pai::agent
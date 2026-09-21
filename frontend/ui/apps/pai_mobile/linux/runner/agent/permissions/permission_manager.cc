// linux/runner/agent/permissions/permission_manager.cc
#include "permission_manager.h"

#include <set>

namespace pai::agent {

namespace {
const std::set<std::string>& Known() {
  static const std::set<std::string> k = {
      "camera", "microphone", "filesystem", "network",
      "notifications", "accessibility", "contacts", "location",
      "process", "clipboard",
  };
  return k;
}
}  // namespace

bool PermissionManager::IsKnown(const std::string& p) {
  return Known().count(p) > 0;
}

std::map<std::string, std::string> PermissionManager::Request(
    const std::vector<std::string>& permissions) {
  std::string granted = "true";
  for (const auto& p : permissions) {
    if (!IsKnown(p)) { granted = "false"; break; }
  }
  return {
      {"ok", "true"},
      {"granted", granted},
  };
}

}  // namespace pai::agent
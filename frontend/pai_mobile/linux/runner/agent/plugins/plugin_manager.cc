// linux/runner/agent/plugins/plugin_manager.cc
#include "plugin_manager.h"

#include <algorithm>

namespace pai::agent {

std::map<std::string, std::string> PluginManager::Invoke(
    const std::string& native_module,
    const std::string& capability,
    const std::map<std::string, std::string>& params) {
  auto m = registry_->Get(native_module);
  if (!m) {
    return {{"ok", "false"}, {"error", "module_not_available"}};
  }

  auto caps = m->Capabilities();
  if (std::find(caps.begin(), caps.end(), capability) == caps.end()) {
    return {{"ok", "false"}, {"error", "capability_not_supported"}};
  }

  // Re-check permissions right before invoking — cheap, and catches
  // the "user unplugged the camera between install and use" case.
  const auto check = permissions_->Request(m->Permissions());
if (check.at("ok") != "true") {
  return {
      {"ok", "false"},
      {"error", "permission_not_granted"},
      {"denied", check.at("denied")},
  };
}

  try {
    return m->Invoke(capability, params);
  } catch (const std::exception& e) {
    return {{"ok", "false"}, {"error", e.what()}};
  }
}

}  // namespace pai::agent
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

  try {
    return m->Invoke(capability, params);
  } catch (const std::exception& e) {
    return {{"ok", "false"}, {"error", e.what()}};
  }
}

std::map<std::string, std::string> PluginManager::RequestPermissions(
    const std::string& native_module,
    const std::vector<std::string>& permissions) {
  auto m = registry_->Get(native_module);
  if (!m) {
    return {{"ok", "false"}, {"error", "module_not_available"}};
  }
  try {
    return m->RequestPermissions(permissions);
  } catch (const std::exception& e) {
    return {{"ok", "false"}, {"error", e.what()}};
  }
}

}  // namespace pai::agent
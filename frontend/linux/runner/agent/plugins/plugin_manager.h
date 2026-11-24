// linux/runner/agent/plugins/plugin_manager.h
#pragma once

#include <map>
#include <string>
#include <vector>

#include "native_module_registry.h"
#include "../permissions/permission_manager.h"

namespace pai::agent {

class PluginManager {
 public:
  PluginManager(NativeModuleRegistry* registry, PermissionManager* permissions)
      : registry_(registry), permissions_(permissions) {}

  std::map<std::string, std::string> Invoke(
      const std::string& native_module,
      const std::string& capability,
      const std::map<std::string, std::string>& params);

  std::map<std::string, std::string> RequestPermissions(
      const std::string& native_module,
      const std::vector<std::string>& permissions);

private:
  NativeModuleRegistry* registry_;
  PermissionManager* permissions_;
};

}  // namespace pai::agent
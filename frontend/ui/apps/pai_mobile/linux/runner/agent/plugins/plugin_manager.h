// linux/runner/agent/plugins/plugin_manager.h
#pragma once

#include <map>
#include <string>
#include <vector>

#include "native_module_registry.h"

namespace pai::agent {

class PluginManager {
 public:
  explicit PluginManager(NativeModuleRegistry* registry) : registry_(registry) {}

  std::map<std::string, std::string> Invoke(
      const std::string& native_module,
      const std::string& capability,
      const std::map<std::string, std::string>& params);

  std::map<std::string, std::string> RequestPermissions(
      const std::string& native_module,
      const std::vector<std::string>& permissions);

 private:
  NativeModuleRegistry* registry_;
};

}  // namespace pai::agent
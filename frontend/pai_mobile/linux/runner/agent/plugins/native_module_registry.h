// linux/runner/agent/plugins/native_module_registry.h
#pragma once

#include <map>
#include <memory>
#include <string>
#include <vector>

#include "plugin_module.h"

namespace pai::agent {

class NativeModuleRegistry {
 public:
  /// Register under an explicit key. If [name] is empty, falls back to
  /// the module's self-reported name.
  void Register(const std::string& name, std::shared_ptr<PluginModule> m);
  void Unregister(const std::string& name);
  std::shared_ptr<PluginModule> Get(const std::string& name) const;
  std::vector<std::string> List() const;
  bool Contains(const std::string& name) const;

  /// Diagnostic — self-reported names vs registered keys.
  std::map<std::string, std::string> DebugKeys() const;

 private:
  std::map<std::string, std::shared_ptr<PluginModule>> modules_;
};

}  // namespace pai::agent
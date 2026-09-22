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
  void Register(std::shared_ptr<PluginModule> m);
  void Unregister(const std::string& name);
  std::shared_ptr<PluginModule> Get(const std::string& name) const;
  std::vector<std::string> List() const;
  bool Contains(const std::string& name) const;

 private:
  std::map<std::string, std::shared_ptr<PluginModule>> modules_;
};

}  // namespace pai::agent
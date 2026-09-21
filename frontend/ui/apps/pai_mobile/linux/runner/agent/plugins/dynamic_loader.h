// linux/runner/agent/plugins/dynamic_loader.h
#pragma once

#include <dlfcn.h>

#include <memory>
#include <string>

#include "plugin_module.h"

namespace pai::agent {

class DynamicLoader {
 public:
  struct Handle {
    void* dl = nullptr;
    pai_plugin_module_t* mod = nullptr;
    void (*destroy)(pai_plugin_module_t*) = nullptr;
    ~Handle();
  };

  /// Load a .so that exports pai_plugin_module_create/destroy.
  /// Throws std::runtime_error on any failure.
  static std::shared_ptr<PluginModule> Load(const std::string& path);
};

}  // namespace pai::agent
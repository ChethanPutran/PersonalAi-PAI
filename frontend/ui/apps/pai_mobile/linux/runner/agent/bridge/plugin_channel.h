#pragma once

#include <flutter_linux/flutter_linux.h>

#include <map>
#include <string>
#include <vector>

#include "../permissions/permission_manager.h"
#include "../plugins/native_module_registry.h"
#include "../plugins/plugin_manager.h"

namespace pai::agent {

class PluginChannel {
 public:
  explicit PluginChannel(FlBinaryMessenger* messenger);

  NativeModuleRegistry registry;
  PluginManager manager{&registry};
  PermissionManager permissions;

 private:
  FlMethodChannel* channel_ = nullptr;
};

}  // namespace pai::agent
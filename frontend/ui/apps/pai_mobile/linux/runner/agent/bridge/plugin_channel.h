// linux/runner/agent/bridge/plugin_channel.h
#pragma once

#include <flutter/binary_messenger.h>
#include <flutter/encodable_value.h>
#include <flutter/method_channel.h>
#include <flutter/method_result.h>

#include <memory>

#include "../permissions/permission_manager.h"
#include "../plugins/native_module_registry.h"
#include "../plugins/plugin_manager.h"

namespace pai::agent {

class PluginChannel {
 public:
  explicit PluginChannel(flutter::BinaryMessenger* messenger);

 private:
  void HandleMethodCall(
      const flutter::MethodCall<flutter::EncodableValue>& call,
      std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result);

  std::unique_ptr<flutter::MethodChannel<flutter::EncodableValue>> channel_;
  NativeModuleRegistry registry_;
  PluginManager manager_{&registry_};
  PermissionManager permissions_;
};

}  // namespace pai::agent
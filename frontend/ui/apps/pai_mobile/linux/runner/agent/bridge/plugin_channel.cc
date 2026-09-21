// linux/runner/agent/bridge/plugin_channel.cc
#include "plugin_channel.h"

#include <flutter/standard_method_codec.h>

#include <map>
#include <string>
#include <vector>

#include "../plugins/dynamic_loader.h"

namespace pai::agent {

namespace {

std::string GetStr(const flutter::EncodableMap& m, const char* key) {
  auto it = m.find(flutter::EncodableValue(key));
  if (it == m.end()) return {};
  if (auto* s = std::get_if<std::string>(&it->second)) return *s;
  return {};
}

std::vector<std::string> GetStrList(const flutter::EncodableMap& m, const char* key) {
  std::vector<std::string> out;
  auto it = m.find(flutter::EncodableValue(key));
  if (it == m.end()) return out;
  if (auto* l = std::get_if<flutter::EncodableList>(&it->second)) {
    out.reserve(l->size());
    for (const auto& v : *l) {
      if (auto* s = std::get_if<std::string>(&v)) out.push_back(*s);
    }
  }
  return out;
}

std::map<std::string, std::string> GetStrMap(const flutter::EncodableMap& m, const char* key) {
  std::map<std::string, std::string> out;
  auto it = m.find(flutter::EncodableValue(key));
  if (it == m.end()) return out;
  if (auto* mm = std::get_if<flutter::EncodableMap>(&it->second)) {
    for (const auto& [k, v] : *mm) {
      auto* ks = std::get_if<std::string>(&k);
      auto* vs = std::get_if<std::string>(&v);
      if (ks && vs) out[*ks] = *vs;
    }
  }
  return out;
}

flutter::EncodableMap ToEncodable(const std::map<std::string, std::string>& m) {
  flutter::EncodableMap out;
  for (const auto& [k, v] : m) {
    out[flutter::EncodableValue(k)] = flutter::EncodableValue(v);
  }
  return out;
}

}  // namespace

PluginChannel::PluginChannel(flutter::BinaryMessenger* messenger) {
  channel_ = std::make_unique<flutter::MethodChannel<flutter::EncodableValue>>(
      messenger, "pai/plugin_runtime",
      &flutter::StandardMethodCodec::GetInstance());

  channel_->SetMethodCallHandler(
      [this](const auto& call, auto result) {
        HandleMethodCall(call, std::move(result));
      });
}

void PluginChannel::HandleMethodCall(
    const flutter::MethodCall<flutter::EncodableValue>& call,
    std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result) {

  const auto* args = std::get_if<flutter::EncodableMap>(call.arguments());

  // ------------------------------------------------------------------
  // listModules
  // ------------------------------------------------------------------
  if (call.method_name() == "listModules") {
    flutter::EncodableList out;
    for (const auto& n : registry_.List()) {
      out.emplace_back(flutter::EncodableValue(n));
    }
    result->Success(flutter::EncodableValue(out));
    return;
  }

  // ------------------------------------------------------------------
  // isModuleAvailable
  // ------------------------------------------------------------------
  if (call.method_name() == "isModuleAvailable") {
    if (!args) { result->Error("BAD_ARGS", "expected map"); return; }
    result->Success(flutter::EncodableValue(registry_.Contains(GetStr(*args, "nativeModule"))));
    return;
  }

  // ------------------------------------------------------------------
  // loadNativeModule
  // ------------------------------------------------------------------
  if (call.method_name() == "loadNativeModule") {
    if (!args) { result->Error("BAD_ARGS", "expected map"); return; }

    const std::string path = GetStr(*args, "artifactPath");
    if (path.empty()) { result->Error("BAD_ARGS", "artifactPath missing"); return; }

    try {
      auto mod = DynamicLoader::Load(path);
      registry_.Register(mod);
      result->Success(flutter::EncodableValue(true));
    } catch (const std::exception& e) {
      result->Error("LOAD_FAILED", e.what());
    }
    return;
  }

  // ------------------------------------------------------------------
  // unloadNativeModule
  // ------------------------------------------------------------------
  if (call.method_name() == "unloadNativeModule") {
    if (!args) { result->Error("BAD_ARGS", "expected map"); return; }
    registry_.Unregister(GetStr(*args, "nativeModule"));
    result->Success();
    return;
  }

  // ------------------------------------------------------------------
  // invoke
  // ------------------------------------------------------------------
  if (call.method_name() == "invoke") {
    if (!args) { result->Error("BAD_ARGS", "expected map"); return; }

    const std::string module = GetStr(*args, "nativeModule");
    const std::string capability = GetStr(*args, "capability");
    auto params = GetStrMap(*args, "parameters");

    auto out = manager_.Invoke(module, capability, params);
    result->Success(flutter::EncodableValue(ToEncodable(out)));
    return;
  }

  // ------------------------------------------------------------------
  // requestPermissions
  // ------------------------------------------------------------------
  if (call.method_name() == "requestPermissions") {
    if (!args) { result->Error("BAD_ARGS", "expected map"); return; }
    auto perms = GetStrList(*args, "permissions");
    auto out = permissions_.Request(perms);
    result->Success(flutter::EncodableValue(ToEncodable(out)));
    return;
  }

  result->NotImplemented();
}

}  // namespace pai::agent
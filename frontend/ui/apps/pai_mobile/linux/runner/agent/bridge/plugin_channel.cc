#include "plugin_channel.h"

#include <cstring>

#include "../plugins/dynamic_loader.h"

namespace pai::agent {

namespace {

std::string GetStr(FlValue* v) {
  if (v == nullptr) return {};
  if (fl_value_get_type(v) != FL_VALUE_TYPE_STRING) return {};
  const gchar* s = fl_value_get_string(v);
  return s ? std::string(s) : std::string();
}

std::vector<std::string> GetStrList(FlValue* v) {
  std::vector<std::string> out;
  if (v == nullptr || fl_value_get_type(v) != FL_VALUE_TYPE_LIST) return out;
  const size_t n = fl_value_get_length(v);
  out.reserve(n);
  for (size_t i = 0; i < n; ++i) {
       out.push_back(GetStr(fl_value_get_list_value(v, i)));
  }
  return out;
}

std::map<std::string, std::string> GetStrMap(FlValue* v) {
  std::map<std::string, std::string> out;
  if (v == nullptr || fl_value_get_type(v) != FL_VALUE_TYPE_MAP) return out;
  const size_t n = fl_value_get_length(v);
  for (size_t i = 0; i < n; ++i) {
       FlValue* k = fl_value_get_map_key(v, i);
    FlValue* val = fl_value_get_map_value(v, i);
    if (fl_value_get_type(k) == FL_VALUE_TYPE_STRING &&
        fl_value_get_type(val) == FL_VALUE_TYPE_STRING) {
      out[fl_value_get_string(k)] = fl_value_get_string(val);
    }
  }
  return out;
}

FlValue* ToFlMap(const std::map<std::string, std::string>& m) {
  FlValue* out = fl_value_new_map();
  for (const auto& [k, v] : m) {
    fl_value_set_string_take(out, k.c_str(), fl_value_new_string(v.c_str()));
  }
  return out;
}

void ReplyMap(FlMethodCall* call, const std::map<std::string, std::string>& m) {
  FlValue* v = ToFlMap(m);
  g_autoptr(FlMethodResponse) resp =
      FL_METHOD_RESPONSE(fl_method_success_response_new(v));
  fl_method_call_respond(call, resp, nullptr);
  fl_value_unref(v);
}

void ReplyBool(FlMethodCall* call, bool b) {
  g_autoptr(FlMethodResponse) resp =
      FL_METHOD_RESPONSE(fl_method_success_response_new(fl_value_new_bool(b)));
  fl_method_call_respond(call, resp, nullptr);
}

void ReplyError(FlMethodCall* call, const char* code, const char* msg) {
  g_autoptr(FlMethodResponse) resp =
      FL_METHOD_RESPONSE(fl_method_error_response_new(code, msg, nullptr));
  fl_method_call_respond(call, resp, nullptr);
}

void ReplyVoid(FlMethodCall* call) {
  g_autoptr(FlMethodResponse) resp =
      FL_METHOD_RESPONSE(fl_method_success_response_new(nullptr));
  fl_method_call_respond(call, resp, nullptr);
}

void MethodCallHandler(FlMethodChannel* /*channel*/,
                       FlMethodCall* call,
                       gpointer user_data) {
  auto* self = static_cast<PluginChannel*>(user_data);
  const gchar* method = fl_method_call_get_name(call);
  FlValue* args = fl_method_call_get_args(call);

  if (std::strcmp(method, "listModules") == 0) {
    FlValue* out = fl_value_new_list();
    for (const auto& n : self->registry.List()) {
      fl_value_append_take(out, fl_value_new_string(n.c_str()));
    }
    g_autoptr(FlMethodResponse) resp =
        FL_METHOD_RESPONSE(fl_method_success_response_new(out));
    fl_method_call_respond(call, resp, nullptr);
    fl_value_unref(out);
    return;
  }

  if (std::strcmp(method, "isModuleAvailable") == 0) {
    std::string name;
    if (args && fl_value_get_type(args) == FL_VALUE_TYPE_MAP) {
      name = GetStr(fl_value_lookup_string(args, "nativeModule"));
    }
    ReplyBool(call, self->registry.Contains(name));
    return;
  }

  if (std::strcmp(method, "loadNativeModule") == 0) {
    std::string path;
    if (args && fl_value_get_type(args) == FL_VALUE_TYPE_MAP) {
      path = GetStr(fl_value_lookup_string(args, "artifactPath"));
    }
    try {
      auto mod = DynamicLoader::Load(path);
      self->registry.Register(mod);
      ReplyBool(call, true);
    } catch (const std::exception& e) {
      ReplyError(call, "LOAD_FAILED", e.what());
    }
    return;
  }

  if (std::strcmp(method, "unloadNativeModule") == 0) {
    std::string name;
    if (args && fl_value_get_type(args) == FL_VALUE_TYPE_MAP) {
      name = GetStr(fl_value_lookup_string(args, "nativeModule"));
    }
    self->registry.Unregister(name);
    ReplyVoid(call);
    return;
  }

  if (std::strcmp(method, "invoke") == 0) {
    std::string module, capability;
    std::map<std::string, std::string> params;
    if (args && fl_value_get_type(args) == FL_VALUE_TYPE_MAP) {
      module = GetStr(fl_value_lookup_string(args, "nativeModule"));
      capability = GetStr(fl_value_lookup_string(args, "capability"));
      params = GetStrMap(fl_value_lookup_string(args, "parameters"));
    }
    ReplyMap(call, self->manager.Invoke(module, capability, params));
    return;
  }

  if (std::strcmp(method, "requestPermissions") == 0) {
    std::vector<std::string> perms;
    if (args && fl_value_get_type(args) == FL_VALUE_TYPE_MAP) {
      perms = GetStrList(fl_value_lookup_string(args, "permissions"));
    }
    ReplyMap(call, self->permissions.Request(perms));
    return;
  }

  g_autoptr(FlMethodResponse) resp =
      FL_METHOD_RESPONSE(fl_method_not_implemented_response_new());
  fl_method_call_respond(call, resp, nullptr);
}

}  // namespace

PluginChannel::PluginChannel(FlBinaryMessenger* messenger) {
  g_autoptr(FlStandardMethodCodec) codec = fl_standard_method_codec_new();
  channel_ = fl_method_channel_new(
      messenger, "pai/plugin_runtime", FL_METHOD_CODEC(codec));
  fl_method_channel_set_method_call_handler(
      channel_, MethodCallHandler, this, nullptr);
}

}  // namespace pai::agent
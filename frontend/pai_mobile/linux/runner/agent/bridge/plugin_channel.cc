#include "plugin_channel.h"

#include <cstring>

#include "../plugins/dynamic_loader.h"

namespace pai::agent
{
  namespace
  {

    std::string GetStr(FlValue *v)
    {
      if (v == nullptr)
        return {};
      if (fl_value_get_type(v) != FL_VALUE_TYPE_STRING)
        return {};
      const gchar *s = fl_value_get_string(v);
      return s ? std::string(s) : std::string();
    }

    std::vector<std::string> GetStrList(FlValue *v)
    {
      std::vector<std::string> out;
      if (v == nullptr || fl_value_get_type(v) != FL_VALUE_TYPE_LIST)
        return out;
      const size_t n = fl_value_get_length(v);
      out.reserve(n);
      for (size_t i = 0; i < n; ++i)
      {
        out.push_back(GetStr(fl_value_get_list_value(v, i)));
      }
      return out;
    }

    std::map<std::string, std::string> GetStrMap(FlValue *v)
    {
      std::map<std::string, std::string> out;
      if (v == nullptr || fl_value_get_type(v) != FL_VALUE_TYPE_MAP)
        return out;
      const size_t n = fl_value_get_length(v);
      for (size_t i = 0; i < n; ++i)
      {
        FlValue *k = fl_value_get_map_key(v, i);
        FlValue *val = fl_value_get_map_value(v, i);
        if (fl_value_get_type(k) == FL_VALUE_TYPE_STRING &&
            fl_value_get_type(val) == FL_VALUE_TYPE_STRING)
        {
          out[fl_value_get_string(k)] = fl_value_get_string(val);
        }
      }
      return out;
    }

    FlValue *ToFlMap(const std::map<std::string, std::string> &m)
    {
      FlValue *out = fl_value_new_map();
      for (const auto &[k, v] : m)
      {
        // "ok" is a boolean in the Dart API (matches Android's mapOf("ok" to true)).
        // Without this, `result['ok'] != true` on the Dart side is always true
        // → every Linux install takes the rollback path.
        FlValue *fv;
        if (k == "ok" && (v == "true" || v == "false"))
        {
          fv = fl_value_new_bool(v == "true");
        }
        else if ((k == "granted" || k == "denied" || k == "capabilities" || k == "permissions") && !v.empty())
        {
          FlValue *list = fl_value_new_list();
          size_t start = 0;
          while (start <= v.size())
          {
            auto end = v.find(',', start);
            if (end == std::string::npos)
              end = v.size();
            fl_value_append_take(list, fl_value_new_string(v.substr(start, end - start).c_str()));
            if (end == v.size())
              break;
            start = end + 1;
          }
          fv = list;
        }
        else
        {
          fv = fl_value_new_string(v.c_str());
        }
        fl_value_set_string_take(out, k.c_str(), fv);
      }
      return out;
    }

    void ReplyMap(FlMethodCall *call, const std::map<std::string, std::string> &m)
    {
      FlValue *v = ToFlMap(m);
      g_autoptr(FlMethodResponse) resp =
          FL_METHOD_RESPONSE(fl_method_success_response_new(v));
      fl_method_call_respond(call, resp, nullptr);
      fl_value_unref(v);
    }

    void ReplyBool(FlMethodCall *call, bool b)
    {
      g_autoptr(FlMethodResponse) resp =
          FL_METHOD_RESPONSE(fl_method_success_response_new(fl_value_new_bool(b)));
      fl_method_call_respond(call, resp, nullptr);
    }

    void ReplyError(FlMethodCall *call, const char *code, const char *msg)
    {
      g_autoptr(FlMethodResponse) resp =
          FL_METHOD_RESPONSE(fl_method_error_response_new(code, msg, nullptr));
      fl_method_call_respond(call, resp, nullptr);
    }

    void ReplyVoid(FlMethodCall *call)
    {
      g_autoptr(FlMethodResponse) resp =
          FL_METHOD_RESPONSE(fl_method_success_response_new(nullptr));
      fl_method_call_respond(call, resp, nullptr);
    }

    std::string JoinList(const std::vector<std::string> &v)
    {
      std::string s;
      for (size_t i = 0; i < v.size(); ++i)
      {
        if (i)
          s += ",";
        s += v[i];
      }
      return s;
    }


    void MethodCallHandler(FlMethodChannel * /*channel*/,
                           FlMethodCall *call,
                           gpointer user_data)
    {
      auto *self = static_cast<PluginChannel *>(user_data);
      const gchar *method = fl_method_call_get_name(call);
      FlValue *args = fl_method_call_get_args(call);

      if (std::strcmp(method, "listModules") == 0)
      {
        FlValue *out = fl_value_new_list();
        for (const auto &n : self->registry.List())
        {
          fl_value_append_take(out, fl_value_new_string(n.c_str()));
        }
        g_autoptr(FlMethodResponse) resp =
            FL_METHOD_RESPONSE(fl_method_success_response_new(out));
        fl_method_call_respond(call, resp, nullptr);
        fl_value_unref(out);
        return;
      }

      if (std::strcmp(method, "isModuleAvailable") == 0)
      {
        std::string name;
        if (args && fl_value_get_type(args) == FL_VALUE_TYPE_MAP)
        {
          name = GetStr(fl_value_lookup_string(args, "nativeModule"));
        }
        ReplyBool(call, self->registry.Contains(name));
        return;
      }

      if (std::strcmp(method, "loadNativeModule") == 0)
      {
        std::string path, name;
        if (args && fl_value_get_type(args) == FL_VALUE_TYPE_MAP)
        {
          name = GetStr(fl_value_lookup_string(args, "nativeModule"));
          path = GetStr(fl_value_lookup_string(args, "artifactPath"));
        }

        if (name.empty() || path.empty())
        {
          ReplyError(call, "INVALID_ARGS",
                     "nativeModule and artifactPath are required");
          return;
        }

        // 1. Load the .so
        std::shared_ptr<PluginModule> mod;
        try
        {
          mod = DynamicLoader::Load(path);
        }
        catch (const std::exception &e)
        {
          ReplyMap(call, {
                             {"ok", "false"},
                             {"error", "load_failed"},
                             {"message", e.what()},
                         });
          return;
        }

        // 2. Ask the plugin what it needs, check availability
        const auto manifest_perms = mod->Permissions();
        const auto perm_result = self->permissions.Request(manifest_perms);
        const bool granted = perm_result.at("ok") == "true";

        if (!granted)
        {
          // 3. Rollback — module is on the stack, its shared_ptr dtor
          //    will call destroy() when `mod` goes out of scope.
          //    Report which were denied.
          ReplyMap(call, {
                             {"ok", "false"},
                             {"error", "permissions_denied"},
                             {"name", mod->Name()},
                             {"permissions", JoinList(manifest_perms)},
                             {"denied", perm_result.at("denied")},
                         });
          return;
        }

        // 4. Success — register and report
        const std::string self_name = mod->Name();
        const std::string registered_name = self_name.empty() ? name : self_name;

        if (!self->registry.Register(registered_name, std::move(mod)))
        {
          // Duplicate key — likely a re-load of an already-registered plugin.
          // Report failure so Dart rolls back rather than silently keeping
          // the stale module.
          ReplyMap(call, {
                             {"ok", "false"},
                             {"error", "already_registered"},
                             {"name", registered_name},
                         });
          return;
        }

        auto registered = self->registry.Get(registered_name);
        if (!registered)
        {
          ReplyMap(call, {
                             {"ok", "false"},
                             {"error", "registration_lost"},
                             {"name", registered_name},
                         });
          return;
        }

        ReplyMap(call, {
                           {"ok", "true"},
                           {"name", registered_name},
                           {"capabilities", JoinList(registered->Capabilities())},
                           {"permissions", perm_result.at("granted")},
                       });
        return;
      }
        if (std::strcmp(method, "debugRegistry") == 0)
        {
          ReplyMap(call, self->registry.DebugKeys());
          return;
        }

        if (std::strcmp(method, "unloadNativeModule") == 0)
        {
          std::string name;
          if (args && fl_value_get_type(args) == FL_VALUE_TYPE_MAP)
          {
            name = GetStr(fl_value_lookup_string(args, "nativeModule"));
          }
          self->registry.Unregister(name);
          ReplyVoid(call);
          return;
        }

        if (std::strcmp(method, "invoke") == 0)
        {
          std::string module, capability;
          std::map<std::string, std::string> params;
          if (args && fl_value_get_type(args) == FL_VALUE_TYPE_MAP)
          {
            module = GetStr(fl_value_lookup_string(args, "nativeModule"));
            capability = GetStr(fl_value_lookup_string(args, "capability"));
            params = GetStrMap(fl_value_lookup_string(args, "parameters"));
          }
          ReplyMap(call, self->manager.Invoke(module, capability, params));
          return;
        }

        if (std::strcmp(method, "requestPermissions") == 0)
        {
          std::vector<std::string> perms;
          if (args && fl_value_get_type(args) == FL_VALUE_TYPE_MAP)
          {
            perms = GetStrList(fl_value_lookup_string(args, "permissions"));
          }
          ReplyMap(call, self->permissions.Request(perms));
          return;
        }

        g_autoptr(FlMethodResponse) resp =
            FL_METHOD_RESPONSE(fl_method_not_implemented_response_new());
        fl_method_call_respond(call, resp, nullptr);
      }

    } // namespace

    PluginChannel::PluginChannel(FlBinaryMessenger *messenger)
    {
      g_autoptr(FlStandardMethodCodec) codec = fl_standard_method_codec_new();
      channel_ = fl_method_channel_new(
          messenger, "pai/plugin_runtime", FL_METHOD_CODEC(codec));
      fl_method_channel_set_method_call_handler(
          channel_, MethodCallHandler, this, nullptr);
    }

  } // namespace pai::agent
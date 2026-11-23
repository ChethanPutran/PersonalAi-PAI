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

    // Convert a single FlValue to its string representation for the
    // string-keyed ABI. Returns (ok, value) — ok=false means "not
    // representable, skip this entry."
    std::pair<bool, std::string> StringifyFlValue(FlValue *v)
    {
      if (v == nullptr)
        return {false, {}};
      switch (fl_value_get_type(v))
      {
      case FL_VALUE_TYPE_STRING:
      {
        const gchar *s = fl_value_get_string(v);
        return {true, s ? std::string(s) : std::string()};
      }
      case FL_VALUE_TYPE_BOOL:
        // Lowercase — matches the `== "true"` idiom used by plugins
        // that have not yet adopted pai::plugin::detail::GetBool.
        return {true, fl_value_get_bool(v) ? "true" : "false"};
      case FL_VALUE_TYPE_INT:
        return {true, std::to_string(fl_value_get_int(v))};
      case FL_VALUE_TYPE_FLOAT:
      {
        char buf[64];
        std::snprintf(buf, sizeof(buf), "%g", fl_value_get_float(v));
        return {true, std::string(buf)};
      }
      case FL_VALUE_TYPE_NULL:
        // Preserve "not provided" semantics — do NOT convert to "".
        // A missing `content` should remain missing, not become an
        // explicit empty-string write.
        return {false, {}};
      default:
        // LIST, MAP, BYTES — not representable at the string ABI.
        // Plugins that need them will need a separate encoding
        // (base64 / JSON) which is a future ABI change.
        return {false, {}};
      }
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
        if (fl_value_get_type(k) != FL_VALUE_TYPE_STRING)
          continue;
        auto [ok, s] = StringifyFlValue(val);
        if (ok)
          out[fl_value_get_string(k)] = std::move(s);
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
        else if ((k == "granted" || k == "denied" || k == "capabilities" || k == "permissions" || k == "pending") && !v.empty())
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
       std::vector<std::string> requested;
        if (args && fl_value_get_type(args) == FL_VALUE_TYPE_MAP)
        {
          name = GetStr(fl_value_lookup_string(args, "nativeModule"));
          path = GetStr(fl_value_lookup_string(args, "artifactPath"));
          requested = GetStrList(fl_value_lookup_string(args, "permissions"));
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

         // 2. Permissions to check: prefer the manifest list sent from Dart.
        //    Only fall back to whatever the .so declares if the manifest
        //    was empty — otherwise a plugin that forgets to implement
        //    Permissions() silently bypasses the consent dialog.
        const auto plugin_declared = mod->Permissions();
        const auto to_check = requested.empty() ? plugin_declared : requested;
         const auto perm_result = self->permissions.Request(to_check);

        if (perm_result.at("ok") != "true")
        {
          auto it = perm_result.find("pending");
          if (it != perm_result.end() && !it->second.empty())
          {
            ReplyMap(call, {
                               {"ok", "false"},
                               {"error", "permissions_pending"},
                               {"name", mod->Name()},
                               {"pending", it->second},
                           });
            return;
          }
                    ReplyMap(call, {
                             {"ok", "false"},
                             {"error", "permissions_denied"},
                             {"name", mod->Name()},
                             {"permissions", JoinList(to_check)},
                             {"denied", perm_result.at("denied")},
                         });
          return;
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

      if (std::strcmp(method, "grantPermissions") == 0)
      {
        std::vector<std::string> perms;
        if (args && fl_value_get_type(args) == FL_VALUE_TYPE_MAP)
        {
          perms = GetStrList(fl_value_lookup_string(args, "permissions"));
        }
        self->permissions.SetDecisions(perms, /*allow=*/true);
        self->permissions.ApplyEnvBridges(); // PAI_SCREEN_CONSENT=1 now set
        ReplyMap(call, {{"ok", "true"}, {"granted", JoinList(perms)}});
        return;
      }

      if (std::strcmp(method, "denyPermissions") == 0)
      {
        std::vector<std::string> perms;
        if (args && fl_value_get_type(args) == FL_VALUE_TYPE_MAP)
        {
          perms = GetStrList(fl_value_lookup_string(args, "permissions"));
        }
        self->permissions.SetDecisions(perms, /*allow=*/false);
        ReplyMap(call, {{"ok", "true"}, {"denied", JoinList(perms)}});
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

        // Reflect persisted "allow" decisions into env vars that plugins gate
  // on directly (PAI_SCREEN_CONSENT, etc.). Without this, a user who
  // granted last session would see the C++ gate pass on next launch but
  // the plugin's own consent gate fail — "sometimes works" bug.
  permissions.ApplyEnvBridges();
  }

} // namespace pai::agent
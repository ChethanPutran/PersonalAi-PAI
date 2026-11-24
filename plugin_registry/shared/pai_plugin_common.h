#pragma once
#include "pai_plugin_abi.h"

#include <cstdlib>
#include <cstring>
#include <map>
#include <new>
#include <string>
#include <vector>

namespace pai::plugin
{

    using ResultMap = std::map<std::string, std::string>;
    using DispatchFn = ResultMap (*)(
        const std::string &capability,
        const ResultMap &params,
        std::string &error);

    struct Descriptor
    {
        const char *name;
        std::vector<const char *> capabilities;
        std::vector<const char *> permissions;
        DispatchFn dispatch;
    };

    namespace detail
    {

        inline const Descriptor *&desc_ref()
        {
            static const Descriptor *d = nullptr;
            return d;
        }

        inline char *Dup(const std::string &s)
        {
            char *p = static_cast<char *>(std::malloc(s.size() + 1));
            if (!p)
                throw std::bad_alloc();
            std::memcpy(p, s.c_str(), s.size() + 1);
            return p;
        }

        inline void FreeKvList(pai_kv_list_t *list)
        {
            if (!list || !list->items)
                return;
            for (size_t i = 0; i < list->count; ++i)
            {
                std::free(const_cast<char *>(list->items[i].key));
                std::free(const_cast<char *>(list->items[i].value));
            }
            std::free(const_cast<pai_kv_t *>(list->items));
            list->items = nullptr;
            list->count = 0;
        }

        inline void FreeString(char *s) { std::free(s); }

        inline ResultMap ParamsFromAbi(const pai_kv_list_t *params)
        {
            ResultMap out;
            if (!params || !params->items)
                return out;
            for (size_t i = 0; i < params->count; ++i)
            {
                out[params->items[i].key ? params->items[i].key : ""] =
                    params->items[i].value ? params->items[i].value : "";
            }
            return out;
        }

        inline void ResultToAbi(const ResultMap& m, pai_kv_list_t* out) {
            out->items = nullptr;
            out->count = 0;
            if (m.empty()) return;

            // calloc so unfilled entries have null key/value (free() handles null).
            // Assign to *out immediately so FreeKvList can clean up if Dup throws.
            auto* items = static_cast<pai_kv_t*>(
                std::calloc(m.size(), sizeof(pai_kv_t)));
            if (!items) throw std::bad_alloc();
            out->items = items;
            out->count = m.size();

            size_t i = 0;
            for (const auto& [k, v] : m) {
                items[i].key = Dup(k);
                items[i].value = Dup(v);
                ++i;
            }
        }

        inline const char *Name()
        {
            auto *d = desc_ref();
            return d ? d->name : "";
        }

        inline const char *const *Capabilities(size_t *n)
        {
            auto *d = desc_ref();
            if (!d)
            {
                *n = 0;
                return nullptr;
            }
            *n = d->capabilities.size();
            return d->capabilities.data();
        }

        inline const char *const *Permissions(size_t *n)
        {
            auto *d = desc_ref();
            if (!d)
            {
                *n = 0;
                return nullptr;
            }
            *n = d->permissions.size();
            return d->permissions.data();
        }

        inline int Invoke(
            const char* capability,
            const pai_kv_list_t* params,
            pai_kv_list_t* out,
            char** err) {
            out->items = nullptr;
            out->count = 0;

            auto* d = desc_ref();
            if (!d || !d->dispatch) {
                if (err) *err = Dup("plugin_not_initialized");
                return 1;
            }
            try {
                std::string error;
                auto result = d->dispatch(
                    capability ? capability : "",
                    ParamsFromAbi(params),
                    error);

                // Deliver the structured result regardless of error state.
                // Plugins build rich failure maps ({ok,error,hint,exit_code,stderr}).
                if (!result.empty()) {
                    ResultToAbi(result, out);
                }

                if (!error.empty()) {
                    if (err) *err = Dup(error);
                    return 1;
                }
                return 0;
            } catch (const std::exception& e) {
                if (err) *err = Dup(e.what());
                // FreeKvList is null-safe, so a partially-populated out is fine.
                return 1;
            }
        }
        
        inline int RequestPermissions(
            const char **, size_t, pai_kv_list_t *out, char **)
        {
            out->items = nullptr;
            out->count = 0;
            return 0;
        }

        inline pai_plugin_module_t g_module = {
            PAI_PLUGIN_ABI_VERSION,
            &Name,
            &Capabilities,
            &Permissions,
            &Invoke,
            &RequestPermissions,
            &FreeKvList,
            &FreeString,
        };

    } // namespace detail
} // namespace pai::plugin

#if defined(_WIN32)
#define PAI_EXPORT __declspec(dllexport)
#else
#define PAI_EXPORT __attribute__((visibility("default"))) __attribute__((used))
#endif

extern "C" PAI_EXPORT pai_plugin_module_t *pai_plugin_module_create(void)
{
    return &pai::plugin::detail::g_module;
}

extern "C" PAI_EXPORT void pai_plugin_module_destroy(pai_plugin_module_t *) {}

// Usage in each plugin:
//   PAI_PLUGIN_REGISTER("pai.shell", ({"shell.exec"}), ({"process"}), &DispatchShell)
#define PAI_PLUGIN_REGISTER(NAME, CAPS, PERMS, DISPATCH) \
    namespace                                            \
    {                                                    \
        struct AutoRegistrar                             \
        {                                                \
            AutoRegistrar()                              \
            {                                            \
                static pai::plugin::Descriptor d{        \
                    NAME,                                \
                    std::vector<const char *>            \
                        CAPS,                            \
                    std::vector<const char *>            \
                        PERMS,                           \
                    DISPATCH,                            \
                };                                       \
                pai::plugin::detail::desc_ref() = &d;    \
            }                                            \
        } g_auto_registrar;                              \
    }

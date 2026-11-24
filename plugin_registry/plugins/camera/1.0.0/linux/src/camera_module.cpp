#include "pai_plugin_abi.h"
#include "camera_capture.h"
#include "camera_record.h"

#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <vector>

namespace {

std::vector<const char*> g_caps = {
    "camera.capture",
    "camera.record",
    "camera.stream",
};

std::vector<const char*> g_perms = {
    "camera",
    "microphone",
    "filesystem",
};

const char* Name() { return "pai.camera"; }

// Matches pai_plugin_abi.h: const char* const* (a const pointer to the
// vector's const element array). Returning `const char**` would let the
// host loader mutate the vector's internal array, so the ABI forbids it.
const char* const* Capabilities(size_t* n) {
    *n = g_caps.size();
    return g_caps.data();
}

const char* const* Permissions(size_t* n) {
    *n = g_perms.size();
    return g_perms.data();
}

char* Dup(const std::string& s) {
    char* p = static_cast<char*>(std::malloc(s.size() + 1));
    if (!p) throw std::bad_alloc();
    std::memcpy(p, s.c_str(), s.size() + 1);
    return p;
}

void FreeKvList(pai_kv_list_t* list) {
    if (!list || !list->items) return;
    for (size_t i = 0; i < list->count; ++i) {
        std::free(const_cast<char*>(list->items[i].key));
        std::free(const_cast<char*>(list->items[i].value));
    }
    std::free(const_cast<pai_kv_t*>(list->items));
    list->items = nullptr;
    list->count = 0;
}

void FreeString(char* s) { std::free(s); }

std::map<std::string, std::string> ParamsFromAbi(const pai_kv_list_t* params) {
    std::map<std::string, std::string> out;
    if (!params || !params->items) return out;
    for (size_t i = 0; i < params->count; ++i) {
        out[params->items[i].key ? params->items[i].key : ""] =
            params->items[i].value ? params->items[i].value : "";
    }
    return out;
}

void ResultToAbi(const std::map<std::string, std::string>& m, pai_kv_list_t* out) {
    out->items = nullptr;
    out->count = 0;
    if (m.empty()) return;

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

int Invoke(
    const char* capability,
    const pai_kv_list_t* params,
    pai_kv_list_t* out,
    char** err) {
    out->items = nullptr;
    out->count = 0;

    const char* cap = capability ? capability : "";
    auto p = ParamsFromAbi(params);

    try {
        std::map<std::string, std::string> result;

        if (std::strcmp(cap, "camera.capture") == 0) {
            result = pai::camera::capture(p);
        } else if (std::strcmp(cap, "camera.record") == 0) {
            result = pai::camera::record(p);
        } else if (std::strcmp(cap, "camera.stream") == 0) {
            result = {{"ok", "false"}, {"error", "stream_not_implemented"}};
        } else {
            if (err) *err = Dup("unknown_capability");
            return 1;
        }

        ResultToAbi(result, out);

        // Match the convention of plugins that use pai_plugin_common.h:
        // logical failure (ok:false) is signalled via the error out-param
        // AND the structured result is still delivered.
        auto it = result.find("ok");
        if (it != result.end() && it->second == "false") {
            auto eit = result.find("error");
            if (err) *err = Dup(eit != result.end()
                                ? eit->second.c_str()
                                : "operation_failed");
            return 1;
        }
        return 0;

    } catch (const std::exception& e) {
        if (err) *err = Dup(e.what());
        return 1;
    }
}
int RequestPermissions(
    const char** /*permissions*/,
    size_t /*count*/,
    pai_kv_list_t* out,
    char** /*err*/) {
    out->items = nullptr;
    out->count = 0;
    return 0;
}

pai_plugin_module_t g_module = {
    PAI_PLUGIN_ABI_VERSION,
    &Name,
    &Capabilities,
    &Permissions,
    &Invoke,
    &RequestPermissions,
    &FreeKvList,
    &FreeString,
};

}  // namespace

#ifdef _WIN32
  #define PAI_EXPORT __declspec(dllexport)
#else
  #define PAI_EXPORT __attribute__((visibility("default")))
#endif

extern "C" PAI_EXPORT pai_plugin_module_t* pai_plugin_module_create(void) {
    return &g_module;
}

extern "C" PAI_EXPORT void pai_plugin_module_destroy(pai_plugin_module_t* /*m*/) {
    // Static module — nothing to free.
}
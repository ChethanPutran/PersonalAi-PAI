#include "pai_plugin_abi.h"

#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <vector>

namespace {

std::vector<const char*> g_caps = {"camera.capture", "camera.record"};
std::vector<const char*> g_perms = {"camera", "microphone", "filesystem"};

const char* kName = "pai.camera";

const char* Name() { return kName; }

const char** Capabilities(size_t* n) {
    *n = g_caps.size();
    return g_caps.data();
}

const char** Permissions(size_t* n) {
    *n = g_perms.size();
    return g_perms.data();
}

char* Dup(const std::string& s) {
    char* p = static_cast<char*>(std::malloc(s.size() + 1));
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

int Invoke(
    const char* capability,
    const pai_kv_list_t* /*params*/,
    pai_kv_list_t* out,
    char** err) {

    // ----- Replace this block with the real camera call -----
    std::map<std::string, std::string> result;
    if (std::strcmp(capability, "camera.capture") == 0) {
        result = {{"ok", "true"}, {"path", "/tmp/fake.jpg"}};
    } else if (std::strcmp(capability, "camera.record") == 0) {
        result = {{"ok", "true"}, {"path", "/tmp/fake.mp4"}};
    } else {
        if (err) *err = Dup("unknown_capability");
        return 1;
    }
    // ------------------------------------------------------

    auto* items = static_cast<pai_kv_t*>(
        std::malloc(sizeof(pai_kv_t) * result.size()));
    size_t i = 0;
    for (const auto& [k, v] : result) {
        items[i].key = Dup(k);
        items[i].value = Dup(v);
        ++i;
    }
    out->items = items;
    out->count = result.size();
    return 0;
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

extern "C" pai_plugin_module_t* pai_plugin_module_create(void) {
    return &g_module;
}

extern "C" void pai_plugin_module_destroy(pai_plugin_module_t* /*m*/) {
    // nothing to do for a static module
}
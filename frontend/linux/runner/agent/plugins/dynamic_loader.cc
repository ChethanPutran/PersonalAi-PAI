// linux/runner/agent/plugins/dynamic_loader.cc
#include "dynamic_loader.h"

#include <stdexcept>
#include <vector>

#include "../abi/pai_plugin_abi.h"

namespace pai::agent {

DynamicLoader::Handle::~Handle() {
  if (mod && destroy) destroy(mod);
  if (dl) dlclose(dl);
}

namespace {

class CAbiPluginModule : public PluginModule {
 public:
  explicit CAbiPluginModule(std::shared_ptr<DynamicLoader::Handle> h)
      : h_(std::move(h)) {}

  std::string Name() const override {
    const char* n = h_->mod->name();
    return n ? std::string(n) : std::string();
  }

  std::vector<std::string> Capabilities() const override {
    size_t n = 0;
    const char** caps = h_->mod->capabilities(&n);
    std::vector<std::string> out;
    out.reserve(n);
    for (size_t i = 0; i < n; ++i) {
      if (caps[i]) out.emplace_back(caps[i]);
    }
    return out;
  }

  std::vector<std::string> Permissions() const override {
    size_t n = 0;
    const char** ps = h_->mod->permissions(&n);
    std::vector<std::string> out;
    out.reserve(n);
    for (size_t i = 0; i < n; ++i) {
      if (ps[i]) out.emplace_back(ps[i]);
    }
    return out;
  }

  std::map<std::string, std::string> Invoke(
      const std::string& capability,
      const std::map<std::string, std::string>& params) override {
    std::vector<pai_kv_t> kv;
    kv.reserve(params.size());
    for (const auto& [k, v] : params) {
      kv.push_back({k.c_str(), v.c_str()});
    }
    pai_kv_list_t in{kv.data(), kv.size()};

    pai_kv_list_t out{};
    char* err = nullptr;
    int rc = h_->mod->invoke(capability.c_str(), &in, &out, &err);

    std::map<std::string, std::string> result;
    if (rc != 0) {
      result["ok"] = "false";
      result["error"] = err ? err : "unknown_error";
      if (err && h_->mod->free_string) h_->mod->free_string(err);
      return result;
    }

    for (size_t i = 0; i < out.count; ++i) {
      const auto& item = out.items[i];
      result[item.key ? item.key : ""] = item.value ? item.value : "";
    }
    if (h_->mod->free_kv_list) h_->mod->free_kv_list(&out);
    return result;
  }

  std::map<std::string, std::string> RequestPermissions(
      const std::vector<std::string>& permissions) override {
    std::vector<const char*> c;
    c.reserve(permissions.size());
    for (const auto& p : permissions) c.push_back(p.c_str());

    pai_kv_list_t out{};
    char* err = nullptr;
    int rc = h_->mod->request_permissions(c.data(), c.size(), &out, &err);

    std::map<std::string, std::string> result;
    result["ok"] = (rc == 0) ? "true" : "false";
    if (rc != 0 && err) {
      result["error"] = err;
      if (h_->mod->free_string) h_->mod->free_string(err);
    }
    for (size_t i = 0; i < out.count; ++i) {
      const auto& item = out.items[i];
      result[item.key ? item.key : ""] = item.value ? item.value : "";
    }
    if (h_->mod->free_kv_list) h_->mod->free_kv_list(&out);
    return result;
  }

 private:
  std::shared_ptr<DynamicLoader::Handle> h_;
};

}  // namespace

std::shared_ptr<PluginModule> DynamicLoader::Load(const std::string& path) {
  auto h = std::make_shared<DynamicLoader::Handle>();

  h->dl = dlopen(path.c_str(), RTLD_NOW | RTLD_LOCAL);
  if (!h->dl) {
    const char* e = dlerror();
    throw std::runtime_error(std::string("dlopen failed: ") + (e ? e : "unknown"));
  }

  auto create = reinterpret_cast<pai_plugin_create_fn>(
      dlsym(h->dl, "pai_plugin_module_create"));
  h->destroy = reinterpret_cast<pai_plugin_destroy_fn>(
      dlsym(h->dl, "pai_plugin_module_destroy"));

  if (!create || !h->destroy) {
    dlclose(h->dl);
    h->dl = nullptr;
    throw std::runtime_error("plugin missing create/destroy symbols");
  }

  h->mod = create();
  if (!h->mod) {
    dlclose(h->dl);
    h->dl = nullptr;
    throw std::runtime_error("plugin create returned null");
  }

  if (h->mod->abi_version != PAI_PLUGIN_ABI_VERSION) {
    h->destroy(h->mod);
    h->mod = nullptr;
    dlclose(h->dl);
    h->dl = nullptr;
    throw std::runtime_error("plugin ABI version mismatch");
  }

  return std::make_shared<CAbiPluginModule>(h);
}

}  // namespace pai::agent
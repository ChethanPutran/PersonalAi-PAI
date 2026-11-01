// linux/runner/agent/plugins/native_module_registry.cc
#include "native_module_registry.h"

namespace pai::agent {

void NativeModuleRegistry::Register(const std::string& name,
                                    std::shared_ptr<PluginModule> m) {
if (!m) return;

  const std::string self = m->Name();

    // If the module is already registered under its self‑reported name,
    // refuse the alias.
    if (!self.empty() && modules_.find(self) != modules_.end()) {
      std::fprintf(stderr,
          "[NativeModuleRegistry] Register(%s): already registered as '%s' "
          "— refusing alias\n",
          name.c_str(), self.c_str());
      return;
    }

  std::string key = name;
  if (key.empty()) key = m->Name();  // fall back only when caller didn't supply

  std::fprintf(stderr,
      "[NativeModuleRegistry] Register key='%s' self_name='%s'\n",
      key.c_str(), self.c_str());
  if (!key.empty() && !self.empty() && key != self) {
    std::fprintf(stderr,
        "[NativeModuleRegistry] WARNING: key '%s' != module name '%s' "
        "(registering under caller key; plugin should be updated)\n",
        key.c_str(), self.c_str());
  }

  modules_[key] = std::move(m);
}

void NativeModuleRegistry::Unregister(const std::string& name) {
  modules_.erase(name);
}

std::shared_ptr<PluginModule> NativeModuleRegistry::Get(const std::string& name) const {
  auto it = modules_.find(name);
  return it == modules_.end() ? nullptr : it->second;
}

std::vector<std::string> NativeModuleRegistry::List() const {
  std::vector<std::string> out;
  out.reserve(modules_.size());
  for (const auto& [k, _] : modules_) out.push_back(k);
  return out;
}

bool NativeModuleRegistry::Contains(const std::string& name) const {
  return modules_.find(name) != modules_.end();
}

std::map<std::string, std::string> NativeModuleRegistry::DebugKeys() const {
  std::map<std::string, std::string> out;
  for (const auto& [k, m] : modules_) out[k] = m->Name();
  return out;
}

}  // namespace pai::agent
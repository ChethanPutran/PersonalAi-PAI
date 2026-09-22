// linux/runner/agent/plugins/native_module_registry.cc
#include "native_module_registry.h"

namespace pai::agent {

void NativeModuleRegistry::Register(std::shared_ptr<PluginModule> m) {
  if (!m) return;
  modules_[m->Name()] = std::move(m);
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

}  // namespace pai::agent
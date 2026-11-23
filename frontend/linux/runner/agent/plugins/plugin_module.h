// linux/runner/agent/plugins/plugin_module.h
#pragma once

#include <map>
#include <string>
#include <vector>

namespace pai::agent {

class PluginModule {
 public:
  virtual ~PluginModule() = default;

  virtual std::string Name() const = 0;
  virtual std::vector<std::string> Capabilities() const = 0;
  virtual std::vector<std::string> Permissions() const = 0;

  virtual std::map<std::string, std::string> Invoke(
      const std::string& capability,
      const std::map<std::string, std::string>& params) = 0;

  virtual std::map<std::string, std::string> RequestPermissions(
      const std::vector<std::string>& permissions) = 0;
};

}  // namespace pai::agent
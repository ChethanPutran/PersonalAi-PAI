// linux/runner/agent/permissions/permission_manager.h
#pragma once

#include <map>
#include <string>
#include <vector>

namespace pai::agent {

class PermissionManager {
 public:
  /// On Linux the OS handles most permissions via device nodes / polkit.
  /// This is a placeholder that returns granted for known logical permissions.
  std::map<std::string, std::string> Request(
      const std::vector<std::string>& permissions);

 private:
  static bool IsKnown(const std::string& p);
};

}  // namespace pai::agent
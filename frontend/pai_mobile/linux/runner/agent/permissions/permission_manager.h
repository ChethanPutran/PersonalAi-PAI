// linux/runner/agent/permissions/permission_manager.h
#pragma once

#include <map>
#include <string>
#include <vector>

namespace pai::agent {

// linux/runner/agent/permissions/permission_manager.h
class PermissionManager {
 public:
  /// Linux has no runtime permission dialogs. This returns the *current*
  /// availability of each logical permission by probing device nodes,
  /// sockets, and the filesystem. No user interaction occurs.
  ///
  /// Returns:
  ///   ok      — "true" iff every requested permission is available
  ///   granted — comma-separated list of available permissions
  ///   denied  — comma-separated list of unavailable ones
  std::map<std::string, std::string> Request(
      const std::vector<std::string>& permissions);

 private:
  static bool IsKnown(const std::string& p);
};

}  // namespace pai::agent

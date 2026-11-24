#pragma once
#include <map>
#include <string>
#include <vector>

namespace pai::agent {

class PermissionManager {
 public:
  PermissionManager();  // loads ~/.config/pai/permissions.json

  std::map<std::string, std::string> Request(
      const std::vector<std::string>& permissions);

  // Read-only: what Request() would answer for each permission, as "granted" | "denied" | "pending".
  // No prompt, no logging, no state change. Used to report the user's device-side choices to the backend.
  std::map<std::string, std::string> PermissionStatus(const std::vector<std::string>& permissions) const;

  void SetDecisions(const std::vector<std::string>& permissions, bool allow);

  // Apply stored "allow" decisions as env vars that plugins gate on
  // directly (e.g. PAI_SCREEN_CONSENT). Called after any grant.
  void ApplyEnvBridges();

 private:
  std::map<std::string, std::string> decisions_;  // perm → "allow"|"deny"

  std::string Classify(const std::string& permission) const;
  void Load();
  void Save() const;
};

}  // namespace pai::agent
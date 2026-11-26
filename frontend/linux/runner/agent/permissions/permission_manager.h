#pragma once
#include <map>
#include <string>
#include <vector>

namespace pai::agent {

class PermissionManager {
 public:
  PermissionManager();  // loads ~/.config/pai/permissions.json

  // EVERY decision belongs to ONE plugin (its native module name, e.g. "pai.files"): allowing
  // "filesystem" for the camera plugin says nothing about the files plugin, which is asked on its own.
  // An empty plugin never matches a decision and can never store one (fails closed).
  std::map<std::string, std::string> Request(
      const std::string& plugin, const std::vector<std::string>& permissions);

  // Read-only: what Request() would answer for each permission, as "granted" | "denied" | "pending".
  // No prompt, no logging, no state change. Used to report the user's device-side choices to the backend.
  std::map<std::string, std::string> PermissionStatus(
      const std::string& plugin, const std::vector<std::string>& permissions) const;

  void SetDecisions(const std::string& plugin, const std::vector<std::string>& permissions, bool allow);

  // Uninstall: forget everything this plugin was ever allowed or denied, so a reinstall asks again.
  void Forget(const std::string& plugin);

  // Apply stored "allow" decisions as env vars that plugins gate on directly (e.g. PAI_SCREEN_CONSENT).
  // The variable is process-wide, so it is set while ANY plugin has the permission allowed.
  // Called after any grant.
  void ApplyEnvBridges();

 private:
  // "<plugin>/<permission>" → "allow"|"deny"
  std::map<std::string, std::string> decisions_;
  static std::string Key(const std::string& plugin, const std::string& permission);
  void ApplyBridges(bool unset_when_none_allowed);

  std::string Classify(const std::string& plugin, const std::string& permission) const;
  void Load();
  void Save() const;
};

}  // namespace pai::agent
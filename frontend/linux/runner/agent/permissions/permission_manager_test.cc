// Standalone test of PermissionManager's consent -> plugin env-var bridge (no Flutter needed).
// Built and run by tests/native/test_linux_permission_bridge.py with XDG_CONFIG_HOME set to a temp dir.
#include "permission_manager.h"

#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

using pai::agent::PermissionManager;

static int failures = 0;
#define CHECK(cond)                                                              \
  do {                                                                           \
    if (!(cond)) { std::fprintf(stderr, "FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); ++failures; } \
  } while (0)

static std::string Env(const char* name) {
  const char* v = std::getenv(name);
  return v ? v : "<unset>";
}

static bool Contains(const std::string& csv, const std::string& item) {
  return ("," + csv + ",").find("," + item + ",") != std::string::npos;
}

// PermissionStatus() is the read-only view reported to the backend: it must agree with Request() and change nothing.
static void StatusTests() {
  char dir[] = "/tmp/pai_perm_status_XXXXXX";      // own config dir: leaves the other checks' file alone
  CHECK(::mkdtemp(dir) != nullptr);
  const char* saved = std::getenv("XDG_CONFIG_HOME");
  const std::string saved_copy = saved ? saved : "";
  ::setenv("XDG_CONFIG_HOME", dir, 1);
  PermissionManager pm;
  auto st = pm.PermissionStatus({"input", "screen", "filesystem"});
  CHECK(st["input"] == "pending" && st["screen"] == "pending");     // never asked yet = not allowed yet
  CHECK(st["filesystem"] == "granted");                             // auto-granted class
  pm.PermissionStatus({"input"});                                              // asking for status must not decide anything
  CHECK(pm.PermissionStatus({"input"})["input"] == "pending");
  pm.SetDecisions({"input"}, true);
  CHECK(pm.PermissionStatus({"input", "screen"})["input"] == "granted");
  CHECK(pm.PermissionStatus({"input", "screen"})["screen"] == "pending");     // per permission, not all-or-nothing
  pm.SetDecisions({"input"}, false);
  CHECK(pm.PermissionStatus({"input"})["input"] == "denied");
  CHECK(pm.PermissionStatus({}).empty());
  PermissionManager reloaded;                                        // the decision survives a restart
  CHECK(reloaded.PermissionStatus({"input"})["input"] == "denied");
  if (saved) ::setenv("XDG_CONFIG_HOME", saved_copy.c_str(), 1); else ::unsetenv("XDG_CONFIG_HOME");
  std::string cmd = std::string("rm -rf ") + dir;
  CHECK(std::system(cmd.c_str()) == 0);
}

int main() {
  ::unsetenv("PAI_INPUT_CONSENT");
  ::unsetenv("PAI_SCREEN_CONSENT");
  {
    PermissionManager pm;

    // input is a consent-required permission: the FIRST request must go to the user, never auto-grant.
    auto r = pm.Request({"input"});
    CHECK(r["ok"] == "false");
    CHECK(Contains(r["pending"], "input"));
    CHECK(r["denied"].empty() && r["granted"].empty());

    // an unrelated, auto-granted permission is unaffected by an input request
    auto mixed = pm.Request({"filesystem", "input", "screen"});
    CHECK(Contains(mixed["granted"], "filesystem"));
    CHECK(Contains(mixed["pending"], "input") && Contains(mixed["pending"], "screen"));

    // asking is not consenting: the plugin gate stays closed
    pm.ApplyEnvBridges();
    CHECK(Env("PAI_INPUT_CONSENT") == "<unset>");

    // the user allows input -> granted, and the plugin's env gate opens (screen stays closed)
    pm.SetDecisions({"input"}, true);
    pm.ApplyEnvBridges();
    CHECK(Env("PAI_INPUT_CONSENT") == "1");
    CHECK(Env("PAI_SCREEN_CONSENT") == "<unset>");
    auto after = pm.Request({"input"});
    CHECK(after["ok"] == "true" && Contains(after["granted"], "input"));

    // the user revokes -> the gate closes IMMEDIATELY (not at the next app restart)
    pm.SetDecisions({"input"}, false);
    pm.ApplyEnvBridges();
    CHECK(Env("PAI_INPUT_CONSENT") == "<unset>");
    auto denied = pm.Request({"input"});
    CHECK(denied["ok"] == "false" && Contains(denied["denied"], "input"));
  }

  // decisions persist across launches: a fresh manager re-opens the gate, and a deny stays denied
  {
    PermissionManager pm;
    pm.SetDecisions({"input", "screen"}, true);
  }
  {
    PermissionManager relaunched;
    relaunched.ApplyEnvBridges();
    CHECK(Env("PAI_INPUT_CONSENT") == "1");
    CHECK(Env("PAI_SCREEN_CONSENT") == "1");
    relaunched.SetDecisions({"screen"}, false);       // revoking one must not touch the other
    relaunched.ApplyEnvBridges();
    CHECK(Env("PAI_SCREEN_CONSENT") == "<unset>");
    CHECK(Env("PAI_INPUT_CONSENT") == "1");
  }

  StatusTests();

  if (failures) { std::fprintf(stderr, "%d check(s) failed\n", failures); return 1; }
  std::puts("OK");
  return 0;
}

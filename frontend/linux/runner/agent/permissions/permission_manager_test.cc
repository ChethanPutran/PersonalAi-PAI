// Standalone test of PermissionManager: per-plugin decisions + the consent -> plugin env-var bridge (no Flutter needed).
// Built and run by tests/native/test_linux_permission_bridge.py with XDG_CONFIG_HOME set to a temp dir.
#include "permission_manager.h"

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
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

static const char* kCam = "pai.camera";
static const char* kFiles = "pai.files";
static const char* kInput = "pai.input";
static const char* kScreen = "pai.screen";

// Runs `body` against its own empty config dir (restores XDG_CONFIG_HOME afterwards).
template <typename F>
static void WithConfigDir(F body) {
  char dir[] = "/tmp/pai_perm_XXXXXX";
  CHECK(::mkdtemp(dir) != nullptr);
  const char* saved = std::getenv("XDG_CONFIG_HOME");
  const std::string saved_copy = saved ? saved : "";
  ::setenv("XDG_CONFIG_HOME", dir, 1);
  body(std::string(dir) + "/pai/permissions.json");
  if (saved) ::setenv("XDG_CONFIG_HOME", saved_copy.c_str(), 1); else ::unsetenv("XDG_CONFIG_HOME");
  CHECK(std::system((std::string("rm -rf ") + dir).c_str()) == 0);
}

// PermissionStatus() is the read-only view reported to the backend: it must agree with Request() and change nothing.
static void StatusTests() {
  WithConfigDir([](const std::string&) {
    PermissionManager pm;
    auto st = pm.PermissionStatus(kInput, {"input", "screen", "filesystem"});
    CHECK(st["input"] == "pending" && st["screen"] == "pending");     // never asked yet = not allowed yet
    CHECK(st["filesystem"] == "pending");                             // nothing is auto-granted any more
    pm.PermissionStatus(kInput, {"input"});                           // asking for status must not decide anything
    CHECK(pm.PermissionStatus(kInput, {"input"})["input"] == "pending");
    pm.SetDecisions(kInput, {"input"}, true);
    CHECK(pm.PermissionStatus(kInput, {"input", "screen"})["input"] == "granted");
    CHECK(pm.PermissionStatus(kInput, {"input", "screen"})["screen"] == "pending");   // per permission
    pm.SetDecisions(kInput, {"input"}, false);
    CHECK(pm.PermissionStatus(kInput, {"input"})["input"] == "denied");
    CHECK(pm.PermissionStatus(kInput, {}).empty());
    PermissionManager reloaded;                                       // the decision survives a restart
    CHECK(reloaded.PermissionStatus(kInput, {"input"})["input"] == "denied");
  });
}

// Each plugin is asked for its own permissions: one plugin's "allow" is never another's.
static void IsolationTests() {
  WithConfigDir([](const std::string&) {
    PermissionManager pm;
    // The user allows the camera plugin everything it declares, including filesystem ...
    pm.SetDecisions(kCam, {"camera", "microphone", "filesystem"}, true);
    CHECK(pm.PermissionStatus(kCam, {"camera", "microphone", "filesystem"})["filesystem"] == "granted");
    // ... the files plugin (installed later) still has to ask for filesystem itself.
    CHECK(pm.PermissionStatus(kFiles, {"filesystem"})["filesystem"] == "pending");
    auto r = pm.Request(kFiles, {"filesystem"});
    CHECK(r["ok"] == "false" && Contains(r["pending"], "filesystem") && r["granted"].empty());
    pm.SetDecisions(kFiles, {"filesystem"}, true);
    CHECK(pm.Request(kFiles, {"filesystem"})["ok"] == "true");
    // A deny for one plugin leaves the other alone.
    pm.SetDecisions(kCam, {"filesystem"}, false);
    CHECK(pm.PermissionStatus(kCam, {"filesystem"})["filesystem"] == "denied");
    CHECK(pm.PermissionStatus(kFiles, {"filesystem"})["filesystem"] == "granted");
    // No plugin named = no decision applies, and none can be stored (fails closed).
    CHECK(pm.PermissionStatus("", {"filesystem"})["filesystem"] == "pending");
    pm.SetDecisions("", {"filesystem"}, true);
    CHECK(pm.PermissionStatus("", {"filesystem"})["filesystem"] == "pending");
    CHECK(pm.PermissionStatus("pai.other", {"filesystem"})["filesystem"] == "pending");
    // Plugin names that share a prefix are different plugins.
    pm.SetDecisions("pai.screen", {"screen"}, true);
    CHECK(pm.PermissionStatus("pai.screen2", {"screen"})["screen"] == "pending");
  });
}

// Uninstalling forgets the plugin's decisions, so a reinstall asks again.
static void UninstallTests() {
  WithConfigDir([](const std::string& file) {
    PermissionManager pm;
    pm.SetDecisions(kCam, {"camera", "filesystem"}, true);
    pm.SetDecisions(kFiles, {"filesystem"}, true);
    pm.Forget(kCam);
    CHECK(pm.PermissionStatus(kCam, {"camera", "filesystem"})["camera"] == "pending");
    CHECK(pm.PermissionStatus(kCam, {"camera", "filesystem"})["filesystem"] == "pending");
    CHECK(pm.PermissionStatus(kFiles, {"filesystem"})["filesystem"] == "granted");      // others untouched
    PermissionManager reloaded;                                       // and it is gone from disk too
    CHECK(reloaded.PermissionStatus(kCam, {"camera"})["camera"] == "pending");
    CHECK(reloaded.PermissionStatus(kFiles, {"filesystem"})["filesystem"] == "granted");
    std::ifstream f(file); std::stringstream ss; ss << f.rdbuf();
    CHECK(ss.str().find("pai.camera") == std::string::npos);
    pm.Forget("pai.never-installed");                                 // harmless
    pm.Forget("");
    CHECK(reloaded.PermissionStatus(kFiles, {"filesystem"})["filesystem"] == "granted");
  });
}

// The shared file written by older releases ("camera":"allow" for everyone) must not grant anything.
static void LegacyFileTests() {
  WithConfigDir([](const std::string& file) {
    { PermissionManager first; first.SetDecisions(kCam, {"camera"}, true); }      // creates the dir
    {
      std::ofstream f(file, std::ios::trunc);
      f << "{\n  \"camera\": \"allow\",\n  \"filesystem\": \"allow\",\n  \"input\": \"allow\",\n"
           "  \"pai.files/filesystem\": \"allow\"\n}\n";
    }
    PermissionManager pm;
    CHECK(pm.PermissionStatus(kCam, {"camera", "filesystem"})["camera"] == "pending");
    CHECK(pm.PermissionStatus(kInput, {"input"})["input"] == "pending");
    CHECK(pm.PermissionStatus(kFiles, {"filesystem"})["filesystem"] == "granted");   // a per-plugin entry is kept
    pm.SetDecisions(kCam, {"camera"}, true);                                         // saving drops the old keys
    std::ifstream f(file); std::stringstream ss; ss << f.rdbuf();
    CHECK(ss.str().find("\"camera\":") == std::string::npos && ss.str().find("pai.camera/camera") != std::string::npos);
  });
}

// The env var is process-wide: open while ANY plugin allows it, closed when none does.
static void EnvBridgeAcrossPluginsTests() {
  ::unsetenv("PAI_SCREEN_CONSENT");
  WithConfigDir([](const std::string&) {
    PermissionManager pm;
    pm.SetDecisions(kScreen, {"screen"}, true);
    pm.SetDecisions("pai.other", {"screen"}, true);
    pm.ApplyEnvBridges();
    CHECK(Env("PAI_SCREEN_CONSENT") == "1");
    pm.SetDecisions(kScreen, {"screen"}, false);
    pm.ApplyEnvBridges();
    CHECK(Env("PAI_SCREEN_CONSENT") == "1");            // another plugin still allows it
    pm.Forget("pai.other");                             // uninstall the last one that allowed it
    CHECK(Env("PAI_SCREEN_CONSENT") == "<unset>");
  });
}

int main() {
  ::unsetenv("PAI_INPUT_CONSENT");
  ::unsetenv("PAI_SCREEN_CONSENT");
  {
    PermissionManager pm;

    // input is a consent-required permission: the FIRST request must go to the user, never auto-grant.
    auto r = pm.Request(kInput, {"input"});
    CHECK(r["ok"] == "false");
    CHECK(Contains(r["pending"], "input"));
    CHECK(r["denied"].empty() && r["granted"].empty());

    // filesystem is no longer auto-granted: it asks like everything else
    auto mixed = pm.Request(kInput, {"filesystem", "input", "screen"});
    CHECK(mixed["granted"].empty());
    CHECK(Contains(mixed["pending"], "filesystem") && Contains(mixed["pending"], "input")
          && Contains(mixed["pending"], "screen"));

    // asking is not consenting: the plugin gate stays closed
    pm.ApplyEnvBridges();
    CHECK(Env("PAI_INPUT_CONSENT") == "<unset>");

    // the user allows input -> granted, and the plugin's env gate opens (screen stays closed)
    pm.SetDecisions(kInput, {"input"}, true);
    pm.ApplyEnvBridges();
    CHECK(Env("PAI_INPUT_CONSENT") == "1");
    CHECK(Env("PAI_SCREEN_CONSENT") == "<unset>");
    auto after = pm.Request(kInput, {"input"});
    CHECK(after["ok"] == "true" && Contains(after["granted"], "input"));

    // the user revokes -> the gate closes IMMEDIATELY (not at the next app restart)
    pm.SetDecisions(kInput, {"input"}, false);
    pm.ApplyEnvBridges();
    CHECK(Env("PAI_INPUT_CONSENT") == "<unset>");
    auto denied = pm.Request(kInput, {"input"});
    CHECK(denied["ok"] == "false" && Contains(denied["denied"], "input"));
  }

  // decisions persist across launches: a fresh manager re-opens the gate, and a deny stays denied
  {
    PermissionManager pm;
    pm.SetDecisions(kInput, {"input"}, true);
    pm.SetDecisions(kScreen, {"screen"}, true);
  }
  {
    PermissionManager relaunched;
    relaunched.ApplyEnvBridges();
    CHECK(Env("PAI_INPUT_CONSENT") == "1");
    CHECK(Env("PAI_SCREEN_CONSENT") == "1");
    relaunched.SetDecisions(kScreen, {"screen"}, false);   // revoking one must not touch the other
    relaunched.ApplyEnvBridges();
    CHECK(Env("PAI_SCREEN_CONSENT") == "<unset>");
    CHECK(Env("PAI_INPUT_CONSENT") == "1");
  }

  StatusTests();
  IsolationTests();
  UninstallTests();
  LegacyFileTests();
  EnvBridgeAcrossPluginsTests();

  if (failures) { std::fprintf(stderr, "%d check(s) failed\n", failures); return 1; }
  std::puts("OK");
  return 0;
}

#include "pai_plugin_common.h"
#include "pai_system_helpers.h"

#include <sstream>

namespace {

// DEFAULTS — always active. Override via PAI_SHELL_WHITELIST env
// (semicolon-separated) if you need different commands. The override
// REPLACES the defaults, it does not append.
std::vector<std::string> DefaultWhitelist() {
    return {
        "ls", "pwd", "whoami", "date", "uptime", "hostname", "uname",
        "df", "du", "free", "ps", "top", "htop", "iostat", "vmstat",
        "cat", "head", "tail", "wc", "grep", "find", "stat", "file",
        "git status", "git log", "git diff", "git branch", "git show",
        "systemctl status", "systemctl list-units",
        "docker ps", "docker images", "docker logs",
        "echo",
    };
}

pai::plugin::ResultMap DispatchShell(
    const std::string& capability,
    const pai::plugin::ResultMap& params,
    std::string& error) {

    if (capability != "shell.exec") {
        error = "unknown_capability";
        return {};
    }

    auto it = params.find("command");
    if (it == params.end() || it->second.empty()) {
        error = "missing_required_param: command";
        return {};
    }
    const std::string command = it->second;

    int timeout_sec = 30;
    if (params.count("timeout_sec") && !params.at("timeout_sec").empty()) {
        try { timeout_sec = std::stoi(params.at("timeout_sec")); }
        catch (...) { timeout_sec = 30; }
    }
    if (timeout_sec <= 0) timeout_sec = 30;
    if (timeout_sec > 300) timeout_sec = 300;

    const std::string cwd = params.count("cwd") ? params.at("cwd") : "";

    // Whitelist check — the hard gate
    auto allowed = pai::sys::EnvList(
        "PAI_SHELL_WHITELIST",
        DefaultWhitelist());

    if (!pai::sys::CommandAllowed(command, allowed)) {
        error = "command_not_whitelisted";
        return {
            {"ok", "false"},
            {"error", "command_not_whitelisted"},
            {"command", command},
            {"hint",
             "Set PAI_SHELL_WHITELIST env or add this command prefix to the "
             "plugin manifest. Metacharacters (; & | > < $ `) are never allowed."},
        };
    }

    std::string to_run = command;
    if (!cwd.empty()) {
        std::string resolved;
        if (!pai::sys::PathInRoots(cwd,
                pai::sys::EnvList("PAI_SHELL_CWD_ROOTS",
                    {pai::sys::HomeDir()}), resolved)) {
            error = "cwd_outside_allowed_roots";
            return {
                {"ok", "false"},
                {"error", "cwd_outside_allowed_roots"},
                {"cwd", cwd},
            };
        }
        to_run = "cd " + resolved + " && " + command;
        // Note: `cd X && cmd` has `&&` which our metacharacter check
        // rejects. We validated `command` above, but this combined
        // string bypasses that check. Instead, use `--directory`-style
        // commands where possible; for now, reject cwd entirely.
        // Return a clear error instead of silently corrupting semantics.
        error = "cwd_not_supported_yet";
        return {
            {"ok", "false"},
            {"error", "cwd_not_supported_yet"},
        };
    }

    auto result = pai::sys::RunShell(to_run, timeout_sec);

    std::map<std::string, std::string> out = {
        {"ok", result.exit_code == 0 ? "true" : "false"},
        {"exit_code", std::to_string(result.exit_code)},
        {"stdout", result.stdout_data},
    };
    if (result.timed_out) out["timed_out"] = "true";
    return out;
}

}  // namespace

PAI_PLUGIN_REGISTER(
    "pai.shell",
    ({"shell.exec"}),
    ({"process"}),
    &DispatchShell
)
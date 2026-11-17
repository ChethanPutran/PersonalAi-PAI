#pragma once
#include <optional>
#include <string>
#include <vector>

namespace pai::sys {

struct CommandResult {
    int exit_code = -1;
    std::string stdout_data;
    bool timed_out = false;
};

/// Execute a command with a hard timeout. Uses /usr/bin/timeout. Never
/// goes through a shell — args are exec'd directly.
CommandResult RunCommand(
    const std::vector<std::string>& argv,
    int timeout_sec = 30);

/// Same, but goes through `sh -c`. Caller MUST have validated the string
/// against a whitelist before calling this.
CommandResult RunShell(
    const std::string& command,
    int timeout_sec = 30);

/// Expand ~ to $HOME, ~user is not supported.
std::string ExpandTilde(const std::string& path);

/// Realpath, if the file exists. Returns "" if not.
std::string ResolvePath(const std::string& path);

/// True if `path` resolves to a location inside one of `roots`.
/// Used by system.files before touching anything.
bool PathInRoots(
    const std::string& path,
    const std::vector<std::string>& roots,
    std::string& resolved_out);

/// True if `cmd` starts with one of `prefixes` AND contains no shell
/// metacharacters. See ./pai_system_helpers.cpp for the exact check.
bool CommandAllowed(
    const std::string& cmd,
    const std::vector<std::string>& prefixes);

/// True if `name` is on PATH.
bool CommandExists(const std::string& name);

/// "x11" | "wayland" | "unknown"
std::string SessionType();

std::string HomeDir();

/// Read a `;`-separated list from env `name`, fall back to `fallback`.
/// Trims whitespace from each item.
std::vector<std::string> EnvList(
    const std::string& name,
    std::vector<std::string> fallback);

/// Read env `name`, or `fallback` if unset/empty.
std::string EnvString(const std::string& name, const std::string& fallback);

}  // namespace pai::sys
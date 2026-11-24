#pragma once
#include <string>
#include <vector>

namespace pai::sys {

struct CommandResult {
    int exit_code = -1;
    std::string stdout_data;
    std::string stderr_data;
    bool timed_out = false;
    bool output_truncated = false;  // stdout or stderr hit the 10 MB cap
};

/// Execute `argv` directly (no shell) with a hard timeout. Enforced by
/// killing the child's process group on expiry. Captures stdout/stderr.
CommandResult RunCommand(
    const std::vector<std::string>& argv,
    int timeout_sec = 30);

/// Run `sh -c command` with a hard timeout. Captures stdout/stderr.
/// Caller MUST have validated `command` against a whitelist first.
CommandResult RunShell(
    const std::string& command,
    int timeout_sec = 30);

/// Same as above, but the child runs with its cwd set to `cwd`.
/// If `cwd` is empty, behaves identically to RunShell(command, timeout_sec).
CommandResult RunShell(const std::string& command,
                       const std::string& cwd,
                       int timeout_sec = 30);

/// Run `argv` with stdin fed from `input_file`, stdout/stderr discarded,
/// and no output capture. Safe for tools that daemonize (xclip, wl-copy)
/// because we only wait for the direct child — the daemon continues
/// independently. Returns the direct child's exit code.
CommandResult RunCommandSilent(
    const std::vector<std::string>& argv,
    const std::string& input_file,
    int timeout_sec = 30);

/// Fork+exec `argv` fully detached (double-fork, setsid, fds to /dev/null).
/// Returns true if the spawn succeeded. Does not wait for the child.
bool SpawnDetached(const std::vector<std::string>& argv);

/// Expand `~` to $HOME. `~user` is not supported.
std::string ExpandTilde(const std::string& path);

/// realpath() if the path exists, "" otherwise.
std::string ResolvePath(const std::string& path);

/// True if `path` resolves inside one of `roots`. Rejects `..` and any
/// symlink that escapes. Used by system.files before touching anything.
bool PathInRoots(
    const std::string& path,
    const std::vector<std::string>& roots,
    std::string& resolved_out);

/// True if `cmd` starts with one of `prefixes` AND contains no shell
/// metacharacters (`; & | > < $ \` \n \r`). This is the hard backstop.
bool CommandAllowed(
    const std::string& cmd,
    const std::vector<std::string>& prefixes);

/// True if `name` is executable on PATH.
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
#include "pai_system_helpers.h"

#include <cerrno>
#include <csignal>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <poll.h>
#include <pwd.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

namespace pai::sys {

namespace {

std::string Trim(const std::string& s) {
    size_t a = 0, b = s.size();
    while (a < b && std::isspace(static_cast<unsigned char>(s[a]))) ++a;
    while (b > a && std::isspace(static_cast<unsigned char>(s[b - 1]))) --b;
    return s.substr(a, b - a);
}

// Read everything from fd into out. Returns true on clean EOF.
bool DrainFd(int fd, std::string& out) {
    char buf[4096];
    for (;;) {
        ssize_t n = ::read(fd, buf, sizeof(buf));
        if (n > 0) { out.append(buf, static_cast<size_t>(n)); continue; }
        if (n == 0) return true;
        if (errno == EINTR) continue;
        return false;
    }
}

// Turn a vector<string> into a null-terminated char*[] for execvp.
std::vector<char*> BuildArgv(const std::vector<std::string>& argv) {
    std::vector<char*> cargv;
    cargv.reserve(argv.size() + 1);
    for (const auto& a : argv) cargv.push_back(const_cast<char*>(a.c_str()));
    cargv.push_back(nullptr);
    return cargv;
}

// Fork+exec, capture stdout/stderr, enforce timeout. `argv` is exec'd
// directly (no shell). `cwd` is optional; if non-empty, the child
// chdir's to `cwd` (resolved via realpath) before exec.
CommandResult RunChild(
    const std::vector<std::string>& argv,
    const std::string& cwd,
    int timeout_sec)
{
    CommandResult res;
    if (argv.empty()) { res.exit_code = -1; return res; }

    int out_pipe[2], err_pipe[2];
    if (::pipe(out_pipe) != 0) { res.exit_code = -1; return res; }
    if (::pipe(err_pipe) != 0) {
        ::close(out_pipe[0]); ::close(out_pipe[1]);
        res.exit_code = -1; return res;
    }

    pid_t pid = ::fork();
    if (pid < 0) {
        ::close(out_pipe[0]); ::close(out_pipe[1]);
        ::close(err_pipe[0]); ::close(err_pipe[1]);
        res.exit_code = -1;
        return res;
    }

    if (pid == 0) {
        // ---- child ----
        ::setpgid(0, 0);   // own pgid so we can kill the whole tree
        ::dup2(out_pipe[1], STDOUT_FILENO);
        ::dup2(err_pipe[1], STDERR_FILENO);
        ::close(out_pipe[0]); ::close(out_pipe[1]);
        ::close(err_pipe[0]); ::close(err_pipe[1]);

        if (!cwd.empty()) {
            char* resolved = ::realpath(cwd.c_str(), nullptr);
            if (!resolved || ::chdir(resolved) != 0) {
                std::free(resolved);
                const char msg[] = "pai: cwd_invalid\n";
                (void)!::write(STDERR_FILENO, msg, sizeof(msg) - 1);
                ::_exit(127);
            }
            std::free(resolved);
        }

        auto cargv = BuildArgv(argv);
        ::execvp(cargv[0], cargv.data());

        const char msg[] = "pai: exec_failed\n";
        (void)!::write(STDERR_FILENO, msg, sizeof(msg) - 1);
        ::_exit(127);
    }

    // ---- parent ----
    ::close(out_pipe[1]);
    ::close(err_pipe[1]);

        std::string out, err;
    int out_fd = out_pipe[0], err_fd = err_pipe[0];
    bool out_open = true, err_open = true;
    bool truncated = false;
    static constexpr size_t kMaxCapture = 10 * 1024 * 1024;  // 10 MB per stream

    const int total_ms = (timeout_sec > 0 ? timeout_sec : 30) * 1000;
    int waited_ms = 0;
    bool timed_out = false;

    struct pollfd fds[2];
    while (out_open || err_open) {
        if (waited_ms >= total_ms) { timed_out = true; break; }

        fds[0].fd = out_open ? out_fd : -1;
        fds[0].events = POLLIN;
        fds[1].fd = err_open ? err_fd : -1;
        fds[1].events = POLLIN;

        int pr = ::poll(fds, 2, 100);
        if (pr < 0) {
            if (errno == EINTR) continue;
            break;
        }
        if (pr == 0) { waited_ms += 100; continue; }

        if (out_open && (fds[0].revents & (POLLIN | POLLHUP))) {
            if (out.size() >= kMaxCapture) {
                // Drain without buffering so the child doesn't block on
                // a full pipe; flag truncation.
                std::string discard;
                if (!DrainFd(out_fd, discard)) out_open = false;
                truncated = true;
            } else {
                if (!DrainFd(out_fd, out)) out_open = false;
            }
            if (fds[0].revents & POLLHUP) out_open = false;
        }
        if (err_open && (fds[1].revents & (POLLIN | POLLHUP))) {
            if (err.size() >= kMaxCapture) {
                std::string discard;
                if (!DrainFd(err_fd, discard)) err_open = false;
                truncated = true;
            } else {
                if (!DrainFd(err_fd, err)) err_open = false;
            }
            if (fds[1].revents & POLLHUP) err_open = false;
        }
    }

    ::close(out_fd);
    ::close(err_fd);
    res.stdout_data = std::move(out);
    res.stderr_data = std::move(err);

    // Wait for the child, still respecting the timeout.
    int status = 0;
    for (;;) {
        pid_t w = ::waitpid(pid, &status, WNOHANG);
        if (w == pid) break;
        if (w < 0) {
            if (errno == EINTR) continue;
            res.exit_code = -1;
            return res;
        }
        if (timed_out || waited_ms >= total_ms) {
            ::kill(-pid, SIGKILL);
            (void)::waitpid(pid, &status, 0);
            res.timed_out = true;
            res.exit_code = 124;   // timeout(1) convention
            return res;
        }
        ::usleep(100 * 1000);
        waited_ms += 100;
    }

    if (WIFEXITED(status)) {
        res.exit_code = WEXITSTATUS(status);
    } else if (WIFSIGNALED(status)) {
        res.exit_code = 128 + WTERMSIG(status);
    } else {
        res.exit_code = -1;
    }
    return res;
}



// Resolve `p` to a canonical absolute path, walking up to the first
// existing ancestor and re-appending the non-existent remainder.
// Unlike realpath(), works for paths whose leaf or intermediate
// directories don't exist yet (write targets).
std::string ResolveOrAncestor(const std::string& p) {
    if (p.empty()) return "";
    std::string current = p;
    std::string remainder;
    while (!current.empty()) {
        char* resolved = ::realpath(current.c_str(), nullptr);
        if (resolved) {
            std::string base(resolved);
            std::free(resolved);
            return remainder.empty() ? base : (base + "/" + remainder);
        }
        size_t slash = current.find_last_of('/');
        if (slash == std::string::npos) {
            // No more separators — relative path with no existing prefix.
            return "";
        }
        std::string leaf = current.substr(slash + 1);
        remainder = remainder.empty() ? leaf : (leaf + "/" + remainder);
        current = (slash == 0) ? "/" : current.substr(0, slash);
        if (current == "/") {
            char* root = ::realpath("/", nullptr);
            if (!root) return "";
            std::string base(root);
            std::free(root);
            return remainder.empty() ? base : (base + remainder);
        }
    }
    return "";
}

}  // namespace

CommandResult RunCommand(const std::vector<std::string>& argv, int timeout_sec) {
    return RunChild(argv, "", timeout_sec);
}

CommandResult RunShell(const std::string& command, int timeout_sec) {
    return RunChild({"/bin/sh", "-c", command}, "", timeout_sec);
}

CommandResult RunShell(const std::string& command,
                       const std::string& cwd,
                       int timeout_sec) {
    return RunChild({"/bin/sh", "-c", command}, cwd, timeout_sec);
}

CommandResult RunCommandSilent(
    const std::vector<std::string>& argv,
    const std::string& input_file,
    int timeout_sec)
{
    CommandResult res;
    if (argv.empty()) { res.exit_code = -1; return res; }

    int in_fd = -1;
    if (!input_file.empty()) {
        in_fd = ::open(input_file.c_str(), O_RDONLY);
        if (in_fd < 0) { res.exit_code = -1; return res; }
    }

    pid_t pid = ::fork();
    if (pid < 0) {
        if (in_fd >= 0) ::close(in_fd);
        res.exit_code = -1;
        return res;
    }

    if (pid == 0) {
        ::setpgid(0, 0);
        if (in_fd >= 0) {
            ::dup2(in_fd, STDIN_FILENO);
            ::close(in_fd);
        }
        int devnull = ::open("/dev/null", O_WRONLY);
        if (devnull >= 0) {
            ::dup2(devnull, STDOUT_FILENO);
            ::dup2(devnull, STDERR_FILENO);
            if (devnull > 2) ::close(devnull);
        }
        auto cargv = BuildArgv(argv);
        ::execvp(cargv[0], cargv.data());
        ::_exit(127);
    }

    if (in_fd >= 0) ::close(in_fd);

    const int total_ms = (timeout_sec > 0 ? timeout_sec : 30) * 1000;
    int waited_ms = 0;
    int status = 0;
    for (;;) {
        pid_t w = ::waitpid(pid, &status, WNOHANG);
        if (w == pid) break;
        if (w < 0) {
            if (errno == EINTR) continue;
            res.exit_code = -1;
            return res;
        }
        if (waited_ms >= total_ms) {
            ::kill(-pid, SIGKILL);
            (void)::waitpid(pid, &status, 0);
            res.timed_out = true;
            res.exit_code = 124;
            return res;
        }
        ::usleep(100 * 1000);
        waited_ms += 100;
    }

    if (WIFEXITED(status)) {
        res.exit_code = WEXITSTATUS(status);
    } else if (WIFSIGNALED(status)) {
        res.exit_code = 128 + WTERMSIG(status);
    } else {
        res.exit_code = -1;
    }
    return res;
}

bool SpawnDetached(const std::vector<std::string>& argv) {
    if (argv.empty()) return false;

    pid_t pid = ::fork();
    if (pid < 0) return false;

    if (pid == 0) {
        // Intermediate child: detach from controlling terminal, then
        // double-fork so the grandchild is reparented to init.
        ::setsid();
        pid_t pid2 = ::fork();
        if (pid2 < 0) ::_exit(127);
        if (pid2 > 0) ::_exit(0);

        // ---- grandchild: fully detached ----
        int devnull = ::open("/dev/null", O_RDWR);
        if (devnull >= 0) {
            ::dup2(devnull, STDIN_FILENO);
            ::dup2(devnull, STDOUT_FILENO);
            ::dup2(devnull, STDERR_FILENO);
            if (devnull > 2) ::close(devnull);
        }
        auto cargv = BuildArgv(argv);
        ::execvp(cargv[0], cargv.data());
        ::_exit(127);
    }

    // Reap intermediate; grandchild is now owned by init.
    (void)::waitpid(pid, nullptr, 0);
    return true;
}

std::string HomeDir() {
    const char* h = std::getenv("HOME");
    if (h && *h) return h;
    struct passwd* pw = ::getpwuid(::getuid());
    if (pw && pw->pw_dir) return pw->pw_dir;
    return "/";
}

std::string ExpandTilde(const std::string& path) {
    if (path.empty() || path[0] != '~') return path;
    if (path.size() == 1) return HomeDir();
    if (path[1] == '/') return HomeDir() + path.substr(1);
    return path;
}

std::string ResolvePath(const std::string& path) {
    // POSIX-2008: realpath(path, NULL) allocates the buffer. Removes
    // the PATH_MAX cap and the <climits> dependency.
    char* resolved = ::realpath(path.c_str(), nullptr);
    if (!resolved) return "";
    std::string out(resolved);
    std::free(resolved);
    return out;
}


bool PathInRoots(
    const std::string& path,
    const std::vector<std::string>& roots,
    std::string& resolved_out) {
    std::string expanded = ExpandTilde(path);

    if (expanded.find("/../") != std::string::npos ||
        (expanded.size() >= 3 &&
         expanded.substr(expanded.size() - 3) == "/..")) {
        return false;
    }

    std::string real = ResolveOrAncestor(expanded);
    if (real.empty()) return false;

    for (const auto& r : roots) {
        std::string root_real = ResolveOrAncestor(ExpandTilde(r));
        if (root_real.empty()) continue;
        if (real == root_real) { resolved_out = real; return true; }
        if (real.size() > root_real.size() &&
            real.compare(0, root_real.size(), root_real) == 0 &&
            real[root_real.size()] == '/') {
            resolved_out = real;
            return true;
        }
    }
    return false;
}


bool CommandAllowed(const std::string& cmd, const std::vector<std::string>& prefixes) {
    // Hard backstop: no shell metacharacters anywhere in the string,
    // even if a prefix matches.
    static const char* forbidden = ";&|<>$`\n\r";
    for (char c : cmd) {
        if (std::strchr(forbidden, c)) return false;
    }

    std::string trimmed = Trim(cmd);
    if (trimmed.empty()) return false;

    for (const auto& raw_prefix : prefixes) {
        std::string prefix = Trim(raw_prefix);
        if (prefix.empty()) continue;
        if (trimmed.size() < prefix.size()) continue;
        if (trimmed.compare(0, prefix.size(), prefix) != 0) continue;

        // Require word boundary after the prefix
        if (trimmed.size() == prefix.size()) return true;
        char next = trimmed[prefix.size()];
        if (next == ' ' || next == '\t') return true;
    }
    return false;
}

bool CommandExists(const std::string& name) {
    const char* path = std::getenv("PATH");
    if (!path) return false;
    std::string p(path);
    size_t start = 0;
    while (start < p.size()) {
        size_t end = p.find(':', start);
        if (end == std::string::npos) end = p.size();
        std::string dir = p.substr(start, end - start);
        if (!dir.empty()) {
            std::string full = dir + "/" + name;
            struct stat st{};
            if (::stat(full.c_str(), &st) == 0 && (st.st_mode & S_IXUSR)) return true;
        }
        start = end + 1;
    }
    return false;
}

std::string SessionType() {
    const char* t = std::getenv("XDG_SESSION_TYPE");
    if (t && *t) return t;
    if (std::getenv("WAYLAND_DISPLAY")) return "wayland";
    if (std::getenv("DISPLAY")) return "x11";
    return "unknown";
}

std::vector<std::string> EnvList(
    const std::string& name, std::vector<std::string> fallback) {
    const char* env = std::getenv(name.c_str());
    if (!env || !*env) return fallback;

    std::vector<std::string> out;
    std::string s(env);
    size_t start = 0;
    while (start < s.size()) {
        size_t end = s.find(';', start);
        if (end == std::string::npos) end = s.size();
        std::string item = Trim(s.substr(start, end - start));
        if (!item.empty()) out.push_back(item);
        start = end + 1;
    }
    return out.empty() ? fallback : out;
}

std::string EnvString(const std::string& name, const std::string& fallback) {
    const char* env = std::getenv(name.c_str());
    return (env && *env) ? std::string(env) : fallback;
}

}  // namespace pai::sys
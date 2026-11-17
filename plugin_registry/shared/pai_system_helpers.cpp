#include "pai_system_helpers.h"

#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <pwd.h>
#include <sstream>
#include <sys/stat.h>
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

std::string ShellQuote(const std::string& s) {
    std::string out = "'";
    for (char c : s) {
        if (c == '\'') out += "'\\''";
        else out += c;
    }
    out += "'";
    return out;
}

}  // namespace

CommandResult RunCommand(const std::vector<std::string>& argv, int timeout_sec) {
    CommandResult res;
    if (argv.empty()) {
        res.exit_code = -1;
        return res;
    }

    std::string full = "/usr/bin/timeout " + std::to_string(timeout_sec) + "s";
    for (const auto& a : argv) full += " " + ShellQuote(a);

    FILE* fp = ::popen(full.c_str(), "r");
    if (!fp) {
        res.exit_code = -1;
        return res;
    }

    char buf[4096];
    size_t n;
    while ((n = std::fread(buf, 1, sizeof(buf), fp)) > 0) {
        res.stdout_data.append(buf, n);
    }

    int status = ::pclose(fp);
    if (WIFEXITED(status)) {
        res.exit_code = WEXITSTATUS(status);
        if (res.exit_code == 124) res.timed_out = true;  // timeout(1) convention
    } else {
        res.exit_code = -1;
    }
    return res;
}

CommandResult RunShell(const std::string& command, int timeout_sec) {
    CommandResult res;
    std::string full = "/usr/bin/timeout " + std::to_string(timeout_sec) + "s ";
    full += "sh -c " + ShellQuote(command);

    FILE* fp = ::popen(full.c_str(), "r");
    if (!fp) { res.exit_code = -1; return res; }

    char buf[4096];
    size_t n;
    while ((n = std::fread(buf, 1, sizeof(buf), fp)) > 0) {
        res.stdout_data.append(buf, n);
    }

    int status = ::pclose(fp);
    if (WIFEXITED(status)) {
        res.exit_code = WEXITSTATUS(status);
        if (res.exit_code == 124) res.timed_out = true;
    }
    return res;
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
    char buf[PATH_MAX];
    if (::realpath(path.c_str(), buf)) return std::string(buf);
    return "";
}

bool PathInRoots(
    const std::string& path,
    const std::vector<std::string>& roots,
    std::string& resolved_out) {
    std::string expanded = ExpandTilde(path);

    // Reject attempts to escape via ..
    if (expanded.find("/../") != std::string::npos ||
        expanded.size() >= 3 && expanded.substr(expanded.size() - 3) == "/..") {
        return false;
    }

    // If the path exists, use realpath (resolves symlinks).
    // If not, build the canonical path from the parent.
    std::string real = ResolvePath(expanded);
    if (real.empty()) {
        // For non-existent files (write targets), resolve the parent.
        size_t slash = expanded.find_last_of('/');
        if (slash == std::string::npos) {
            real = ResolvePath(".") + "/" + expanded;
        } else {
            std::string parent = expanded.substr(0, slash);
            std::string leaf = expanded.substr(slash + 1);
            std::string parent_real = ResolvePath(parent);
            if (parent_real.empty()) return false;
            real = parent_real + "/" + leaf;
        }
    }

    for (const auto& r : roots) {
        std::string root_real = ResolvePath(ExpandTilde(r));
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
    // Reject shell metacharacters anywhere in the string. This is the
    // hard backstop: even if a prefix matches, no `;`, `&`, `|`, `>`,
    // `<`, `$`, backtick, or newline can sneak through.
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
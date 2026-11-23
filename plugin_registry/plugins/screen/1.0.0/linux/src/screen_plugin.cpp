#include "pai_plugin_common.h"
#include "pai_system_helpers.h"

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <sys/stat.h>

namespace {

// Recursively create `path` (mkdir -p). No shell, no injection.
bool EnsureDir(const std::string& path) {
    if (path.empty()) return false;
    std::string partial;
    size_t start = 0;
    if (path[0] == '/') { partial = "/"; start = 1; }
    while (start <= path.size()) {
        size_t end = path.find('/', start);
        if (end == std::string::npos) end = path.size();
        if (end > start) {
            partial += path.substr(start, end - start);
            if (::mkdir(partial.c_str(), 0755) != 0 && errno != EEXIST) {
                return false;
            }
            partial += "/";
        }
        start = end + 1;
        if (end == path.size()) break;
    }
    return true;
}

std::string DefaultOutputPath() {
    char ts[32];
    std::time_t t = std::time(nullptr);
    std::strftime(ts, sizeof(ts), "%Y%m%d_%H%M%S", std::localtime(&t));
    std::string dir = pai::sys::EnvString(
        "PAI_SCREEN_DIR",
        std::string(pai::sys::HomeDir()) + "/Pictures/PAI");

    if (!EnsureDir(dir)) {
        std::fprintf(stderr, "pai.screen: cannot create %s\n", dir.c_str());
        // Fall through — RunCommand will fail with a clear error.
    }
    return dir + "/screen_" + ts + ".png";
}

// Returns {tool, args_suffix} for the current session type, or {"", ""}
// if no suitable tool is available.
std::pair<std::string, std::string> PickScreenTool() {
    auto session = pai::sys::SessionType();

    // grim is Wayland-only. Require both conditions — otherwise an X11
    // system with grim installed (e.g. as a dependency of something)
    // would pick it and fail at runtime.
    if (session == "wayland" && pai::sys::CommandExists("grim")) {
        return {"grim", ""};
    }
    if (session == "x11") {
        if (pai::sys::CommandExists("scrot")) return {"scrot", ""};
        if (pai::sys::CommandExists("import")) return {"import", "-window root"};
        if (pai::sys::CommandExists("gnome-screenshot"))
            return {"gnome-screenshot", "-f"};
        if (pai::sys::CommandExists("spectacle"))
            return {"spectacle", "-b -n -o"};
    }
    // Last-resort: grim might still be the only option in a weird
    // session (e.g. nested compositor). Try it rather than fail.
    if (pai::sys::CommandExists("grim")) return {"grim", ""};
    return {"", ""};
}

pai::plugin::ResultMap DispatchScreen(
    const std::string& capability,
    const pai::plugin::ResultMap& params,
    std::string& error) {

    if (capability != "screen.capture") {
        error = "unknown_capability";
        return {};
    }

    // Consent gate. The plugin refuses to capture unless the user has
    // explicitly opted in via PAI_SCREEN_CONSENT=1.
    if (pai::sys::EnvString("PAI_SCREEN_CONSENT", "0") != "1") {
        error = "screen_capture_not_consented";
        return {
            {"ok", "false"},
            {"error", "screen_capture_not_consented"},
            {"hint", "set PAI_SCREEN_CONSENT=1 in the agent's environment"},
        };
    }

    auto [tool, suffix] = PickScreenTool();
    if (tool.empty()) {
        error = "no_screen_tool_available";
        return {
            {"ok", "false"},
            {"error", "no_screen_tool_available"},
            {"hint", "install grim (Wayland) or scrot (X11)"},
        };
    }

    std::string out_path;
    if (params.count("outputPath") && !params.at("outputPath").empty()) {
        // Validate caller-supplied output path against allowed roots.
        // Default roots: PAI_SCREEN_DIR (or HomeDir/Pictures/PAI), HomeDir, /tmp.
        // Override with PAI_SCREEN_OUTPUT_ROOTS (semicolon-separated).
        std::string default_dir = pai::sys::EnvString(
            "PAI_SCREEN_DIR",
            std::string(pai::sys::HomeDir()) + "/Pictures/PAI");
        auto roots = pai::sys::EnvList(
            "PAI_SCREEN_OUTPUT_ROOTS",
            {default_dir, pai::sys::HomeDir(), "/tmp"});

        std::string resolved;
        if (!pai::sys::PathInRoots(params.at("outputPath"), roots, resolved)) {
            error = "outputPath_outside_allowed_roots";
            return {
                {"ok", "false"},
                {"error", "outputPath_outside_allowed_roots"},
                {"outputPath", params.at("outputPath")},
                {"hint",
                 "Set PAI_SCREEN_OUTPUT_ROOTS to allow additional directories."},
            };
        }
        out_path = resolved;
    } else {
        out_path = DefaultOutputPath();
    }

    std::vector<std::string> argv = {tool};
    if (!suffix.empty()) {
        // suffix is a space-separated literal in PickScreenTool — safe
        // to split naively here because it is a compile-time constant
        // string, not caller input.
        std::string s;
        for (char c : suffix) {
            if (c == ' ') { if (!s.empty()) { argv.push_back(s); s.clear(); } }
            else s += c;
        }
        if (!s.empty()) argv.push_back(s);
    }
    argv.push_back(out_path);

    auto result = pai::sys::RunCommand(argv, 10);
    if (result.exit_code != 0) {
        error = "capture_failed";
        return {
            {"ok", "false"},
            {"error", "capture_failed"},
            {"exit_code", std::to_string(result.exit_code)},
            {"stderr", result.stderr_data},
        };
    }

    return {
        {"ok", "true"},
        {"path", out_path},
        {"tool", tool},
        {"session", pai::sys::SessionType()},
    };
}

}  // namespace

PAI_PLUGIN_REGISTER(
    "pai.screen",
    ({"screen.capture"}),
    ({"screen"}),
    &DispatchScreen
)
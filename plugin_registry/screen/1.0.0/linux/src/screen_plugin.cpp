#include "pai_plugin_common.h"
#include "pai_system_helpers.h"

#include <cstdlib>
#include <ctime>

namespace {

std::string DefaultOutputPath() {
    char ts[32];
    std::time_t t = std::time(nullptr);
    std::strftime(ts, sizeof(ts), "%Y%m%d_%H%M%S", std::localtime(&t));
    std::string dir = pai::sys::EnvString(
        "PAI_SCREEN_DIR",
        std::string(pai::sys::HomeDir()) + "/Pictures/PAI");
    std::string mkdir_cmd = "mkdir -p '" + dir + "'";
    std::system(mkdir_cmd.c_str());
    return dir + "/screen_" + ts + ".png";
}

// Returns {tool, args_suffix} for the current session type, or {"", ""}
// if no suitable tool is available.
std::pair<std::string, std::string> PickScreenTool() {
    auto session = pai::sys::SessionType();

    if (session == "wayland" || pai::sys::CommandExists("grim")) {
        if (pai::sys::CommandExists("grim")) {
            return {"grim", ""};
        }
    }
    if (session == "x11") {
        if (pai::sys::CommandExists("scrot")) return {"scrot", ""};
        if (pai::sys::CommandExists("import")) return {"import", "-window root"};
        if (pai::sys::CommandExists("gnome-screenshot"))
            return {"gnome-screenshot", "-f"};
        if (pai::sys::CommandExists("spectacle"))
            return {"spectacle", "-b -n -o"};
    }
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

    // Consent: the plugin's own gate. If the user hasn't opted in to
    // screen capture via PAI_SCREEN_CONSENT=1, refuse. This is the
    // plugin-side enforcement of the confirmation safeguard.
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

    std::string out_path = params.count("outputPath") && !params.at("outputPath").empty()
        ? params.at("outputPath") : DefaultOutputPath();

    std::vector<std::string> argv = {tool};
    if (!suffix.empty()) {
        // suffix is space-separated; split naively — safe because tool
        // args here never contain spaces we care about
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
            {"stderr", result.stdout_data},
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
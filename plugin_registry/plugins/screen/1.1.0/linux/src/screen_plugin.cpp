#include "pai_plugin_common.h"
#include "pai_portal.h"
#include "pai_system_helpers.h"

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
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
        // Fall through: the capture will fail with a clear error.
    }
    return dir + "/screen_" + ts + ".png";
}

// Width/height from a PNG's IHDR; false if the file is not a PNG. A capture tool that "succeeds"
// but leaves an empty or non-image file must not be reported as a screenshot.
bool PngSize(const std::string& path, int& w, int& h) {
    std::ifstream f(path, std::ios::binary);
    unsigned char b[24];
    if (!f.read(reinterpret_cast<char*>(b), sizeof b)) return false;
    static const unsigned char sig[8] = {0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A};
    if (std::memcmp(b, sig, 8) != 0 || std::memcmp(b + 12, "IHDR", 4) != 0) return false;
    w = (b[16] << 24) | (b[17] << 16) | (b[18] << 8) | b[19];
    h = (b[20] << 24) | (b[21] << 16) | (b[22] << 8) | b[23];
    return w > 0 && h > 0;
}

bool CopyFile(const std::string& from, const std::string& to) {
    std::ifstream in(from, std::ios::binary);
    std::ofstream out(to, std::ios::binary | std::ios::trunc);
    if (!in || !out) return false;
    out << in.rdbuf();
    return out.good();
}

// Command-line capture tools: {tool, args_suffix} for this session, or {"", ""}.
std::pair<std::string, std::string> PickScreenTool() {
    auto session = pai::sys::SessionType();

    // grim only works on compositors that implement wlr-screencopy (sway, Hyprland...). GNOME's
    // does not, which is why the portal comes first; grim stays for wlroots desktops.
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
    if (pai::sys::CommandExists("grim")) return {"grim", ""};
    return {"", ""};
}

pai::plugin::ResultMap Failure(std::string& error, const std::string& code,
                               pai::plugin::ResultMap extra = {}) {
    error = code;
    extra["ok"] = "false";
    extra["error"] = code;
    return extra;
}

// Capture through the desktop portal: the sanctioned route on Wayland (GNOME, KDE). Returns true
// and fills `png` on success. `fatal` is set when the user said no (or it timed out): the caller must
// NOT fall back to a tool that sidesteps that answer.
bool CaptureViaPortal(const std::string& out_path, int timeout_sec, std::string& detail, bool& fatal,
                      std::string& fail_code) {
    fatal = false;
    auto shot = pai::portal::Screenshot(timeout_sec);
    if (!shot.ok) {
        detail = shot.detail;
        fail_code = shot.error;
        if (shot.error == "cancelled" || shot.error == "timeout") fatal = true;
        return false;
    }
    if (!CopyFile(shot.path, out_path)) {
        detail = "cannot write " + out_path;
        fail_code = "failed";
        return false;
    }
    // The portal's file exists only because we asked for it: remove it once copied, so every capture
    // does not leave a second copy behind in the user's pictures. (Harmless no-op for sandbox paths.)
    if (shot.path != out_path) std::remove(shot.path.c_str());
    return true;
}

pai::plugin::ResultMap DispatchScreen(
    const std::string& capability,
    const pai::plugin::ResultMap& params,
    std::string& error) {

    if (capability != "screen.capture") {
        error = "unknown_capability";
        return {};
    }

    // Consent gate. The plugin refuses to capture unless the user has explicitly opted in: the app
    // sets PAI_SCREEN_CONSENT=1 when the user allows the "screen" permission.
    if (pai::sys::EnvString("PAI_SCREEN_CONSENT", "0") != "1") {
        return Failure(error, "screen_capture_not_consented",
                       {{"hint", "allow the screen permission for PAI (or set PAI_SCREEN_CONSENT=1)"}});
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
            return Failure(error, "outputPath_outside_allowed_roots",
                           {{"outputPath", params.at("outputPath")},
                            {"hint", "Set PAI_SCREEN_OUTPUT_ROOTS to allow additional directories."}});
        }
        out_path = resolved;
    } else {
        out_path = DefaultOutputPath();
    }

    // PAI_SCREEN_BACKEND: auto (default) | portal | cli
    const std::string backend = pai::sys::EnvString("PAI_SCREEN_BACKEND", "auto");
    if (backend != "auto" && backend != "portal" && backend != "cli") {
        return Failure(error, "invalid_backend", {{"hint", "PAI_SCREEN_BACKEND must be auto, portal or cli"}});
    }

    std::string used_tool, portal_detail, portal_code;
    bool captured = false;

    if (backend != "cli") {
        bool fatal = false;
        // How long to wait for the desktop's permission dialog (seconds); bounded so a bad value cannot hang the host.
        int portal_timeout = std::atoi(pai::sys::EnvString("PAI_SCREEN_PORTAL_TIMEOUT", "60").c_str());
        if (portal_timeout < 1) portal_timeout = 60;
        if (portal_timeout > 300) portal_timeout = 300;
        captured = CaptureViaPortal(out_path, portal_timeout, portal_detail, fatal, portal_code);
        if (captured) used_tool = "portal";
        else if (fatal) {
            return Failure(error, portal_code == "cancelled" ? "capture_denied" : "capture_timeout",
                           {{"detail", portal_detail},
                            {"hint", portal_code == "cancelled" ? "the screenshot was declined in the desktop's permission dialog"
                                                                : "no answer to the desktop's permission dialog"}});
        } else if (backend == "portal") {
            return Failure(error, "capture_failed", {{"tool", "portal"}, {"detail", portal_detail}, {"portal_error", portal_code}});
        }
    }

    if (!captured) {
        auto [tool, suffix] = PickScreenTool();
        if (tool.empty()) {
            return Failure(error, "no_screen_tool_available",
                           {{"hint", "needs the desktop screenshot portal (xdg-desktop-portal), or grim (wlroots) / scrot (X11)"},
                            {"portal_error", portal_code}, {"detail", portal_detail}});
        }
        std::vector<std::string> argv = {tool};
        if (!suffix.empty()) {
            // suffix is a space-separated literal from PickScreenTool, never caller input.
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
            return Failure(error, "capture_failed",
                           {{"tool", tool}, {"exit_code", std::to_string(result.exit_code)},
                            {"stderr", result.stderr_data}, {"portal_error", portal_code}});
        }
        used_tool = tool;
    }

    int w = 0, h = 0;
    if (!PngSize(out_path, w, h)) {
        std::remove(out_path.c_str());
        return Failure(error, "capture_invalid", {{"tool", used_tool}, {"hint", "the capture tool did not produce a PNG"}});
    }
    ::chmod(out_path.c_str(), 0600);                 // a screenshot can hold anything: owner-only

    return {
        {"ok", "true"},
        {"path", out_path},
        {"tool", used_tool},
        {"session", pai::sys::SessionType()},
        {"width", std::to_string(w)},
        {"height", std::to_string(h)},
    };
}

}  // namespace

PAI_PLUGIN_REGISTER(
    "pai.screen",
    ({"screen.capture"}),
    ({"screen"}),
    &DispatchScreen
)

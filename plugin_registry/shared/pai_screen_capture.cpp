#include "pai_screen_capture.h"

#include "pai_portal.h"
#include "pai_system_helpers.h"

#include <sys/stat.h>
#include <unistd.h>

#include <cstdio>
#include <cstring>
#include <fstream>

namespace pai::capture {

namespace {

bool CopyFile(const std::string& from, const std::string& to) {
    std::ifstream in(from, std::ios::binary);
    std::ofstream out(to, std::ios::binary | std::ios::trunc);
    if (!in || !out) return false;
    out << in.rdbuf();
    return out.good();
}

std::string TempPath() {
    std::string dir = pai::sys::EnvString("XDG_RUNTIME_DIR", "/tmp");
    std::string tmpl = dir + "/pai_cc_shot_XXXXXX";
    std::vector<char> buf(tmpl.begin(), tmpl.end());
    buf.push_back('\0');
    int fd = ::mkstemp(buf.data());                 // 0600, unique
    if (fd < 0) return "";
    ::close(fd);
    return std::string(buf.data());
}

// {tool, args} for the session, or {"", {}}. Same order as the screen plugin.
std::pair<std::string, std::vector<std::string>> PickTool() {
    const std::string session = pai::sys::SessionType();
    if (session == "wayland" && pai::sys::CommandExists("grim")) return {"grim", {}};
    if (session == "x11") {
        if (pai::sys::CommandExists("scrot")) return {"scrot", {"-o"}};
        if (pai::sys::CommandExists("import")) return {"import", {"-window", "root"}};
        if (pai::sys::CommandExists("gnome-screenshot")) return {"gnome-screenshot", {"-f"}};
        if (pai::sys::CommandExists("spectacle")) return {"spectacle", {"-b", "-n", "-o"}};
    }
    if (pai::sys::CommandExists("grim")) return {"grim", {}};
    return {"", {}};
}

}  // namespace

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

Shot CaptureScreen(int portal_timeout_sec) {
    Shot s;
    s.png_path = TempPath();
    if (s.png_path.empty()) { s.error = "capture_failed"; s.detail = "cannot create a temp file"; return s; }

    // Portal first. A "no" (or no answer) is final: never sidestep it with a tool.
    std::string portal_code, portal_detail;
    const std::string backend = pai::sys::EnvString("PAI_SCREEN_BACKEND", "auto");
    if (backend != "cli") {
        auto shot = pai::portal::Screenshot(portal_timeout_sec);
        if (shot.ok && CopyFile(shot.path, s.png_path)) {
            if (shot.path != s.png_path) std::remove(shot.path.c_str());
            s.tool = "portal";
        } else if (!shot.ok) {
            portal_code = shot.error;
            portal_detail = shot.detail;
            if (portal_code == "cancelled" || portal_code == "timeout") {
                s.error = portal_code == "cancelled" ? "capture_denied" : "capture_timeout";
                s.detail = portal_detail;
                std::remove(s.png_path.c_str());
                s.png_path.clear();
                return s;
            }
        }
    }
    if (s.tool.empty()) {
        auto [tool, args] = PickTool();
        if (tool.empty()) {
            s.error = "no_screen_tool_available";
            s.detail = "needs the desktop screenshot portal, or grim (wlroots) / scrot (X11); portal: " + portal_code;
            std::remove(s.png_path.c_str());
            s.png_path.clear();
            return s;
        }
        std::vector<std::string> argv = {tool};
        argv.insert(argv.end(), args.begin(), args.end());
        argv.push_back(s.png_path);
        auto r = pai::sys::RunCommand(argv, 10);
        if (r.exit_code != 0) {
            s.error = "capture_failed";
            s.detail = tool + " exited " + std::to_string(r.exit_code) + ": " + r.stderr_data.substr(0, 200);
            Cleanup(s);
            return s;
        }
        s.tool = tool;
    }
    if (!PngSize(s.png_path, s.width, s.height)) {
        s.error = "capture_invalid";
        s.detail = "the capture tool did not produce a PNG";
        Cleanup(s);
        return s;
    }
    ::chmod(s.png_path.c_str(), 0600);
    s.ok = true;
    return s;
}

void Cleanup(Shot& shot) {
    if (!shot.png_path.empty()) std::remove(shot.png_path.c_str());
    shot.png_path.clear();
}

}  // namespace pai::capture

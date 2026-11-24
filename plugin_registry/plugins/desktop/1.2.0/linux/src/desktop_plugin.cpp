#include "pai_plugin_common.h"
#include "pai_system_helpers.h"

#include <algorithm>
#include <cctype>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <dirent.h>
#include <fcntl.h>
#include <fstream>
#include <map>
#include <random>
#include <sstream>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <thread>
#include <set>
#include <string>
#include <unistd.h>
#include <utility>
#include <vector>

namespace {

// Strip leading/trailing whitespace (tool output ends with a newline).
std::string Trim(std::string s) {
    while (!s.empty() && (s.back() == '\n' || s.back() == '\r' || s.back() == ' ')) s.pop_back();
    size_t i = 0;
    while (i < s.size() && s[i] == ' ') ++i;
    return s.substr(i);
}

// Parse a whole-string integer; false on anything else.
bool ParseInt(const std::string& s, long& out) {
    if (s.empty()) return false;
    char* end = nullptr;
    long v = std::strtol(s.c_str(), &end, 10);
    if (end == s.c_str() || *end != '\0') return false;
    out = v;
    return true;
}

// Decode %XX escapes (file:// URLs carry them; a path with a space is "%20").
bool PercentDecode(const std::string& in, std::string& out) {
    out.clear();
    for (size_t i = 0; i < in.size(); ++i) {
        if (in[i] != '%') { out += in[i]; continue; }
        if (i + 2 >= in.size()) return false;
        auto hex = [](char c) -> int {
            if (c >= '0' && c <= '9') return c - '0';
            if (c >= 'a' && c <= 'f') return c - 'a' + 10;
            if (c >= 'A' && c <= 'F') return c - 'A' + 10;
            return -1;
        };
        int h = hex(in[i + 1]), l = hex(in[i + 2]);
        if (h < 0 || l < 0) return false;
        char c = static_cast<char>(h * 16 + l);
        if (c == '\0') return false;
        out += c;
        i += 2;
    }
    return true;
}

// --------------------------------------------------------------------
// Clipboard
// --------------------------------------------------------------------

std::pair<std::string, std::string> ClipboardReadTool() {
    auto session = pai::sys::SessionType();
    if (session == "wayland" && pai::sys::CommandExists("wl-paste"))
        return {"wl-paste", ""};
    if (pai::sys::CommandExists("xclip"))
        return {"xclip", "-selection clipboard -o"};
    if (pai::sys::CommandExists("xsel"))
        return {"xsel", "-b"};
    return {"", ""};
}

std::pair<std::string, std::string> ClipboardWriteTool() {
    auto session = pai::sys::SessionType();
    if (session == "wayland" && pai::sys::CommandExists("wl-copy"))
        return {"wl-copy", ""};
    if (pai::sys::CommandExists("xclip"))
        return {"xclip", "-selection clipboard"};
    if (pai::sys::CommandExists("xsel"))
        return {"xsel", "-b -i"};
    return {"", ""};
}

// Split a space-separated argument suffix into an argv fragment.
// Safe here because suffixes come from our own constant tables.
std::vector<std::string> SplitArgs(const std::string& s) {
    std::vector<std::string> out;
    std::string cur;
    for (char c : s) {
        if (c == ' ') { if (!cur.empty()) { out.push_back(cur); cur.clear(); } }
        else cur += c;
    }
    if (!cur.empty()) out.push_back(cur);
    return out;
}

pai::plugin::ResultMap ClipboardRead(std::string& error) {
    auto [tool, args] = ClipboardReadTool();
    if (tool.empty()) { error = "no_clipboard_tool"; return {}; }

    std::vector<std::string> argv = {tool};
    for (auto& a : SplitArgs(args)) argv.push_back(a);

    auto r = pai::sys::RunCommand(argv, 5);
    // xclip/wl-paste exit non-zero with no output when the clipboard is simply empty
    // (or holds a non-text type): that is "", not a failure. Anything else is a real error.
    if (r.timed_out || (r.exit_code != 0 && !r.stderr_data.empty()
                        && r.stderr_data.find("not available") == std::string::npos
                        && r.stderr_data.find("Nothing is copied") == std::string::npos)) {
        error = "clipboard_read_failed";
        return {
            {"ok", "false"},
            {"error", "clipboard_read_failed"},
            {"exit_code", std::to_string(r.exit_code)},
            {"stderr", r.stderr_data},
        };
    }
    return {
        {"ok", "true"},
        {"content", r.stdout_data},
        {"length", std::to_string(r.stdout_data.size())},
    };
}

pai::plugin::ResultMap ClipboardWrite(
    const pai::plugin::ResultMap& params, std::string& error) {
    if (!params.count("content")) {
        error = "missing_required_param: content";
        return {};
    }
    auto [tool, args] = ClipboardWriteTool();
    if (tool.empty()) { error = "no_clipboard_tool"; return {}; }

    // Stage the payload in a private (0600, unpredictable-name) tempfile: clipboard text can be
    // a password, and a fixed name under world-readable /tmp let other local users read it
    // (or pre-create the path as a symlink). xclip/wl-copy read stdin, so feed the file via
    // RunCommandSilent, which does not capture output (xclip daemonizes and would otherwise
    // hold the capture pipes open). Prefer the per-user runtime dir over /tmp.
    const char* runtime = std::getenv("XDG_RUNTIME_DIR");
    std::string tmpl = std::string(runtime && *runtime && ::access(runtime, W_OK) == 0 ? runtime : "/tmp")
        + "/pai_clip_XXXXXX";
    std::vector<char> buf(tmpl.begin(), tmpl.end());
    buf.push_back('\0');
    int fd = ::mkstemp(buf.data());          // creates with mode 0600, O_EXCL
    if (fd < 0) {
        error = "tempfile_create_failed";
        return {{"ok", "false"}, {"error", "tempfile_create_failed"}};
    }
    const std::string tmp(buf.data());
    {
        const std::string& content = params.at("content");
        size_t off = 0;
        while (off < content.size()) {
            ssize_t n = ::write(fd, content.data() + off, content.size() - off);
            if (n <= 0) { ::close(fd); std::remove(tmp.c_str());
                error = "tempfile_write_failed";
                return {{"ok", "false"}, {"error", "tempfile_write_failed"}}; }
            off += static_cast<size_t>(n);
        }
        ::close(fd);
    }

    std::vector<std::string> argv = {tool};
    for (auto& a : SplitArgs(args)) argv.push_back(a);

    auto r = pai::sys::RunCommandSilent(argv, tmp, 5);
    std::remove(tmp.c_str());

    if (r.exit_code != 0) {
        error = "clipboard_write_failed";
        return {
            {"ok", "false"},
            {"error", "clipboard_write_failed"},
            {"exit_code", std::to_string(r.exit_code)},
        };
    }
    return {
        {"ok", "true"},
        {"bytes_written", std::to_string(params.at("content").size())},
    };
}

// --------------------------------------------------------------------
// Audio — play a file/URL, or speak text via TTS
// --------------------------------------------------------------------

pai::plugin::ResultMap AudioPlay(
    const pai::plugin::ResultMap& params, std::string& error) {
    std::string source;
    if (params.count("url") && !params.at("url").empty()) source = params.at("url");
    else if (params.count("path") && !params.at("path").empty()) source = params.at("path");
    else { error = "missing url or path"; return {}; }

    // The player takes this as a positional argument; a value starting with '-' would be parsed
    // as an OPTION instead (mpv --script=... runs arbitrary code). Never let it through.
    if (source[0] == '-') {
        error = "invalid_source";
        return {{"ok", "false"}, {"error", "invalid_source"}};
    }
    if (source.find("://") == std::string::npos && ::access(source.c_str(), R_OK) != 0) {
        error = "file_not_found";
        return {{"ok", "false"}, {"error", "file_not_found"}, {"source", source}};
    }

    std::pair<std::string, std::string> tool = {"", ""};
    if      (pai::sys::CommandExists("mpv"))    tool = {"mpv", "--no-video --really-quiet"};
    else if (pai::sys::CommandExists("ffplay")) tool = {"ffplay", "-nodisp -autoexit -loglevel quiet"};
    else if (pai::sys::CommandExists("paplay")) tool = {"paplay", ""};
    else if (pai::sys::CommandExists("aplay"))  tool = {"aplay", ""};
    if (tool.first.empty()) { error = "no_audio_tool"; return {}; }

    // argv form — no shell, no quoting, no injection.
    std::vector<std::string> argv = {tool.first};
    for (auto& a : SplitArgs(tool.second)) argv.push_back(a);
    argv.push_back(source);

    if (!pai::sys::SpawnDetached(argv)) {
        error = "spawn_failed";
        return {{"ok", "false"}, {"error", "spawn_failed"}};
    }
    return {
        {"ok", "true"},
        {"tool", tool.first},
        {"source", source},
        {"mode", "async"},
    };
}

pai::plugin::ResultMap AudioSpeak(
    const pai::plugin::ResultMap& params, std::string& error) {
    if (!params.count("text") || params.at("text").empty()) {
        error = "missing_required_param: text";
        return {};
    }
    const std::string text = params.at("text");

    // Linux ARG_MAX is typically 128 KB; cap so execvp doesn't E2BIG.
    if (text.size() > 32 * 1024) {
        error = "text_too_long";
        return {
            {"ok", "false"},
            {"error", "text_too_long"},
            {"size", std::to_string(text.size())},
            {"max", "32768"},
        };
    }

    // spd-say / espeak-ng / espeak all accept text as a positional arg.
    // (spd-say has NO -f flag; espeak-ng does, but positional works for
    // all three so we use it uniformly and skip the tempfile entirely.)
    std::vector<std::string> argv;
    if      (pai::sys::CommandExists("spd-say"))   argv = {"spd-say", text};
    else if (pai::sys::CommandExists("espeak-ng")) argv = {"espeak-ng", text};
    else if (pai::sys::CommandExists("espeak"))    argv = {"espeak", text};
    else { error = "no_tts_tool"; return {}; }

    // Blocking call with timeout — caller learns whether speech finished.
    auto r = pai::sys::RunCommand(argv, 30);
    if (r.exit_code != 0) {
        error = "tts_failed";
        return {
            {"ok", "false"},
            {"error", "tts_failed"},
            {"exit_code", std::to_string(r.exit_code)},
            {"stderr", r.stderr_data},
        };
    }
    return {
        {"ok", "true"},
        {"tool", argv[0]},
        {"length", std::to_string(text.size())},
    };
}

// Get / set / mute the default output volume (PipeWire wpctl, else PulseAudio pactl).
pai::plugin::ResultMap AudioVolume(
    const pai::plugin::ResultMap& params, std::string& error) {
    const std::string action = params.count("action") ? params.at("action") : "get";
    static const std::set<std::string> allowed = {"get", "set", "mute", "unmute"};
    if (!allowed.count(action)) {
        error = "invalid_action";
        return {{"ok", "false"}, {"error", "invalid_action"}, {"allowed", "get,set,mute,unmute"}};
    }

    long level = -1;
    if (action == "set") {
        if (!params.count("level") || !ParseInt(params.at("level"), level)
            || level < 0 || level > 100) {
            error = "invalid_level";
            return {{"ok", "false"}, {"error", "invalid_level"}, {"hint", "level must be 0-100"}};
        }
    }

    const bool wp = pai::sys::CommandExists("wpctl");
    if (!wp && !pai::sys::CommandExists("pactl")) {
        error = "no_audio_control_tool";
        return {{"ok", "false"}, {"error", "no_audio_control_tool"},
                {"hint", "install wireplumber (wpctl) or pulseaudio-utils (pactl)"}};
    }

    std::vector<std::string> argv;
    if (wp) {
        const std::string sink = "@DEFAULT_AUDIO_SINK@";
        if      (action == "get")    argv = {"wpctl", "get-volume", sink};
        else if (action == "set")    argv = {"wpctl", "set-volume", sink, std::to_string(level / 100.0)};
        else if (action == "mute")   argv = {"wpctl", "set-mute", sink, "1"};
        else                         argv = {"wpctl", "set-mute", sink, "0"};
    } else {
        const std::string sink = "@DEFAULT_SINK@";
        if      (action == "get")    argv = {"pactl", "get-sink-volume", sink};
        else if (action == "set")    argv = {"pactl", "set-sink-volume", sink, std::to_string(level) + "%"};
        else if (action == "mute")   argv = {"pactl", "set-sink-mute", sink, "1"};
        else                         argv = {"pactl", "set-sink-mute", sink, "0"};
    }

    auto r = pai::sys::RunCommand(argv, 5);
    if (r.exit_code != 0) {
        error = "volume_control_failed";
        return {{"ok", "false"}, {"error", "volume_control_failed"},
                {"exit_code", std::to_string(r.exit_code)}, {"stderr", r.stderr_data}};
    }
    pai::plugin::ResultMap out = {{"ok", "true"}, {"action", action}, {"tool", argv[0]}};
    if (action == "get") out["volume"] = Trim(r.stdout_data);
    if (action == "set") out["level"] = std::to_string(level);
    return out;
}

// --------------------------------------------------------------------
// Media control via playerctl
// --------------------------------------------------------------------

pai::plugin::ResultMap MediaControl(
    const pai::plugin::ResultMap& params, std::string& error) {
    if (!pai::sys::CommandExists("playerctl")) {
        error = "playerctl_not_installed";
        return {
            {"ok", "false"},
            {"error", "playerctl_not_installed"},
            {"hint", "sudo apt install playerctl"},
        };
    }

    const std::string action =
        params.count("action") ? params.at("action") : "status";

    static const std::set<std::string> allowed = {
        "play", "pause", "play-pause", "stop", "next", "previous", "status",
    };
    if (!allowed.count(action)) {
        error = "invalid_action";
        return {
            {"ok", "false"},
            {"error", "invalid_action"},
            {"allowed", "play,pause,play-pause,stop,next,previous,status"},
        };
    }

    auto r = pai::sys::RunCommand({"playerctl", action}, 5);
    if (action == "status") {
        // playerctl exits 1 with "No players found" when nothing is running.
        if (r.exit_code != 0) {
            error = "no_player";
            return {
                {"ok", "false"},
                {"action", action},
                {"error", "no_player"},
                {"hint", "no media player is running"},
            };
        }
        return {
            {"ok", "true"},
            {"action", action},
            {"stdout", Trim(r.stdout_data)},
        };
    }
    if (r.exit_code != 0) {
        const bool none = r.stderr_data.find("No players") != std::string::npos;
        error = none ? "no_player" : "media_control_failed";
        return {
            {"ok", "false"},
            {"action", action},
            {"error", error},
            {"exit_code", std::to_string(r.exit_code)},
            {"hint", none ? "no media player is running" : r.stderr_data},
        };
    }
    return {
        {"ok", "true"},
        {"action", action},
        {"exit_code", "0"},
    };
}

// --------------------------------------------------------------------
// Notifications
// --------------------------------------------------------------------

pai::plugin::ResultMap NotificationsRead(std::string& error) {
    // dunst keeps a history we can query. GNOME/KDE do not expose one to clients.
    if (pai::sys::CommandExists("dunstctl")) {
        auto r = pai::sys::RunCommand({"dunstctl", "history"}, 5);
        if (r.exit_code == 0) {
            return {
                {"ok", "true"},
                {"tool", "dunstctl"},
                {"format", "json"},
                {"notifications", r.stdout_data},
            };
        }
        error = "notifications_read_failed";
        return {{"ok", "false"}, {"error", "notifications_read_failed"},
                {"stderr", r.stderr_data}};
    }
    error = "not_supported";
    return {
        {"ok", "false"},
        {"error", "not_supported"},
        {"hint", "Reading notifications needs the dunst daemon (dunstctl history); "
                 "GNOME/KDE do not let other programs read them."},
    };
}

// Show a desktop notification.
pai::plugin::ResultMap NotificationsSend(
    const pai::plugin::ResultMap& params, std::string& error) {
    const std::string title = params.count("title") ? params.at("title") : "";
    const std::string message = params.count("message") ? params.at("message") : "";
    if (title.empty() && message.empty()) {
        error = "missing_required_param: title or message";
        return {};
    }
    if (title.size() + message.size() > 4096) {
        error = "text_too_long";
        return {{"ok", "false"}, {"error", "text_too_long"}};
    }
    if (!pai::sys::CommandExists("notify-send")) {
        error = "notify_send_not_installed";
        return {{"ok", "false"}, {"error", "notify_send_not_installed"},
                {"hint", "sudo apt install libnotify-bin"}};
    }
    std::string urgency = params.count("urgency") ? params.at("urgency") : "normal";
    if (urgency != "low" && urgency != "normal" && urgency != "critical") {
        error = "invalid_urgency";
        return {{"ok", "false"}, {"error", "invalid_urgency"},
                {"allowed", "low,normal,critical"}};
    }
    std::vector<std::string> argv = {"notify-send", "-u", urgency, "-a", "PAI"};
    if (params.count("timeout_ms") && !params.at("timeout_ms").empty()) {
        long ms = 0;
        if (!ParseInt(params.at("timeout_ms"), ms) || ms < 0 || ms > 600000) {
            error = "invalid_timeout_ms";
            return {{"ok", "false"}, {"error", "invalid_timeout_ms"}};
        }
        argv.push_back("-t");
        argv.push_back(std::to_string(ms));
    }
    // "--" ends option parsing so a title like "--help" is text, not a flag.
    argv.push_back("--");
    argv.push_back(title.empty() ? message : title);
    if (!title.empty() && !message.empty()) argv.push_back(message);

    auto r = pai::sys::RunCommand(argv, 5);
    if (r.exit_code != 0) {
        error = "notify_failed";
        return {{"ok", "false"}, {"error", "notify_failed"},
                {"exit_code", std::to_string(r.exit_code)}, {"stderr", r.stderr_data}};
    }
    return {{"ok", "true"}, {"tool", "notify-send"}, {"urgency", urgency}};
}

pai::plugin::ResultMap NotificationsDismiss(
    const pai::plugin::ResultMap& params, std::string& error) {
    (void)params;
    if (pai::sys::CommandExists("dunstctl")) {
        auto r = pai::sys::RunCommand({"dunstctl", "close-all"}, 5);
        return {
            {"ok", r.exit_code == 0 ? "true" : "false"},
            {"tool", "dunstctl"},
        };
    }
    error = "no_notification_daemon_detected";
    return {
        {"ok", "false"},
        {"error", "no_notification_daemon_detected"},
        {"hint", "install dunst (or a D-Bus-aware dismisser)"},
    };
}

// --------------------------------------------------------------------
// App launch
// --------------------------------------------------------------------

// Path of "<id>.desktop" in the XDG application dirs, or "" if there is none.
std::string FindDesktopFile(const std::string& id) {
    if (id.find('/') != std::string::npos) return "";
    std::vector<std::string> dirs;
    const std::string data_home = pai::sys::EnvString("XDG_DATA_HOME", pai::sys::HomeDir() + "/.local/share");
    dirs.push_back(data_home + "/applications");
    std::string data_dirs = pai::sys::EnvString("XDG_DATA_DIRS", "/usr/local/share:/usr/share");
    size_t start = 0;
    while (start <= data_dirs.size()) {
        size_t end = data_dirs.find(':', start);
        if (end == std::string::npos) end = data_dirs.size();
        if (end > start) dirs.push_back(data_dirs.substr(start, end - start) + "/applications");
        start = end + 1;
    }
    for (const auto& d : dirs) {
        const std::string path = d + "/" + id + ".desktop";
        if (::access(path.c_str(), R_OK) == 0) return path;
    }
    return "";
}

pai::plugin::ResultMap AppLaunch(
    const pai::plugin::ResultMap& params, std::string& error) {
    if (params.count("url") && !params.at("url").empty()) {
        const std::string url = params.at("url");
        // A local file that does not exist would "succeed" (xdg-open exits 0) yet show nothing.
        if (url[0] == '-') {          // would be parsed as an xdg-open option
            error = "invalid_url";
            return {{"ok", "false"}, {"error", "invalid_url"}};
        }
        const std::string prefix = "file://";
        if (url.compare(0, prefix.size(), prefix) == 0) {
            std::string rest = url.substr(prefix.size());
            const std::string local = "localhost/";
            if (rest.compare(0, local.size(), local) == 0) rest = rest.substr(local.size() - 1);
            std::string path;
            // "%20" etc: the path on disk has a real space, so decode before checking it.
            if (!PercentDecode(rest, path)) {
                error = "invalid_url";
                return {{"ok", "false"}, {"url", url}, {"error", "invalid_url"}};
            }
            if (access(path.c_str(), R_OK) != 0) {
                error = "file_not_found";
                return {{"ok", "false"}, {"url", url}, {"error", "file_not_found"}};
            }
        }
        // Fully detached with stdio on /dev/null. The old pipe-capturing RunCommand left the
        // launched viewer/browser holding pipes we closed on return, so it died on its first
        // write (SIGPIPE) and "opened" nothing while still reporting exit_code 0.
        if (!pai::sys::SpawnDetached({"xdg-open", url})) {
            error = "spawn_failed";
            return {{"ok", "false"}, {"url", url}, {"error", "spawn_failed"}};
        }
        return {{"ok", "true"}, {"url", url}, {"tool", "xdg-open"}, {"closable", "false"}};
    }
    if (params.count("app") && !params.at("app").empty()) {
        const std::string app = params.at("app");
        if (app[0] == '-') {          // would be parsed as an option by gtk-launch / the app
            error = "invalid_app";
            return {{"ok", "false"}, {"error", "invalid_app"}};
        }

        // Prefer gtk-launch (resolves .desktop basenames).
        auto r = pai::sys::RunCommandSilent({"gtk-launch", app}, "", 10);   // no pipes: the app must outlive us
        if (r.exit_code == 0) {
            return {{"ok", "true"}, {"app", app}, {"tool", "gtk-launch"}, {"closable", "false"}};
        }
        // gtk-launch can fail for reasons unrelated to the app (it aborts outright on some
        // GNOME setups with a GSettings schema mismatch). `gio launch` does the same job.
        const std::string desktop_file = FindDesktopFile(app);
        if (!desktop_file.empty() && pai::sys::CommandExists("gio")) {
            auto g = pai::sys::RunCommandSilent({"gio", "launch", desktop_file}, "", 10);
            if (g.exit_code == 0) {
                return {{"ok", "true"}, {"app", app}, {"tool", "gio"}, {"closable", "false"}};
            }
        }
        // Fallback: direct exec via SpawnDetached — argv, no shell, no
        // injection. Double-fork so we don't accumulate zombies. SpawnDetached reports
        // success once the fork happens, even if exec then fails, so confirm the program
        // exists first; otherwise a typo'd app name would come back "ok".
        const bool has_slash = app.find('/') != std::string::npos;
        const bool runnable = has_slash ? ::access(app.c_str(), X_OK) == 0
                                        : pai::sys::CommandExists(app);
        if (!runnable) {
            error = "app_not_found";
            return {{"ok", "false"}, {"app", app}, {"error", "app_not_found"},
                    {"hint", "no installed app or program with that name"}};
        }
        if (!pai::sys::SpawnDetached({app})) {
            error = "spawn_failed";
            return {{"ok", "false"}, {"error", "spawn_failed"}};
        }
        return {{"ok", "true"}, {"app", app}, {"tool", "direct"}, {"closable", "false"}};
    }
    error = "missing app or url";
    return {};
}

// ====================================================================
// app.open / app.close / app.list - apps PAI opened and can close again
// ====================================================================
//
// app.launch is fire-and-forget and can NOT be closed. app.open starts the app as a process
// PAI owns (its own session, so the whole app is signalled as one group) and remembers it, so
// app.close ends exactly that app and never something the user started themselves.
//
// Records live in a private (0600) file under $XDG_RUNTIME_DIR so they survive the plugin being
// reloaded (e.g. on an update). Each record carries the process start time: a recycled PID can
// never be mistaken for the app we opened.

std::string Lower(std::string s) {
    for (auto& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return s;
}

std::string Basename(const std::string& p) {
    auto i = p.rfind('/');
    return i == std::string::npos ? p : p.substr(i + 1);
}

bool Truthy(const std::string& v) {
    const std::string l = Lower(v);
    return l == "true" || l == "1" || l == "yes" || l == "on";
}

std::string JsonEscape(const std::string& s) {
    std::string o;
    for (unsigned char c : s) {
        switch (c) {
            case '"':  o += "\\\""; break;
            case '\\': o += "\\\\"; break;
            case '\n': o += "\\n"; break;
            case '\r': o += "\\r"; break;
            case '\t': o += "\\t"; break;
            default:
                if (c < 0x20) { char b[8]; std::snprintf(b, sizeof b, "\\u%04x", c); o += b; }
                else o += static_cast<char>(c);
        }
    }
    return o;
}

std::string PercentEncodePath(const std::string& in) {
    static const char* hex = "0123456789ABCDEF";
    std::string o;
    for (unsigned char c : in) {
        if (std::isalnum(c) || c == '-' || c == '.' || c == '_' || c == '~' || c == '/') o += static_cast<char>(c);
        else { o += '%'; o += hex[c >> 4]; o += hex[c & 15]; }
    }
    return o;
}

// ---- what to open -----------------------------------------------------

struct Target {
    std::string url;      // what %u/%U get
    std::string path;     // what %f/%F get (local path, or the URL itself when it is not a file)
    bool is_file = false;
};

bool ParseTarget(const std::string& in, Target& t, std::string& err) {
    if (in[0] == '-') { err = "invalid_url"; return false; }       // would read as an option
    const std::string prefix = "file://";
    std::string path;
    if (in.compare(0, prefix.size(), prefix) == 0) {
        std::string rest = in.substr(prefix.size());
        const std::string local = "localhost/";
        if (rest.compare(0, local.size(), local) == 0) rest = rest.substr(local.size() - 1);
        if (!PercentDecode(rest, path) || path.empty() || path[0] != '/') { err = "invalid_url"; return false; }
        t.url = in;
    } else if (in[0] == '/') {
        path = in;
        t.url = prefix + PercentEncodePath(in);
    } else {
        // scheme:... (https://, mailto:, tel:)
        size_t colon = in.find(':');
        if (colon == std::string::npos || colon == 0) { err = "invalid_url"; return false; }
        for (size_t i = 0; i < colon; ++i) {
            char c = in[i];
            if (!(std::isalnum(static_cast<unsigned char>(c)) || c == '+' || c == '-' || c == '.')) {
                err = "invalid_url"; return false;
            }
        }
        t.url = t.path = in;
        t.is_file = false;
        return true;
    }
    if (::access(path.c_str(), R_OK) != 0) { err = "file_not_found"; return false; }
    t.path = path;
    t.is_file = true;
    return true;
}

// ---- .desktop entries --------------------------------------------------

struct DesktopEntry {
    std::string id, name, path;
    std::vector<std::string> exec;     // tokenised Exec=
    bool terminal = false;
};

// Exec= tokenising per the Desktop Entry spec: whitespace separated, "double quotes" group.
std::vector<std::string> SplitExec(const std::string& s) {
    std::vector<std::string> out;
    std::string cur;
    bool in_q = false, have = false;
    for (size_t i = 0; i < s.size(); ++i) {
        char c = s[i];
        if (in_q) {
            if (c == '\\' && i + 1 < s.size()) cur += s[++i];
            else if (c == '"') in_q = false;
            else cur += c;
        } else if (c == '"') { in_q = true; have = true; }
        else if (c == ' ' || c == '\t') {
            if (have || !cur.empty()) { out.push_back(cur); cur.clear(); have = false; }
        } else cur += c;
    }
    if (have || !cur.empty()) out.push_back(cur);
    return out;
}

bool ParseDesktopFile(const std::string& path, const std::string& id, DesktopEntry& e) {
    std::ifstream f(path);
    if (!f) return false;
    std::string line, type, exec, name;
    bool in_entry = false, terminal = false;
    while (std::getline(f, line)) {
        line = Trim(line);
        if (line.empty() || line[0] == '#') continue;
        if (line[0] == '[') { in_entry = (line == "[Desktop Entry]"); continue; }
        if (!in_entry) continue;
        size_t eq = line.find('=');
        if (eq == std::string::npos) continue;
        const std::string key = line.substr(0, eq), val = line.substr(eq + 1);
        if (key == "Name") name = val;
        else if (key == "Exec") exec = val;
        else if (key == "Type") type = val;
        else if (key == "Terminal") terminal = (val == "true");
        else if (key == "Hidden" && val == "true") return false;
    }
    if (type != "Application" || exec.empty()) return false;
    e.id = id;
    e.name = name.empty() ? id : name;
    e.path = path;
    e.exec = SplitExec(exec);
    e.terminal = terminal;
    return !e.exec.empty();
}

std::vector<std::string> AppDirs() {
    std::vector<std::string> dirs;
    dirs.push_back(pai::sys::EnvString("XDG_DATA_HOME", pai::sys::HomeDir() + "/.local/share") + "/applications");
    const std::string data_dirs = pai::sys::EnvString("XDG_DATA_DIRS", "/usr/local/share:/usr/share");
    size_t start = 0;
    while (start <= data_dirs.size()) {
        size_t end = data_dirs.find(':', start);
        if (end == std::string::npos) end = data_dirs.size();
        if (end > start) dirs.push_back(data_dirs.substr(start, end - start) + "/applications");
        start = end + 1;
    }
    return dirs;
}

// All entries, earlier XDG dirs winning for a repeated id.
std::vector<DesktopEntry> LoadEntries() {
    std::vector<DesktopEntry> out;
    std::set<std::string> seen;
    for (const auto& d : AppDirs()) {
        DIR* dir = ::opendir(d.c_str());
        if (!dir) continue;
        std::vector<std::string> names;
        while (dirent* de = ::readdir(dir)) {
            std::string n = de->d_name;
            if (n.size() > 8 && n.compare(n.size() - 8, 8, ".desktop") == 0) names.push_back(n);
        }
        ::closedir(dir);
        std::sort(names.begin(), names.end());
        for (const auto& n : names) {
            const std::string id = n.substr(0, n.size() - 8);
            if (seen.count(id)) continue;
            DesktopEntry e;
            if (ParseDesktopFile(d + "/" + n, id, e)) { out.push_back(e); }
            seen.insert(id);
        }
    }
    return out;
}

// By id ("org.gnome.Calculator"), then display Name, then the id's last segment ("calculator").
bool FindEntry(std::string query, DesktopEntry& out) {
    if (query.size() > 8 && query.compare(query.size() - 8, 8, ".desktop") == 0) query.resize(query.size() - 8);
    const std::string lq = Lower(query);
    const auto all = LoadEntries();
    for (const auto& e : all) if (e.id == query) { out = e; return true; }
    for (const auto& e : all) if (Lower(e.name) == lq) { out = e; return true; }
    for (const auto& e : all) {
        size_t dot = e.id.rfind('.');
        if (dot != std::string::npos && Lower(e.id.substr(dot + 1)) == lq) { out = e; return true; }
    }
    return false;
}

// Exec= with the field codes filled in. A target is always passed on, even to an app whose
// Exec has no %f/%u (app + url means "open this in that app").
std::vector<std::string> BuildArgv(const DesktopEntry& e, const Target* t) {
    std::vector<std::string> out;
    bool used = false;
    for (const auto& tok : e.exec) {
        std::string r;
        bool had_code = false;
        for (size_t i = 0; i < tok.size(); ++i) {
            if (tok[i] != '%' || i + 1 >= tok.size()) { r += tok[i]; continue; }
            switch (tok[++i]) {
                case '%': r += '%'; break;
                case 'f': case 'F': if (t) r += t->path; used = true; had_code = true; break;
                case 'u': case 'U': if (t) r += t->url;  used = true; had_code = true; break;
                case 'c': r += e.name; break;
                case 'k': r += e.path; break;
                default:  had_code = true; break;          // %i %d %D %n %N %v %m: dropped
            }
        }
        if (r.empty() && had_code) continue;               // the token was only a field code
        out.push_back(r);
    }
    if (t && !used) out.push_back(t->url);
    return out;
}

bool ProgramRunnable(const std::string& prog) {
    if (prog.empty()) return false;
    if (prog.find('/') != std::string::npos) return ::access(prog.c_str(), X_OK) == 0;
    return pai::sys::CommandExists(prog);
}

// Default application for a file/URL: xdg-mime first, then `gio mime` (which also follows
// parent types, e.g. text/x-python -> text/plain).
bool ResolveHandler(const Target& t, DesktopEntry& entry, std::string& err) {
    std::string mime;
    if (t.is_file) {
        if (pai::sys::CommandExists("xdg-mime")) {
            auto r = pai::sys::RunCommand({"xdg-mime", "query", "filetype", t.path}, 5);
            if (r.exit_code == 0) mime = Trim(r.stdout_data);
        }
    } else {
        mime = "x-scheme-handler/" + Lower(t.url.substr(0, t.url.find(':')));
    }
    std::string handler;
    if (!mime.empty() && pai::sys::CommandExists("xdg-mime")) {
        auto r = pai::sys::RunCommand({"xdg-mime", "query", "default", mime}, 5);
        if (r.exit_code == 0) handler = Trim(r.stdout_data);
    }
    if (handler.empty() && !mime.empty() && pai::sys::CommandExists("gio")) {
        auto r = pai::sys::RunCommand({"gio", "mime", mime}, 5);
        const std::string marker = ": ";
        size_t eol = r.stdout_data.find('\n');
        std::string first = r.stdout_data.substr(0, eol);
        size_t at = first.rfind(marker);
        if (r.exit_code == 0 && first.compare(0, 19, "Default application") == 0 && at != std::string::npos)
            handler = Trim(first.substr(at + marker.size()));
    }
    if (handler.empty() || !FindEntry(handler, entry)) {
        err = "no_handler";
        return false;
    }
    return true;
}

// ---- starting a process we can track -----------------------------------

struct SpawnResult { bool ok = false; pid_t pid = 0; int err = 0; };

// Double fork so the app is not our child (no zombies to reap inside the host process); the
// grandchild gets its own session -> its pid is also its process-group id, so signalling
// -pid reaches the app and anything it spawned. stdio goes to /dev/null. A CLOEXEC pipe
// reports an exec() failure (it closes silently when exec succeeds).
SpawnResult SpawnTracked(const std::vector<std::string>& argv) {
    SpawnResult res;
    if (argv.empty()) { res.err = EINVAL; return res; }
    std::vector<char*> cargv;
    for (const auto& a : argv) cargv.push_back(const_cast<char*>(a.c_str()));
    cargv.push_back(nullptr);

    int pidp[2], errp[2];
    if (::pipe2(pidp, O_CLOEXEC) != 0) { res.err = errno; return res; }
    if (::pipe2(errp, O_CLOEXEC) != 0) { res.err = errno; ::close(pidp[0]); ::close(pidp[1]); return res; }

    pid_t mid = ::fork();
    if (mid < 0) {
        res.err = errno;
        ::close(pidp[0]); ::close(pidp[1]); ::close(errp[0]); ::close(errp[1]);
        return res;
    }
    if (mid == 0) {                                   // intermediate
        pid_t g = ::fork();
        if (g == 0) {                                 // grandchild: becomes the app
            ::setsid();
            sigset_t none;
            sigemptyset(&none);
            ::sigprocmask(SIG_SETMASK, &none, nullptr);
            ::signal(SIGPIPE, SIG_DFL);
            int dn = ::open("/dev/null", O_RDWR);
            if (dn >= 0) { ::dup2(dn, 0); ::dup2(dn, 1); ::dup2(dn, 2); if (dn > 2) ::close(dn); }
            ::execvp(cargv[0], cargv.data());
            int e = errno;
            ssize_t ignored = ::write(errp[1], &e, sizeof e);
            (void)ignored;
            ::_exit(127);
        }
        ssize_t ignored = ::write(pidp[1], &g, sizeof g);
        (void)ignored;
        ::_exit(0);
    }
    ::close(pidp[1]);
    ::close(errp[1]);
    int status = 0;
    ::waitpid(mid, &status, 0);                        // ECHILD if the host auto-reaps: fine
    pid_t g = 0;
    ssize_t n = ::read(pidp[0], &g, sizeof g);
    int e = 0;
    ssize_t m = ::read(errp[0], &e, sizeof e);         // EOF when exec succeeded
    ::close(pidp[0]);
    ::close(errp[0]);
    if (n != static_cast<ssize_t>(sizeof g) || g <= 1) { res.err = EAGAIN; return res; }
    if (m == static_cast<ssize_t>(sizeof e)) { res.err = e; return res; }
    res.ok = true;
    res.pid = g;
    return res;
}

// ---- process identity ---------------------------------------------------

bool ReadProcStat(pid_t pid, char& state, unsigned long long& start) {
    std::ifstream f("/proc/" + std::to_string(pid) + "/stat");
    if (!f) return false;
    std::string line;
    std::getline(f, line);
    size_t rp = line.rfind(')');                       // comm may contain spaces/parens
    if (rp == std::string::npos || rp + 2 >= line.size()) return false;
    std::istringstream iss(line.substr(rp + 2));
    std::string tok;
    for (int idx = 0; iss >> tok; ++idx) {
        if (idx == 0) state = tok[0];                   // field 3
        if (idx == 19) { start = std::strtoull(tok.c_str(), nullptr, 10); return true; }   // field 22
    }
    return false;
}

bool ProcAlive(pid_t pid, unsigned long long start) {
    char st = 0;
    unsigned long long s = 0;
    if (pid <= 1 || !ReadProcStat(pid, st, s)) return false;
    return s == start && st != 'Z' && st != 'X';
}

// ---- the record of what is open ------------------------------------------

struct OpenRec {
    std::string handle, name, target;
    pid_t pid = 0;
    unsigned long long start = 0;
    long opened_at = 0;
};

std::string StorePath() {
    const char* rt = std::getenv("XDG_RUNTIME_DIR");
    if (rt && *rt && ::access(rt, W_OK) == 0) return std::string(rt) + "/pai_desktop_opens";
    return "/tmp/pai_desktop_opens_" + std::to_string(::getuid());
}

// Serialises read-modify-write across the processes that may load this plugin.
class StoreLock {
public:
    StoreLock() {
        fd_ = ::open((StorePath() + ".lock").c_str(), O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600);
        if (fd_ >= 0) ::flock(fd_, LOCK_EX);
    }
    ~StoreLock() { if (fd_ >= 0) { ::flock(fd_, LOCK_UN); ::close(fd_); } }
    StoreLock(const StoreLock&) = delete;
    StoreLock& operator=(const StoreLock&) = delete;
private:
    int fd_ = -1;
};

std::string OneLine(std::string s) {
    for (auto& c : s) if (c == '\t' || c == '\n' || c == '\r') c = ' ';
    return s;
}

std::vector<OpenRec> LoadOpens() {
    std::vector<OpenRec> out;
    const std::string path = StorePath();
    struct stat st;
    // Only trust a file that is ours and private: a planted record must never name a pid for us.
    if (::lstat(path.c_str(), &st) != 0 || !S_ISREG(st.st_mode) || st.st_uid != ::getuid()
        || (st.st_mode & 077) != 0) return out;
    std::ifstream f(path);
    std::string line;
    while (std::getline(f, line)) {
        std::vector<std::string> p;
        size_t s = 0;
        while (true) {
            size_t t = line.find('\t', s);
            p.push_back(line.substr(s, t == std::string::npos ? std::string::npos : t - s));
            if (t == std::string::npos) break;
            s = t + 1;
        }
        if (p.size() < 6) continue;
        OpenRec r;
        r.handle = p[0];
        r.pid = static_cast<pid_t>(std::strtol(p[1].c_str(), nullptr, 10));
        r.start = std::strtoull(p[2].c_str(), nullptr, 10);
        r.opened_at = std::strtol(p[3].c_str(), nullptr, 10);
        r.name = p[4];
        r.target = p[5];
        if (!r.handle.empty() && r.pid > 1) out.push_back(r);
    }
    return out;
}

bool SaveOpens(const std::vector<OpenRec>& v) {
    const std::string path = StorePath();
    std::string tmpl = path + ".XXXXXX";
    std::vector<char> buf(tmpl.begin(), tmpl.end());
    buf.push_back('\0');
    int fd = ::mkstemp(buf.data());                    // 0600
    if (fd < 0) return false;
    std::string data;
    for (const auto& r : v)
        data += r.handle + "\t" + std::to_string(r.pid) + "\t" + std::to_string(r.start) + "\t"
              + std::to_string(r.opened_at) + "\t" + OneLine(r.name) + "\t" + OneLine(r.target) + "\n";
    size_t off = 0;
    while (off < data.size()) {
        ssize_t n = ::write(fd, data.data() + off, data.size() - off);
        if (n <= 0) { ::close(fd); std::remove(buf.data()); return false; }
        off += static_cast<size_t>(n);
    }
    ::close(fd);
    if (::rename(buf.data(), path.c_str()) != 0) { std::remove(buf.data()); return false; }
    return true;
}

// Drops records whose app is gone; returns the dropped ones.
std::vector<OpenRec> PruneDead(std::vector<OpenRec>& v) {
    std::vector<OpenRec> dead, live;
    for (auto& r : v) (ProcAlive(r.pid, r.start) ? live : dead).push_back(r);
    v.swap(live);
    return dead;
}

std::string NewHandle() {
    std::random_device rd;
    char b[16];
    std::snprintf(b, sizeof b, "open-%08x", static_cast<unsigned>(rd()));
    return b;
}

std::string RecJson(const OpenRec& r) {
    return "{\"handle\":\"" + JsonEscape(r.handle) + "\",\"app\":\"" + JsonEscape(r.name)
         + "\",\"target\":\"" + JsonEscape(r.target) + "\",\"pid\":" + std::to_string(r.pid)
         + ",\"opened_seconds_ago\":" + std::to_string(std::time(nullptr) - r.opened_at) + "}";
}

std::string RecsJson(const std::vector<OpenRec>& v) {
    std::string s = "[";
    for (size_t i = 0; i < v.size(); ++i) s += (i ? "," : "") + RecJson(v[i]);
    return s + "]";
}

// ---- capabilities ----------------------------------------------------------

constexpr size_t kMaxOpenApps = 32;

pai::plugin::ResultMap AppOpen(const pai::plugin::ResultMap& params, std::string& error) {
    const std::string app = params.count("app") ? params.at("app") : "";
    const std::string url = params.count("url") ? params.at("url") : "";
    if (app.empty() && url.empty()) { error = "missing app or url"; return {}; }
    if (!app.empty() && app[0] == '-') {
        error = "invalid_app";
        return {{"ok", "false"}, {"error", "invalid_app"}};
    }

    Target target;
    bool have_target = false;
    if (!url.empty()) {
        std::string e;
        if (!ParseTarget(url, target, e)) {
            error = e;
            return {{"ok", "false"}, {"url", url}, {"error", e}};
        }
        have_target = true;
    }

    DesktopEntry entry;
    if (!app.empty()) {
        if (!FindEntry(app, entry)) {                  // not an installed app: a program name?
            if (!ProgramRunnable(app)) {
                error = "app_not_found";
                return {{"ok", "false"}, {"app", app}, {"error", "app_not_found"},
                        {"hint", "no installed app or program with that name"}};
            }
            entry.name = app;
            entry.exec = {app};
        }
    } else {
        std::string e;
        if (!ResolveHandler(target, entry, e)) {
            error = e;
            return {{"ok", "false"}, {"url", url}, {"error", e},
                    {"hint", "no default application is set for this file or link"}};
        }
    }
    if (entry.terminal) {
        error = "terminal_app_unsupported";
        return {{"ok", "false"}, {"app", entry.name}, {"error", "terminal_app_unsupported"},
                {"hint", "this app runs inside a terminal and cannot be opened here"}};
    }

    const auto argv = BuildArgv(entry, have_target ? &target : nullptr);
    if (argv.empty() || !ProgramRunnable(argv[0])) {
        error = "app_not_found";
        return {{"ok", "false"}, {"app", entry.name}, {"error", "app_not_found"},
                {"hint", "the program this app runs is not installed"}};
    }

    {   // room for one more?
        StoreLock lock;
        auto recs = LoadOpens();
        if (!PruneDead(recs).empty()) SaveOpens(recs);
        if (recs.size() >= kMaxOpenApps) {
            error = "too_many_open_apps";
            return {{"ok", "false"}, {"error", "too_many_open_apps"},
                    {"hint", "close some with app.close first"}};
        }
    }

    const SpawnResult sp = SpawnTracked(argv);
    if (!sp.ok) {
        error = "exec_failed";
        return {{"ok", "false"}, {"app", entry.name}, {"error", "exec_failed"},
                {"detail", std::strerror(sp.err)}};
    }

    char st = 0;
    unsigned long long start = 0;
    bool alive = ReadProcStat(sp.pid, st, start) && st != 'Z' && st != 'X';
    // Settle: a process that exits within ~1.5s either passed the request to an app that was
    // already running (browsers, editors) or failed to start. Either way PAI holds nothing
    // it could close, and must say so rather than hand back a handle that closes nothing.
    for (int i = 0; alive && i < 30; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
        alive = ProcAlive(sp.pid, start);
    }
    if (!alive) {
        return {
            {"ok", "true"},
            {"app", entry.name},
            {"closable", "false"},
            {"status", "handed_off_or_exited"},
            {"hint", "The app exited right away: it either passed this to an already-running "
                     "instance (so PAI cannot close it) or it failed to start."},
        };
    }

    OpenRec rec;
    rec.handle = NewHandle();
    rec.name = entry.name;
    rec.target = have_target ? target.url : "";
    rec.pid = sp.pid;
    rec.start = start;
    rec.opened_at = std::time(nullptr);
    {
        StoreLock lock;
        auto recs = LoadOpens();
        PruneDead(recs);
        recs.push_back(rec);
        SaveOpens(recs);
    }
    return {
        {"ok", "true"},
        {"handle", rec.handle},
        {"app", rec.name},
        {"pid", std::to_string(rec.pid)},
        {"closable", "true"},
    };
}

pai::plugin::ResultMap AppList(std::string& error) {
    StoreLock lock;
    auto recs = LoadOpens();
    if (!PruneDead(recs).empty() && !SaveOpens(recs)) {
        error = "store_write_failed";
        return {{"ok", "false"}, {"error", "store_write_failed"}};
    }
    return {{"ok", "true"}, {"count", std::to_string(recs.size())}, {"apps", RecsJson(recs)}};
}

bool WaitAllGone(const std::vector<OpenRec>& v, int millis) {
    for (int waited = 0;; waited += 100) {
        bool any = false;
        for (const auto& r : v) if (ProcAlive(r.pid, r.start)) { any = true; break; }
        if (!any) return true;
        if (waited >= millis) return false;
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
}

// SIGTERM/SIGKILL the app's whole process group (it is its own session leader), or just the
// pid if that is not the case. Re-checks identity right before signalling.
int SignalApp(const OpenRec& r, int sig) {
    if (r.pid <= 1 || r.pid == ::getpid()) return EINVAL;
    if (sig == SIGTERM && !ProcAlive(r.pid, r.start)) return 0;
    const bool group = ::getpgid(r.pid) == r.pid;
    int rc = group ? ::kill(-r.pid, sig) : ::kill(r.pid, sig);
    return (rc == 0 || errno == ESRCH) ? 0 : errno;
}

pai::plugin::ResultMap AppClose(const pai::plugin::ResultMap& params, std::string& error) {
    const std::string handle = params.count("handle") ? params.at("handle") : "";
    const std::string app = params.count("app") ? params.at("app") : "";
    const bool force = params.count("force") && Truthy(params.at("force"));

    StoreLock lock;
    auto recs = LoadOpens();
    const auto dead = PruneDead(recs);
    if (!dead.empty()) SaveOpens(recs);

    auto matches = [&](const OpenRec& r) {
        if (!handle.empty()) return r.handle == handle;
        return Lower(r.name) == Lower(app);
    };

    std::vector<OpenRec> targets;
    if (!handle.empty() || !app.empty()) {
        for (const auto& r : recs) if (matches(r)) targets.push_back(r);
        if (targets.empty()) {
            for (const auto& r : dead) if (matches(r)) {   // closed since: that is the goal state
                return {{"ok", "true"}, {"status", "already_closed"}, {"app", r.name}};
            }
            error = "not_found";
            return {{"ok", "false"}, {"error", "not_found"},
                    {"hint", "PAI has no open app with that handle or name (it may already have been "
                     "closed); app.list shows what PAI has open"},
                    {"apps", RecsJson(recs)}};
        }
    } else {
        if (recs.empty()) {
            error = "nothing_open";
            return {{"ok", "false"}, {"error", "nothing_open"},
                    {"hint", "PAI has no app open that it can close"}};
        }
        if (recs.size() > 1) {
            error = "ambiguous";
            return {{"ok", "false"}, {"error", "ambiguous"},
                    {"hint", "several apps are open: pass handle or app"}, {"apps", RecsJson(recs)}};
        }
        targets.push_back(recs[0]);
    }

    for (const auto& r : targets) {
        int e = SignalApp(r, SIGTERM);
        if (e != 0) {
            error = "signal_failed";
            return {{"ok", "false"}, {"error", "signal_failed"}, {"app", r.name},
                    {"detail", std::strerror(e)}};
        }
    }
    bool gone = WaitAllGone(targets, 3000);              // graceful: lets the app ask about unsaved work
    bool forced = false;
    if (!gone && force) {
        for (const auto& r : targets) if (ProcAlive(r.pid, r.start)) SignalApp(r, SIGKILL);
        forced = true;
        gone = WaitAllGone(targets, 1500);
    }

    std::string closed, remaining;
    std::vector<OpenRec> keep;
    for (const auto& r : recs) {
        bool is_target = false;
        for (const auto& t : targets) if (t.handle == r.handle) is_target = true;
        if (is_target && !ProcAlive(r.pid, r.start)) { closed += (closed.empty() ? "" : ",") + r.handle; continue; }
        if (is_target) remaining += (remaining.empty() ? "" : ",") + r.handle;
        keep.push_back(r);
    }
    SaveOpens(keep);

    pai::plugin::ResultMap out = {{"closed", closed}};
    if (forced) out["forced"] = "true";
    if (!gone) {
        error = "did_not_close";
        out["ok"] = "false";
        out["error"] = "did_not_close";
        out["still_running"] = remaining;
        out["hint"] = "The app did not exit. Retry with force=true (unsaved work may be lost).";
        return out;
    }
    out["ok"] = "true";
    out["app"] = targets[0].name;
    out["count"] = std::to_string(targets.size());
    return out;
}

// --------------------------------------------------------------------
// Dispatcher
// --------------------------------------------------------------------

pai::plugin::ResultMap DispatchDesktop(
    const std::string& capability,
    const pai::plugin::ResultMap& params,
    std::string& error) {

    if (capability == "clipboard.read")        return ClipboardRead(error);
    if (capability == "clipboard.write")       return ClipboardWrite(params, error);
    if (capability == "audio.play")            return AudioPlay(params, error);
    if (capability == "audio.speak")           return AudioSpeak(params, error);
    if (capability == "media.control")         return MediaControl(params, error);
    if (capability == "notifications.read")    return NotificationsRead(error);
    if (capability == "notifications.dismiss") return NotificationsDismiss(params, error);
    if (capability == "notifications.send")    return NotificationsSend(params, error);
    if (capability == "audio.volume")          return AudioVolume(params, error);
    if (capability == "app.launch")            return AppLaunch(params, error);
    if (capability == "app.open")              return AppOpen(params, error);
    if (capability == "app.close")             return AppClose(params, error);
    if (capability == "app.list")              return AppList(error);

    error = "unknown_capability";
    return {};
}

}  // namespace

PAI_PLUGIN_REGISTER(
    "pai.desktop",
    ({"clipboard.read", "clipboard.write", "audio.play", "audio.speak",
      "media.control", "notifications.read", "notifications.dismiss",
      "notifications.send", "audio.volume", "app.launch", "app.open", "app.close", "app.list"}),
    ({"clipboard", "notifications", "network"}),
    &DispatchDesktop
)
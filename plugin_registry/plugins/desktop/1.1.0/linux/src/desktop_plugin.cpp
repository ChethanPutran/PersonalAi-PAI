#include "pai_plugin_common.h"
#include "pai_system_helpers.h"

#include <cstdio>
#include <cstdlib>
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
        return {{"ok", "true"}, {"url", url}, {"tool", "xdg-open"}};
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
            return {{"ok", "true"}, {"app", app}, {"tool", "gtk-launch"}};
        }
        // gtk-launch can fail for reasons unrelated to the app (it aborts outright on some
        // GNOME setups with a GSettings schema mismatch). `gio launch` does the same job.
        const std::string desktop_file = FindDesktopFile(app);
        if (!desktop_file.empty() && pai::sys::CommandExists("gio")) {
            auto g = pai::sys::RunCommandSilent({"gio", "launch", desktop_file}, "", 10);
            if (g.exit_code == 0) {
                return {{"ok", "true"}, {"app", app}, {"tool", "gio"}};
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
        return {{"ok", "true"}, {"app", app}, {"tool", "direct"}};
    }
    error = "missing app or url";
    return {};
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

    error = "unknown_capability";
    return {};
}

}  // namespace

PAI_PLUGIN_REGISTER(
    "pai.desktop",
    ({"clipboard.read", "clipboard.write", "audio.play", "audio.speak",
      "media.control", "notifications.read", "notifications.dismiss",
      "notifications.send", "audio.volume", "app.launch"}),
    ({"clipboard", "notifications", "network"}),
    &DispatchDesktop
)
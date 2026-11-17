#include "pai_plugin_common.h"
#include "pai_system_helpers.h"

#include <sstream>

namespace {

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

pai::plugin::ResultMap ClipboardRead(std::string& error) {
    auto [tool, args] = ClipboardReadTool();
    if (tool.empty()) { error = "no_clipboard_tool"; return {}; }

    std::vector<std::string> argv = {tool};
    if (!args.empty()) {
        std::string s;
        for (char c : args) {
            if (c == ' ') { if (!s.empty()) { argv.push_back(s); s.clear(); } }
            else s += c;
        }
        if (!s.empty()) argv.push_back(s);
    }

    auto r = pai::sys::RunCommand(argv, 5);
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

    // xclip/wl-copy read from stdin. RunShell with echo | tool is unsafe
    // (metacharacters in `content`). Use a tempfile + redirect inside a
    // controlled shell command.
    const std::string tmp = "/tmp/pai_clip_" + std::to_string(::getpid()) + ".txt";
    {
        std::ofstream f(tmp, std::ios::binary);
        f.write(params.at("content").data(), params.at("content").size());
    }

    std::string cmd = tool;
    if (!args.empty()) cmd += " " + args;
    cmd += " < " + tmp;

    auto r = pai::sys::RunShell(cmd, 5);
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

    // Try in order of preference
    std::pair<std::string, std::string> tool = {"", ""};
    if (pai::sys::CommandExists("mpv"))        tool = {"mpv", "--no-video --really-quiet"};
    else if (pai::sys::CommandExists("ffplay")) tool = {"ffplay", "-nodisp -autoexit -loglevel quiet"};
    else if (pai::sys::CommandExists("paplay")) tool = {"paplay", ""};
    else if (pai::sys::CommandExists("aplay"))  tool = {"aplay", ""};

    if (tool.first.empty()) { error = "no_audio_tool"; return {}; }

    // Fire-and-forget: launch in background, return immediately
    std::string bg = tool.first;
    if (!tool.second.empty()) bg += " " + tool.second;
    bg += " '" + source + "' > /dev/null 2>&1 &";

    std::system(bg.c_str());
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

    std::string tool, args;
    if (pai::sys::CommandExists("spd-say")) { tool = "spd-say"; args = ""; }
    else if (pai::sys::CommandExists("espeak-ng")) { tool = "espeak-ng"; }
    else if (pai::sys::CommandExists("espeak")) { tool = "espeak"; }
    else { error = "no_tts_tool"; return {}; }

    // Write text to tempfile to avoid shell escaping issues
    const std::string tmp = "/tmp/pai_tts_" + std::to_string(::getpid()) + ".txt";
    {
        std::ofstream f(tmp);
        f << text;
    }
    std::string cmd = tool + " -f " + tmp + " &";
    std::system(cmd.c_str());
    std::remove(tmp.c_str());

    return {
        {"ok", "true"},
        {"tool", tool},
        {"length", std::to_string(text.size())},
    };
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

    const std::string action = params.count("action") ? params.at("action") : "status";
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
        return {
            {"ok", "true"},
            {"action", action},
            {"stdout", r.stdout_data},
        };
    }
    return {
        {"ok", r.exit_code == 0 ? "true" : "false"},
        {"action", action},
        {"exit_code", std::to_string(r.exit_code)},
    };
}

// --------------------------------------------------------------------
// Notifications (D-Bus via gdbus)
// --------------------------------------------------------------------

pai::plugin::ResultMap NotificationsRead(std::string& error) {
    if (!pai::sys::CommandExists("gdbus")) {
        error = "gdbus_not_installed";
        return {};
    }
    // Reading notifications requires the Notifications D-Bus service to
    // expose GetNotifications — not standard on all DEs. Return an empty
    // list for now with a clear hint.
    return {
        {"ok", "true"},
        {"notifications", ""},
        {"hint",
         "Notifications-read is DE-specific (GNOME has it, KDE/XFCE vary). "
         "The full implementation requires a persistent D-Bus client that "
         "subscribes to Notify calls — see FOLLOW_UP.md in this plugin."},
    };
}

pai::plugin::ResultMap NotificationsDismiss(
    const pai::plugin::ResultMap& params, std::string& error) {
    // GNOME: gdbus call org.gnome.Shell Eval "…"  (needs special perm)
    // Simplest cross-DE: dunstctl close-all (if dunst is the daemon)
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

pai::plugin::ResultMap AppLaunch(
    const pai::plugin::ResultMap& params, std::string& error) {
    if (params.count("url") && !params.at("url").empty()) {
        auto r = pai::sys::RunCommand({"xdg-open", params.at("url")}, 10);
        return {
            {"ok", r.exit_code == 0 ? "true" : "false"},
            {"url", params.at("url")},
            {"exit_code", std::to_string(r.exit_code)},
        };
    }
    if (params.count("app") && !params.at("app").empty()) {
        const std::string app = params.at("app");
        // Try `gtk-launch` first (desktop file basename without .desktop)
        // Fall back to `gio launch` with full path, or just direct exec.
        auto r = pai::sys::RunCommand({"gtk-launch", app}, 10);
        if (r.exit_code == 0) {
            return {{"ok", "true"}, {"app", app}, {"tool", "gtk-launch"}};
        }
        // Fall back to direct exec (app is on PATH)
        std::string bg = app + " > /dev/null 2>&1 &";
        std::system(bg.c_str());
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
    if (capability == "app.launch")            return AppLaunch(params, error);

    error = "unknown_capability";
    return {};
}

}  // namespace

PAI_PLUGIN_REGISTER(
    "pai.desktop",
    ({"clipboard.read", "clipboard.write", "audio.play", "audio.speak",
      "media.control", "notifications.read", "notifications.dismiss",
      "app.launch"}),
    ({"clipboard", "notifications", "network"}),
    &DispatchDesktop
)
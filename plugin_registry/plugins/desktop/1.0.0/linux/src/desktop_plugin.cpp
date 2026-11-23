#include "pai_plugin_common.h"
#include "pai_system_helpers.h"

#include <atomic>
#include <cstdio>
#include <fstream>
#include <set>
#include <string>
#include <unistd.h>
#include <utility>
#include <vector>

namespace {

// Sequence counter so concurrent clipboard.write calls never collide
// on the tempfile name (getpid() alone isn't enough in a plugin host).
std::atomic<uint64_t> g_clip_seq{0};

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

    // Write payload to a unique tempfile. xclip/wl-copy read from stdin,
    // so feed them via RunCommandSilent — which does not capture output
    // (xclip daemonizes and would otherwise hold the capture pipes open).
    const std::string tmp = "/tmp/pai_clip_"
        + std::to_string(::getpid()) + "_"
        + std::to_string(g_clip_seq.fetch_add(1)) + ".txt";

    {
        std::ofstream f(tmp, std::ios::binary);
        if (!f) {
            error = "tempfile_create_failed";
            return {{"ok", "false"}, {"error", "tempfile_create_failed"}};
        }
        f.write(params.at("content").data(),
                static_cast<std::streamsize>(params.at("content").size()));
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
// Notifications
// --------------------------------------------------------------------

pai::plugin::ResultMap NotificationsRead(std::string& error) {
    if (!pai::sys::CommandExists("gdbus")) {
        error = "gdbus_not_installed";
        return {
            {"ok", "false"},
            {"error", "gdbus_not_installed"},
            {"hint", "install gdbus (package: libglib2.0-bin)"},
        };
    }
    error = "not_implemented";
    return {
        {"ok", "false"},
        {"error", "not_implemented"},
        {"hint",
         "Notifications-read is DE-specific (GNOME has it, KDE/XFCE vary). "
         "Full implementation needs a persistent D-Bus client that "
         "subscribes to Notify calls — see FOLLOW_UP.md."},
    };
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

        // Prefer gtk-launch (resolves .desktop basenames).
        auto r = pai::sys::RunCommand({"gtk-launch", app}, 10);
        if (r.exit_code == 0) {
            return {{"ok", "true"}, {"app", app}, {"tool", "gtk-launch"}};
        }
        // Fallback: direct exec via SpawnDetached — argv, no shell, no
        // injection. Double-fork so we don't accumulate zombies.
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
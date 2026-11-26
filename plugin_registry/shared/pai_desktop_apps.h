#pragma once
// Installed applications from the freedesktop .desktop entries (XDG_DATA_HOME / XDG_DATA_DIRS).
//
// Extracted from the desktop plugin (desktop 1.2.0 keeps its own copy: a released version is immutable) so
// other plugins (computer_control) list and launch applications by the same rules.
#include <string>
#include <vector>

namespace pai::apps {

struct Entry {
    std::string id;                 // "org.gnome.Calculator" (file name without .desktop)
    std::string name;               // display name
    std::string path;               // the .desktop file
    std::vector<std::string> exec;  // tokenised Exec=
    bool terminal = false;
    bool no_display = false;        // NoDisplay=true: helpers and URL handlers, not shown in menus
};

/// Exec= tokenising per the Desktop Entry spec: whitespace separated, "double quotes" group.
std::vector<std::string> SplitExec(const std::string& s);

/// Parse one .desktop file. False when it is not a visible Application with an Exec.
bool ParseDesktopFile(const std::string& path, const std::string& id, Entry& e);

/// XDG application directories, user's first.
std::vector<std::string> AppDirs();

/// Every entry, earlier XDG dirs winning for a repeated id. Entries with NoDisplay are included
/// (flagged) so callers can choose.
std::vector<Entry> LoadEntries();

/// Case-insensitive match of `query` on id or display name (substring). Empty query = all.
std::vector<Entry> Filter(const std::vector<Entry>& all, const std::string& query, bool include_hidden = false);

/// By id ("org.gnome.Calculator"), then display name, then the id's last segment ("calculator").
bool FindEntry(const std::vector<Entry>& all, std::string query, Entry& out);

/// Exec= with the field codes (%f %u ...) dropped, ready for a direct exec (no shell).
std::vector<std::string> LaunchArgv(const Entry& e);

}  // namespace pai::apps

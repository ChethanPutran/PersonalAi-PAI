#include "pai_desktop_apps.h"

#include "pai_system_helpers.h"

#include <dirent.h>

#include <algorithm>
#include <fstream>
#include <set>

namespace pai::apps {

namespace {
std::string Trim(std::string s) {
    const char* ws = " \t\r\n";
    s.erase(0, s.find_first_not_of(ws));
    s.erase(s.find_last_not_of(ws) + 1);
    return s;
}
std::string Lower(std::string s) {
    for (auto& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return s;
}
}  // namespace

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

bool ParseDesktopFile(const std::string& path, const std::string& id, Entry& e) {
    std::ifstream f(path);
    if (!f) return false;
    std::string line, type, exec, name;
    bool in_entry = false, terminal = false, no_display = false;
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
        else if (key == "NoDisplay") no_display = (val == "true");
        else if (key == "Hidden" && val == "true") return false;
    }
    if (type != "Application" || exec.empty()) return false;
    e.id = id;
    e.name = name.empty() ? id : name;
    e.path = path;
    e.exec = SplitExec(exec);
    e.terminal = terminal;
    e.no_display = no_display;
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

std::vector<Entry> LoadEntries() {
    std::vector<Entry> out;
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
            Entry e;
            if (ParseDesktopFile(d + "/" + n, id, e)) out.push_back(e);
            seen.insert(id);
        }
    }
    return out;
}

std::vector<Entry> Filter(const std::vector<Entry>& all, const std::string& query, bool include_hidden) {
    const std::string q = Lower(query);
    std::vector<Entry> out;
    for (const auto& e : all) {
        if (e.no_display && !include_hidden) continue;
        if (q.empty() || Lower(e.id).find(q) != std::string::npos || Lower(e.name).find(q) != std::string::npos)
            out.push_back(e);
    }
    std::sort(out.begin(), out.end(), [](const Entry& a, const Entry& b) { return Lower(a.name) < Lower(b.name); });
    return out;
}

bool FindEntry(const std::vector<Entry>& all, std::string query, Entry& out) {
    if (query.size() > 8 && query.compare(query.size() - 8, 8, ".desktop") == 0) query.resize(query.size() - 8);
    const std::string lq = Lower(query);
    for (const auto& e : all) if (e.id == query) { out = e; return true; }
    for (const auto& e : all) if (Lower(e.name) == lq) { out = e; return true; }
    for (const auto& e : all) {
        size_t dot = e.id.rfind('.');
        if (dot != std::string::npos && Lower(e.id.substr(dot + 1)) == lq) { out = e; return true; }
    }
    return false;
}

std::vector<std::string> LaunchArgv(const Entry& e) {
    std::vector<std::string> out;
    for (const auto& tok : e.exec) {
        std::string r;
        bool had_code = false;
        for (size_t i = 0; i < tok.size(); ++i) {
            if (tok[i] != '%' || i + 1 >= tok.size()) { r += tok[i]; continue; }
            switch (tok[++i]) {
                case '%': r += '%'; break;
                case 'c': r += e.name; break;
                case 'k': r += e.path; break;
                default:  had_code = true; break;          // %f %F %u %U %i %d %D %n %N %v %m: dropped
            }
        }
        if (r.empty() && had_code) continue;               // the token was only a field code
        out.push_back(r);
    }
    return out;
}

}  // namespace pai::apps

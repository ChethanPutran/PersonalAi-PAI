#include "cc_atspi.h"

#include "pai_desktop_apps.h"

#include <atspi/atspi.h>
#include <gio/gio.h>
#include <unistd.h>

#include <algorithm>
#include <fstream>
#include <memory>
#include <mutex>
#include <set>

namespace pai::cc::atspi {

BusStatus Status();

namespace {

constexpr int kMaxChildrenPerNode = 300;      // a list with thousands of rows must not stall an observation
constexpr int kMaxTextChars = 300;

std::mutex g_init_mu;
bool g_inited = false;

// libatspi ABORTS THE WHOLE PROCESS (dbind-ERROR) when it cannot reach the accessibility bus, which inside the
// Flutter host would take the app down. So the bus is asked first, over a plain GDBus call that fails softly, and
// libatspi is only initialised (once) when it is reachable. Re-checked on every call: the bus can go away.
bool EnsureInit() {
    if (!Status().bus) return false;
    std::lock_guard<std::mutex> lock(g_init_mu);
    if (!g_inited) {
        // atspi_init() returns >0 when it was already initialised, 0 on success, <0 on failure.
        if (atspi_init() < 0) return false;
        atspi_set_timeout(1500, 15000);      // a hung app must not hang the observation
        g_inited = true;
    }
    return true;
}

std::string Str(gchar* s) {
    std::string out = s ? s : "";
    g_free(s);
    return out;
}

void ClearError(GError** e) {
    if (e && *e) { g_error_free(*e); *e = nullptr; }
}

// RAII for AtspiAccessible* we own a reference to.
struct Ref {
    AtspiAccessible* p = nullptr;
    Ref() = default;
    explicit Ref(AtspiAccessible* a) : p(a) {}
    Ref(const Ref&) = delete;
    Ref& operator=(const Ref&) = delete;
    Ref(Ref&& o) noexcept : p(o.p) { o.p = nullptr; }
    Ref& operator=(Ref&& o) noexcept { if (this != &o) { reset(); p = o.p; o.p = nullptr; } return *this; }
    ~Ref() { reset(); }
    void reset() { if (p) { g_object_unref(p); p = nullptr; } }
    AtspiAccessible* release() { auto* r = p; p = nullptr; return r; }
};

AtspiAccessible* A(Handle h) { return reinterpret_cast<AtspiAccessible*>(h); }

bool HasState(AtspiStateSet* set, AtspiStateType t) { return set && atspi_state_set_contains(set, t); }

bool ClickishAction(const std::string& n) {
    static const std::set<std::string> names = {"click", "press", "activate", "jump", "toggle", "check", "uncheck",
                                                "select", "open", "invoke", "default", "push", "doubleclick"};
    std::string l = n;
    for (auto& c : l) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return names.count(l) > 0;
}

// Name of the first click-like action, or "" ; index in `idx`.
std::string FindClickAction(AtspiAccessible* a, int& idx) {
    GError* err = nullptr;
    AtspiAction* act = atspi_accessible_get_action_iface(a);
    if (!act) return "";
    std::string found;
    gint n = atspi_action_get_n_actions(act, &err);
    ClearError(&err);
    for (gint i = 0; i < n && i < 8 && found.empty(); ++i) {
        std::string name = Str(atspi_action_get_action_name(act, i, &err));
        ClearError(&err);
        if (ClickishAction(name)) { found = name; idx = i; }
    }
    g_object_unref(act);
    return found;
}

std::string ReadExeName(long pid) {
    if (pid <= 0) return "";
    char buf[512];
    ssize_t n = ::readlink(("/proc/" + std::to_string(pid) + "/exe").c_str(), buf, sizeof buf - 1);
    if (n <= 0) return "";
    buf[n] = '\0';
    std::string p = buf;
    size_t slash = p.rfind('/');
    return slash == std::string::npos ? p : p.substr(slash + 1);
}

// desktop-file id for an app: a .desktop whose Name matches the accessible name, or whose Exec program is the executable.
std::string DesktopIdFor(const std::string& app_name, const std::string& exe) {
    static std::vector<pai::apps::Entry> entries;       // read once per process
    static std::once_flag once;
    std::call_once(once, [] { entries = pai::apps::LoadEntries(); });
    auto lower = [](std::string s) { for (auto& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c))); return s; };
    const std::string ln = lower(app_name), le = lower(exe);
    for (const auto& e : entries) {
        if (!ln.empty() && lower(e.name) == ln) return e.id;
        if (!le.empty() && !e.exec.empty()) {
            std::string prog = e.exec[0];
            size_t slash = prog.rfind('/');
            if (slash != std::string::npos) prog = prog.substr(slash + 1);
            if (lower(prog) == le) return e.id;
        }
    }
    return "";
}

AppInfo DescribeApp(AtspiAccessible* app, AtspiAccessible* window) {
    GError* err = nullptr;
    AppInfo info;
    info.name = Str(atspi_accessible_get_name(app, &err));
    ClearError(&err);
    if (window) { info.title = Str(atspi_accessible_get_name(window, &err)); ClearError(&err); }
    info.pid = static_cast<long>(atspi_accessible_get_process_id(app, &err));
    ClearError(&err);
    const std::string exe = ReadExeName(info.pid);
    info.id = DesktopIdFor(info.name, exe);
    if (info.id.empty()) info.id = exe.empty() ? info.name : exe;
    return info;
}

bool IsWindowRole(AtspiRole r) {
    return r == ATSPI_ROLE_FRAME || r == ATSPI_ROLE_WINDOW || r == ATSPI_ROLE_DIALOG || r == ATSPI_ROLE_ALERT ||
           r == ATSPI_ROLE_FILE_CHOOSER || r == ATSPI_ROLE_COLOR_CHOOSER || r == ATSPI_ROLE_FONT_CHOOSER;
}

// The AT-SPI implementation of NodeSource. Every accessible it hands out is a new reference it tracks.
class Source : public NodeSource {
public:
    explicit Source(Handle root) : root_(root) {}
    ~Source() override {
        for (AtspiAccessible* a : refs_) g_object_unref(a);       // every reference we acquired, duplicates included
    }

    bool Info(Handle h, NodeInfo& out) override {
        AtspiAccessible* a = A(h);
        GError* err = nullptr;
        AtspiRole role = atspi_accessible_get_role(a, &err);
        if (err) { ClearError(&err); return false; }                         // the element is gone
        out.role = Str(atspi_role_get_name(role));
        out.name = Str(atspi_accessible_get_name(a, &err));
        ClearError(&err);

        AtspiStateSet* ss = atspi_accessible_get_state_set(a);
        const bool is_root = (h == root_);
        out.showing = is_root || HasState(ss, ATSPI_STATE_SHOWING);
        out.enabled = HasState(ss, ATSPI_STATE_ENABLED) || HasState(ss, ATSPI_STATE_SENSITIVE);
        out.focused = HasState(ss, ATSPI_STATE_FOCUSED);
        out.editable = HasState(ss, ATSPI_STATE_EDITABLE);
        const bool checkable_role = role == ATSPI_ROLE_CHECK_BOX || role == ATSPI_ROLE_TOGGLE_BUTTON ||
                                    role == ATSPI_ROLE_RADIO_BUTTON || role == ATSPI_ROLE_CHECK_MENU_ITEM ||
                                    role == ATSPI_ROLE_RADIO_MENU_ITEM || HasState(ss, ATSPI_STATE_CHECKABLE);
        if (checkable_role) out.checked = (HasState(ss, ATSPI_STATE_CHECKED) || HasState(ss, ATSPI_STATE_PRESSED)) ? 1 : 0;
        if (ss) g_object_unref(ss);

        out.scrollable = role == ATSPI_ROLE_SCROLL_PANE || role == ATSPI_ROLE_VIEWPORT;

        if (AtspiComponent* comp = atspi_accessible_get_component_iface(a)) {
            if (AtspiRect* r = atspi_component_get_extents(comp, ATSPI_COORD_TYPE_SCREEN, &err)) {
                out.bounds = {r->x, r->y, r->width, r->height};
                g_free(r);
            }
            ClearError(&err);
            g_object_unref(comp);
        }

        int idx = 0;
        out.clickable = !FindClickAction(a, idx).empty();

        // Text content, except for password fields (never read what the user typed there).
        if (role != ATSPI_ROLE_PASSWORD_TEXT) {
            if (AtspiText* t = atspi_accessible_get_text_iface(a)) {
                gint count = atspi_text_get_character_count(t, &err);
                ClearError(&err);
                if (count > 0) {
                    out.text = Str(atspi_text_get_text(t, 0, std::min<gint>(count, kMaxTextChars), &err));
                    ClearError(&err);
                }
                g_object_unref(t);
            }
        }
        return true;
    }

    std::vector<Handle> Children(Handle h) override {
        std::vector<Handle> out;
        AtspiAccessible* a = A(h);
        GError* err = nullptr;
        gint n = atspi_accessible_get_child_count(a, &err);
        if (err) { ClearError(&err); return out; }
        n = std::min<gint>(n, kMaxChildrenPerNode);
        for (gint i = 0; i < n; ++i) {
            AtspiAccessible* c = atspi_accessible_get_child_at_index(a, i, &err);
            ClearError(&err);
            if (!c) continue;
            refs_.push_back(c);
            members_.insert(c);
            out.push_back(reinterpret_cast<Handle>(c));
        }
        return out;
    }

    // Take a new reference to every handle that stays in the observation; the rest are released by ~Source.
    void Keep(const std::map<std::string, Handle>& handles) {
        for (const auto& [id, h] : handles) {
            AtspiAccessible* a = A(h);
            if (members_.count(a)) g_object_ref(a);
        }
    }

private:
    Handle root_;
    std::vector<AtspiAccessible*> refs_;          // one entry per reference acquired
    std::set<AtspiAccessible*> members_;          // the distinct objects (for Keep)
};

}  // namespace

// ------------------------------------------------------------------------------- bus status

BusStatus Status() {
    BusStatus st;
    GError* err = nullptr;
    GDBusConnection* conn = g_bus_get_sync(G_BUS_TYPE_SESSION, nullptr, &err);
    if (!conn) { ClearError(&err); return st; }
    GVariant* r = g_dbus_connection_call_sync(conn, "org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress", nullptr,
                                              G_VARIANT_TYPE("(s)"), G_DBUS_CALL_FLAGS_NONE, 1500, nullptr, &err);
    if (r) {
        st.bus = true;
        g_variant_unref(r);
    } else {
        ClearError(&err);
    }
    if (st.bus) {
        GVariant* p = g_dbus_connection_call_sync(conn, "org.a11y.Bus", "/org/a11y/bus", "org.freedesktop.DBus.Properties", "Get",
                                                  g_variant_new("(ss)", "org.a11y.Status", "IsEnabled"), G_VARIANT_TYPE("(v)"),
                                                  G_DBUS_CALL_FLAGS_NONE, 1500, nullptr, &err);
        if (p) {
            GVariant* inner = nullptr;
            g_variant_get(p, "(v)", &inner);
            if (inner) { st.enabled = g_variant_get_boolean(inner); g_variant_unref(inner); }
            g_variant_unref(p);
        } else {
            ClearError(&err);
        }
    }
    g_object_unref(conn);
    return st;
}

// ------------------------------------------------------------------------ window discovery

namespace {

// First window (new reference) of `app` satisfying `pred`.
template <typename Pred>
AtspiAccessible* FindWindow(AtspiAccessible* app, Pred pred) {
    GError* err = nullptr;
    gint n = atspi_accessible_get_child_count(app, &err);
    ClearError(&err);
    for (gint i = 0; i < n && i < 64; ++i) {
        AtspiAccessible* w = atspi_accessible_get_child_at_index(app, i, &err);
        ClearError(&err);
        if (!w) continue;
        AtspiRole role = atspi_accessible_get_role(w, &err);
        bool ok = false;
        if (!err && IsWindowRole(role)) {
            AtspiStateSet* ss = atspi_accessible_get_state_set(w);
            ok = pred(ss);
            if (ss) g_object_unref(ss);
        }
        ClearError(&err);
        if (ok) return w;
        g_object_unref(w);
    }
    return nullptr;
}

struct Found {
    Ref app, window;
};

Found FindActive(long fallback_pid) {
    Found f;
    GError* err = nullptr;
    Ref desktop(atspi_get_desktop(0));
    if (!desktop.p) return f;
    gint apps = atspi_accessible_get_child_count(desktop.p, &err);
    ClearError(&err);
    Ref showing_app, showing_window;
    for (gint i = 0; i < apps && i < 128; ++i) {
        Ref app(atspi_accessible_get_child_at_index(desktop.p, i, &err));
        ClearError(&err);
        if (!app.p) continue;
        if (AtspiAccessible* w = FindWindow(app.p, [](AtspiStateSet* s) { return HasState(s, ATSPI_STATE_ACTIVE); })) {
            f.window = Ref(w);
            f.app = std::move(app);
            return f;
        }
        if (fallback_pid > 0) {
            long pid = static_cast<long>(atspi_accessible_get_process_id(app.p, &err));
            ClearError(&err);
            if (pid == fallback_pid) {
                if (AtspiAccessible* w = FindWindow(app.p, [](AtspiStateSet* s) { return HasState(s, ATSPI_STATE_SHOWING); })) {
                    f.window = Ref(w);
                    f.app = std::move(app);
                    return f;
                }
            }
        }
        if (!showing_window.p) {
            if (AtspiAccessible* w = FindWindow(app.p, [](AtspiStateSet* s) { return HasState(s, ATSPI_STATE_SHOWING); })) {
                showing_window = Ref(w);
                showing_app = std::move(app);
            }
        }
    }
    f.app = std::move(showing_app);                    // nothing reports ACTIVE: the first app with a visible window
    f.window = std::move(showing_window);
    return f;
}

}  // namespace

Observation ObserveActiveWindow(const Limits& limits, long fallback_pid) {
    Observation obs;
    if (!EnsureInit()) { obs.error = "a11y_unavailable"; obs.detail = "atspi_init failed"; return obs; }
    Found f = FindActive(fallback_pid);
    if (!f.window.p) {
        const BusStatus st = Status();
        obs.error = st.bus ? "no_window" : "a11y_unavailable";
        obs.detail = st.bus ? "no accessible window is open (or its app does not expose accessibility)"
                            : "the accessibility bus is not running";
        return obs;
    }
    obs.app = DescribeApp(f.app.p, f.window.p);
    Source source(reinterpret_cast<Handle>(f.window.p));
    obs.tree = BuildTree(source, reinterpret_cast<Handle>(f.window.p), limits);
    source.Keep(obs.tree.handles);
    // The window itself is not owned by `source` (we hold it in `f`): give the tree its own reference.
    for (auto& [id, h] : obs.tree.handles) {
        if (h == reinterpret_cast<Handle>(f.window.p)) g_object_ref(f.window.p);
    }
    obs.ok = true;
    return obs;
}

bool Foreground(AppInfo& out, long fallback_pid, std::string& error) {
    if (!EnsureInit()) { error = "a11y_unavailable"; return false; }
    Found f = FindActive(fallback_pid);
    if (!f.app.p) { error = Status().bus ? "no_window" : "a11y_unavailable"; return false; }
    out = DescribeApp(f.app.p, f.window.p);
    return true;
}

void Release(Handle h) {
    if (h) g_object_unref(A(h));
}

// ------------------------------------------------------------------------------------ actions

bool IsAlive(Handle h) {
    GError* err = nullptr;
    atspi_accessible_get_role(A(h), &err);
    const bool dead = err != nullptr;
    ClearError(&err);
    return !dead;
}

bool GetBounds(Handle h, Bounds& out) {
    AtspiComponent* comp = atspi_accessible_get_component_iface(A(h));
    if (!comp) return false;
    GError* err = nullptr;
    AtspiRect* r = atspi_component_get_extents(comp, ATSPI_COORD_TYPE_SCREEN, &err);
    ClearError(&err);
    g_object_unref(comp);
    if (!r) return false;
    out = {r->x, r->y, r->width, r->height};
    g_free(r);
    return true;
}

bool DoClickAction(Handle h, std::string& which, std::string& error) {
    int idx = 0;
    which = FindClickAction(A(h), idx);
    if (which.empty()) { error = "not_supported"; return false; }
    AtspiAction* act = atspi_accessible_get_action_iface(A(h));
    if (!act) { error = "not_supported"; return false; }
    GError* err = nullptr;
    const gboolean ok = atspi_action_do_action(act, idx, &err);
    ClearError(&err);
    g_object_unref(act);
    if (!ok) error = "failed";
    return ok;
}

bool Focus(Handle h, std::string& error) {
    AtspiComponent* comp = atspi_accessible_get_component_iface(A(h));
    if (!comp) { error = "not_supported"; return false; }
    GError* err = nullptr;
    const gboolean ok = atspi_component_grab_focus(comp, &err);
    ClearError(&err);
    g_object_unref(comp);
    if (!ok) error = "failed";
    return ok;
}

bool IsEditable(Handle h) {
    AtspiEditableText* et = atspi_accessible_get_editable_text_iface(A(h));
    if (!et) return false;
    g_object_unref(et);
    AtspiStateSet* ss = atspi_accessible_get_state_set(A(h));
    const bool editable = HasState(ss, ATSPI_STATE_EDITABLE);
    if (ss) g_object_unref(ss);
    return editable;
}

bool InsertText(Handle h, const std::string& text, bool clear_first, std::string& error) {
    AtspiEditableText* et = atspi_accessible_get_editable_text_iface(A(h));
    if (!et) { error = "not_supported"; return false; }
    GError* err = nullptr;
    gboolean ok;
    if (clear_first) {
        ok = atspi_editable_text_set_text_contents(et, text.c_str(), &err);
    } else {
        gint pos = -1;
        if (AtspiText* t = atspi_accessible_get_text_iface(A(h))) {
            pos = atspi_text_get_caret_offset(t, &err);
            ClearError(&err);
            g_object_unref(t);
        }
        if (pos < 0) pos = 0x7fffffff;                       // no caret information: append
        ok = atspi_editable_text_insert_text(et, pos, text.c_str(), static_cast<gint>(text.size()), &err);
    }
    ClearError(&err);
    g_object_unref(et);
    if (!ok) error = "failed";
    return ok;
}

}  // namespace pai::cc::atspi

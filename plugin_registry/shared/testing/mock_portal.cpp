// A stand-in for xdg-desktop-portal, for tests only: speaks the real portal wire protocol
// (Request objects, `Response` signals, remote-desktop sessions) on a private session bus.
//
//   dbus-run-session -- mock_portal            # prints READY once the bus name is owned
//
// Behaviour (environment):
//   MOCK_LOG=<file>              append one JSON line per call received
//   MOCK_DELAY_MS=30             delay before a Response signal (the client must not race it)
//   MOCK_SCREENSHOT_URI=<uri>    file:// uri returned by Screenshot
//   MOCK_SCREENSHOT_CODE=0|1|2   response code (1 = user cancelled)
//   MOCK_NO_RESPONSE=<Method>    never answer that request method (timeout tests)
//   MOCK_START_CODE=0|1|2        response code of RemoteDesktop.Start
//   MOCK_STREAMS="x,y,w,h;..."   screens reported by Start (default 0,0,1920,1080)
//   MOCK_DEVICES=3               granted device mask reported by Start
//   MOCK_SESSION_AS_PATH=1       return session_handle as an object path ('o') instead of 's'
//   MOCK_DIE_AFTER_NOTIFY=N      after N Notify* calls the session "disappears" (UnknownObject)
//   MOCK_FAIL_NOTIFY_AT=N        the Nth Notify* call alone fails (transient org.freedesktop.DBus.Error.Failed)
//   MOCK_WAIT_FOR_SESSION=1      nothing special; reserved
#include <gio/gio.h>

#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

namespace {

const char* kXml = R"(
<node>
  <interface name="org.freedesktop.portal.Screenshot">
    <method name="Screenshot"><arg type="s" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
    <property name="version" type="u" access="read"/>
  </interface>
  <interface name="org.freedesktop.portal.ScreenCast">
    <method name="SelectSources"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
    <property name="version" type="u" access="read"/>
  </interface>
  <interface name="org.freedesktop.portal.RemoteDesktop">
    <method name="CreateSession"><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
    <method name="SelectDevices"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
    <method name="Start"><arg type="o" direction="in"/><arg type="s" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
    <method name="NotifyPointerMotionAbsolute"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="u" direction="in"/><arg type="d" direction="in"/><arg type="d" direction="in"/></method>
    <method name="NotifyPointerButton"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="i" direction="in"/><arg type="u" direction="in"/></method>
    <method name="NotifyPointerAxisDiscrete"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="u" direction="in"/><arg type="i" direction="in"/></method>
    <method name="NotifyKeyboardKeysym"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="i" direction="in"/><arg type="u" direction="in"/></method>
    <property name="version" type="u" access="read"/>
    <property name="AvailableDeviceTypes" type="u" access="read"/>
  </interface>
</node>)";

const char* kSessionXml = R"(
<node><interface name="org.freedesktop.portal.Session">
  <method name="Close"/>
  <signal name="Closed"><arg type="a{sv}"/></signal>
</interface></node>)";

GDBusConnection* g_conn = nullptr;
GDBusNodeInfo* g_session_info = nullptr;
std::vector<guint> g_session_regs;
int g_notify_count = 0;
bool g_session_dead = false;

std::string Env(const char* k, const char* d = "") { const char* v = std::getenv(k); return (v && *v) ? v : d; }
int EnvInt(const char* k, int d) { auto s = Env(k); return s.empty() ? d : std::atoi(s.c_str()); }

std::string Esc(const std::string& s) {
    std::string o;
    for (char c : s) { if (c == '"' || c == '\\') o += '\\'; o += c; }
    return o;
}

void Log(const std::string& json) {
    auto path = Env("MOCK_LOG");
    if (path.empty()) return;
    if (FILE* f = std::fopen(path.c_str(), "a")) { std::fprintf(f, "%s\n", json.c_str()); std::fclose(f); }
}

std::string SenderToken(const char* sender) {
    std::string s = sender;
    if (!s.empty() && s[0] == ':') s.erase(0, 1);
    for (auto& c : s) if (c == '.') c = '_';
    return s;
}

struct Resp { std::string sender, path; guint code; GVariant* results; };

gboolean EmitResponse(gpointer p) {
    auto* r = static_cast<Resp*>(p);
    GError* err = nullptr;
    g_dbus_connection_emit_signal(g_conn, r->sender.c_str(), r->path.c_str(), "org.freedesktop.portal.Request", "Response",
                                  g_variant_new("(u@a{sv})", r->code, r->results), &err);
    if (err) g_error_free(err);
    delete r;
    return G_SOURCE_REMOVE;
}

GVariant* Dict(std::vector<std::pair<const char*, GVariant*>> kv) {
    GVariantBuilder b;
    g_variant_builder_init(&b, G_VARIANT_TYPE("a{sv}"));
    for (auto& e : kv) g_variant_builder_add(&b, "{sv}", e.first, e.second);
    return g_variant_builder_end(&b);
}

std::string Token(GVariant* options, const char* key = "handle_token") {
    const gchar* t = nullptr;
    if (!g_variant_lookup(options, key, "&s", &t) || !t) return "";
    return t;
}

void OnSessionCall(GDBusConnection*, const gchar*, const gchar* path, const gchar*, const gchar* method, GVariant*,
                   GDBusMethodInvocation* inv, gpointer) {
    if (std::string(method) == "Close") {
        Log(std::string("{\"method\":\"Session.Close\",\"session\":\"") + path + "\"}");
        g_dbus_method_invocation_return_value(inv, nullptr);
    }
}

GVariant* BuildStreams() {
    GVariantBuilder arr;
    g_variant_builder_init(&arr, G_VARIANT_TYPE("a(ua{sv})"));
    const char* configured = std::getenv("MOCK_STREAMS");        // set-but-empty means "no screen shared"
    std::string spec = configured ? configured : "0,0,1920,1080";
    guint node = 40;
    size_t pos = 0;
    while (pos <= spec.size()) {
        size_t end = spec.find(';', pos);
        if (end == std::string::npos) end = spec.size();
        int x = 0, y = 0, w = 0, h = 0;
        if (std::sscanf(spec.substr(pos, end - pos).c_str(), "%d,%d,%d,%d", &x, &y, &w, &h) == 4) {
            GVariant* props = Dict({{"position", g_variant_new("(ii)", x, y)}, {"size", g_variant_new("(ii)", w, h)}});
            g_variant_builder_add(&arr, "(u@a{sv})", node++, props);
        }
        pos = end + 1;
    }
    return g_variant_builder_end(&arr);
}

void OnCall(GDBusConnection* conn, const gchar* sender, const gchar* object_path, const gchar* iface, const gchar* method,
            GVariant* params, GDBusMethodInvocation* inv, gpointer) {
    const std::string m = method;
    const bool request_style = m == "Screenshot" || m == "CreateSession" || m == "SelectDevices" || m == "SelectSources" || m == "Start";
    if (!request_style) {                                   // Notify* methods: record, maybe die
        ++g_notify_count;
        int die_after = EnvInt("MOCK_DIE_AFTER_NOTIFY", 0);
        if (g_session_dead || (die_after > 0 && g_notify_count > die_after)) {
            g_session_dead = true;
            gchar* dead_params = g_variant_print(params, FALSE);                  // still record the ATTEMPT
            Log(std::string("{\"method\":\"") + m + "\",\"dead\":true,\"params\":\"" + Esc(dead_params) + "\"}");
            g_free(dead_params);
            g_dbus_method_invocation_return_dbus_error(inv, "org.freedesktop.DBus.Error.UnknownObject", "no such session");
            return;
        }
        gchar* s = g_variant_print(params, FALSE);
        if (g_notify_count == EnvInt("MOCK_FAIL_NOTIFY_AT", 0)) {
            Log(std::string("{\"method\":\"") + m + "\",\"dead\":true,\"params\":\"" + Esc(s) + "\"}");   // attempted, not accepted
            g_free(s);
            g_dbus_method_invocation_return_dbus_error(inv, "org.freedesktop.DBus.Error.Failed", "transient failure");
            return;
        }
        Log(std::string("{\"method\":\"") + m + "\",\"params\":\"" + Esc(s) + "\"}");
        g_free(s);
        g_dbus_method_invocation_return_value(inv, nullptr);
        return;
    }

    // options is the last argument of every request-style method
    GVariant* options = g_variant_get_child_value(params, g_variant_n_children(params) - 1);
    const std::string token = Token(options);
    const std::string request_path = "/org/freedesktop/portal/desktop/request/" + SenderToken(sender) + "/" + token;
    gchar* printed = g_variant_print(params, FALSE);
    Log(std::string("{\"method\":\"") + m + "\",\"params\":\"" + Esc(printed) + "\"}");
    g_free(printed);

    guint code = 0;
    GVariant* results = Dict({});
    if (m == "Screenshot") {
        code = static_cast<guint>(EnvInt("MOCK_SCREENSHOT_CODE", 0));
        if (code == 0) results = Dict({{"uri", g_variant_new_string(Env("MOCK_SCREENSHOT_URI", "file:///nonexistent.png").c_str())}});
    } else if (m == "CreateSession") {
        const std::string stoken = Token(options, "session_handle_token");
        const std::string session = "/org/freedesktop/portal/desktop/session/" + SenderToken(sender) + "/" + stoken;
        if (!g_session_info) g_session_info = g_dbus_node_info_new_for_xml(kSessionXml, nullptr);
        GError* err = nullptr;
        guint id = g_dbus_connection_register_object(conn, session.c_str(), g_session_info->interfaces[0],
                                                     new GDBusInterfaceVTable{OnSessionCall, nullptr, nullptr, {}}, nullptr, nullptr, &err);
        if (err) g_error_free(err);
        g_session_regs.push_back(id);
        results = Dict({{"session_handle", EnvInt("MOCK_SESSION_AS_PATH", 0)
                                              ? g_variant_new_object_path(session.c_str())
                                              : g_variant_new_string(session.c_str())}});
    } else if (m == "Start") {
        code = static_cast<guint>(EnvInt("MOCK_START_CODE", 0));
        if (code == 0) results = Dict({{"devices", g_variant_new_uint32(static_cast<guint>(EnvInt("MOCK_DEVICES", 3)))},
                                       {"streams", BuildStreams()}});
    }
    g_variant_unref(options);

    g_dbus_method_invocation_return_value(inv, g_variant_new("(o)", request_path.c_str()));
    if (Env("MOCK_NO_RESPONSE") == m) { g_variant_unref(g_variant_ref_sink(results)); return; }
    g_timeout_add(static_cast<guint>(EnvInt("MOCK_DELAY_MS", 30)), EmitResponse, new Resp{sender, request_path, code, results});
}

GVariant* OnGetProp(GDBusConnection*, const gchar*, const gchar*, const gchar*, const gchar* prop, GError**, gpointer) {
    if (std::string(prop) == "AvailableDeviceTypes") return g_variant_new_uint32(7);
    return g_variant_new_uint32(2);
}

void OnAcquired(GDBusConnection* conn, const gchar*, gpointer) {
    g_conn = conn;
    GDBusNodeInfo* info = g_dbus_node_info_new_for_xml(kXml, nullptr);
    static GDBusInterfaceVTable vt = {OnCall, OnGetProp, nullptr, {}};
    for (guint i = 0; info->interfaces[i]; ++i) {
        GError* err = nullptr;
        g_dbus_connection_register_object(conn, "/org/freedesktop/portal/desktop", info->interfaces[i], &vt, nullptr, nullptr, &err);
        if (err) { std::fprintf(stderr, "register: %s\n", err->message); g_error_free(err); }
    }
}

void OnNameAcquired(GDBusConnection*, const gchar*, gpointer) {
    std::printf("READY\n");
    std::fflush(stdout);
}

void OnNameLost(GDBusConnection*, const gchar* name, gpointer) {
    std::fprintf(stderr, "lost bus name %s\n", name);
    std::exit(2);
}

}  // namespace

int main() {
    g_bus_own_name(G_BUS_TYPE_SESSION, Env("PAI_PORTAL_BUS_NAME", "org.freedesktop.portal.Desktop").c_str(),
                   G_BUS_NAME_OWNER_FLAGS_NONE, OnAcquired, OnNameAcquired, OnNameLost, nullptr, nullptr);
    g_main_loop_run(g_main_loop_new(nullptr, FALSE));
    return 0;
}

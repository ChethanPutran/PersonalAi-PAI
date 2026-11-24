#include "pai_portal.h"

#include <gio/gio.h>

#include <atomic>
#include <functional>
#include <cstdlib>
#include <mutex>
#include <unistd.h>

namespace pai::portal {
namespace {

const char* kObjectPath = "/org/freedesktop/portal/desktop";
const char* kRemoteDesktop = "org.freedesktop.portal.RemoteDesktop";
const char* kScreenCast = "org.freedesktop.portal.ScreenCast";
const char* kScreenshot = "org.freedesktop.portal.Screenshot";
const char* kRequest = "org.freedesktop.portal.Request";
const char* kSessionIface = "org.freedesktop.portal.Session";

struct GErr {
    GError* e = nullptr;
    ~GErr() { if (e) g_error_free(e); }
    std::string msg() const { return e && e->message ? e->message : ""; }
    GError** out() { return &e; }
};

GDBusConnection* Connection(std::string& detail) {
    static std::mutex m;
    static GDBusConnection* conn = nullptr;
    std::lock_guard<std::mutex> lock(m);
    if (conn && !g_dbus_connection_is_closed(conn)) return conn;
    GErr err;
    conn = g_bus_get_sync(G_BUS_TYPE_SESSION, nullptr, err.out());
    if (!conn) detail = err.msg();
    return conn;
}

// ":1.42" -> "1_42": how the portal spells a caller inside request/session object paths.
std::string SenderToken(GDBusConnection* c) {
    std::string s = g_dbus_connection_get_unique_name(c) ? g_dbus_connection_get_unique_name(c) : "";
    if (!s.empty() && s[0] == ':') s.erase(0, 1);
    for (auto& ch : s) if (ch == '.') ch = '_';
    return s;
}

std::string NewToken() {
    static std::atomic<unsigned> counter{0};
    return "pai" + std::to_string(::getpid()) + "_" + std::to_string(++counter);
}

bool ServiceMissing(const GError* e) {
    return e && (g_error_matches(e, G_DBUS_ERROR, G_DBUS_ERROR_SERVICE_UNKNOWN) ||
                 g_error_matches(e, G_DBUS_ERROR, G_DBUS_ERROR_NAME_HAS_NO_OWNER));
}

// ---------------------------------------------------------------- request / response

struct Pending {
    GMainLoop* loop = nullptr;
    bool got = false;
    bool timed_out = false;
    uint32_t code = 2;
    GVariant* results = nullptr;     // owned
};

void OnResponse(GDBusConnection*, const gchar*, const gchar*, const gchar*, const gchar*,
                GVariant* params, gpointer user) {
    auto* p = static_cast<Pending*>(user);
    if (p->got) return;
    guint32 code = 2;
    GVariant* results = nullptr;
    g_variant_get(params, "(u@a{sv})", &code, &results);
    p->code = code;
    p->results = results;
    p->got = true;
    g_main_loop_quit(p->loop);
}

gboolean OnTimeout(gpointer user) {
    auto* p = static_cast<Pending*>(user);
    if (!p->got) p->timed_out = true;
    g_main_loop_quit(p->loop);
    return G_SOURCE_REMOVE;
}

struct Reply {
    bool ok = false;
    std::string error;               // portal_unavailable | cancelled | timeout | failed
    std::string detail;
    GVariant* results = nullptr;     // owned
    ~Reply() { if (results) g_variant_unref(results); }
    Reply() = default;
    Reply(const Reply&) = delete;
    Reply& operator=(const Reply&) = delete;
};

/// Calls a portal method that returns a request handle and later answers with a `Response` signal.
///
/// The `Response` signal can arrive before the method call returns, so we subscribe to the request
/// path *first*. The path is predictable because the caller passes `handle_token` in the options
/// (`build` must put the token it is given there): /portal/desktop/request/<sender>/<token>.
void CallRequest(GDBusConnection* c, const char* iface, const char* method,
                 const std::function<GVariant*(const std::string&)>& build, int timeout_sec, Reply& r) {
    const std::string token = NewToken();
    const std::string path = std::string("/org/freedesktop/portal/desktop/request/") + SenderToken(c) + "/" + token;
    const std::string bus = BusName();

    GMainContext* ctx = g_main_context_new();
    g_main_context_push_thread_default(ctx);      // signal callbacks are dispatched in the subscribing context
    Pending pend;
    pend.loop = g_main_loop_new(ctx, FALSE);

    guint sub = g_dbus_connection_signal_subscribe(c, bus.c_str(), kRequest, "Response", path.c_str(), nullptr,
                                                  G_DBUS_SIGNAL_FLAGS_NONE, OnResponse, &pend, nullptr);
    guint sub2 = 0;
    GErr err;
    GVariant* ret = g_dbus_connection_call_sync(c, bus.c_str(), kObjectPath, iface, method, build(token),
                                                G_VARIANT_TYPE("(o)"), G_DBUS_CALL_FLAGS_NONE, 30000, nullptr, err.out());
    if (!ret) {
        r.error = ServiceMissing(err.e) ? "portal_unavailable" : "failed";
        r.detail = err.msg();
    } else {
        const gchar* handle = nullptr;
        g_variant_get(ret, "(&o)", &handle);
        if (handle && path != handle)            // a portal that ignored handle_token: listen on its real path too
            sub2 = g_dbus_connection_signal_subscribe(c, bus.c_str(), kRequest, "Response", handle, nullptr,
                                                      G_DBUS_SIGNAL_FLAGS_NONE, OnResponse, &pend, nullptr);
        GSource* timer = g_timeout_source_new_seconds(static_cast<guint>(timeout_sec > 0 ? timeout_sec : 1));
        g_source_set_callback(timer, OnTimeout, &pend, nullptr);
        g_source_attach(timer, ctx);
        g_main_loop_run(pend.loop);               // returns on Response or timeout
        g_source_destroy(timer);
        g_source_unref(timer);
        g_variant_unref(ret);

        if (pend.got) {
            r.results = pend.results;
            pend.results = nullptr;
            if (pend.code == 0) r.ok = true;
            else { r.error = pend.code == 1 ? "cancelled" : "failed"; r.detail = "portal response " + std::to_string(pend.code); }
        } else {
            r.error = "timeout";
            r.detail = "no answer within " + std::to_string(timeout_sec) + "s";
        }
    }
    if (sub) g_dbus_connection_signal_unsubscribe(c, sub);
    if (sub2) g_dbus_connection_signal_unsubscribe(c, sub2);
    if (pend.results) g_variant_unref(pend.results);
    g_main_loop_unref(pend.loop);
    g_main_context_pop_thread_default(ctx);
    g_main_context_unref(ctx);
}

GVariantBuilder* NewOptions(const std::string& token) {
    auto* b = g_variant_builder_new(G_VARIANT_TYPE("a{sv}"));
    g_variant_builder_add(b, "{sv}", "handle_token", g_variant_new_string(token.c_str()));
    return b;
}

}  // namespace

std::string BusName() {
    const char* env = std::getenv("PAI_PORTAL_BUS_NAME");
    return (env && *env) ? env : "org.freedesktop.portal.Desktop";
}

// ----------------------------------------------------------------- screenshot

ScreenshotResult Screenshot(int timeout_sec) {
    ScreenshotResult out;
    std::string detail;
    GDBusConnection* c = Connection(detail);
    if (!c) { out.error = "portal_unavailable"; out.detail = detail; return out; }

    Reply r;
    CallRequest(c, kScreenshot, "Screenshot", [](const std::string& tok) {
        GVariantBuilder* b = NewOptions(tok);
        g_variant_builder_add(b, "{sv}", "interactive", g_variant_new_boolean(FALSE));
        g_variant_builder_add(b, "{sv}", "modal", g_variant_new_boolean(FALSE));
        GVariant* v = g_variant_new("(sa{sv})", "", b);
        g_variant_builder_unref(b);
        return v;
    }, timeout_sec, r);
    if (!r.ok) { out.error = r.error; out.detail = r.detail; return out; }

    const gchar* uri = nullptr;
    if (!g_variant_lookup(r.results, "uri", "&s", &uri) || !uri || !*uri) {
        out.error = "failed";
        out.detail = "the portal returned no file";
        return out;
    }
    GErr err;
    gchar* path = g_filename_from_uri(uri, nullptr, err.out());
    if (!path) { out.error = "failed"; out.detail = std::string("bad uri: ") + uri; return out; }
    out.path = path;
    g_free(path);
    out.ok = true;
    return out;
}

// ---------------------------------------------------------------------- input

struct InputSession::Impl {
    std::mutex m;
    GDBusConnection* conn = nullptr;
    std::string session;
    bool active = false;
    uint32_t devices = 0;
    std::vector<Stream> streams;

    // Called with `m` held.
    void CloseLocked() {
        if (conn && !session.empty()) {
            GErr err;
            GVariant* r = g_dbus_connection_call_sync(conn, BusName().c_str(), session.c_str(), kSessionIface, "Close",
                                                      nullptr, nullptr, G_DBUS_CALL_FLAGS_NONE, 5000, nullptr, err.out());
            if (r) g_variant_unref(r);
        }
        session.clear();
        active = false;
        devices = 0;
        streams.clear();
    }

    // Called with `m` held. Returns false (+ error) on failure.
    bool Notify(const char* method, GVariant* params, std::string& error) {
        if (!active) { error = "session_closed"; g_variant_unref(g_variant_ref_sink(params)); return false; }
        GErr err;
        GVariant* r = g_dbus_connection_call_sync(conn, BusName().c_str(), kObjectPath, kRemoteDesktop, method, params,
                                                  nullptr, G_DBUS_CALL_FLAGS_NONE, 5000, nullptr, err.out());
        if (r) { g_variant_unref(r); return true; }
        gchar* remote = err.e ? g_dbus_error_get_remote_error(err.e) : nullptr;
        const std::string name = remote ? remote : "";
        g_free(remote);
        const bool gone = name.find("UnknownObject") != std::string::npos || name.find("NotFound") != std::string::npos ||
                          name.find("Closed") != std::string::npos || name.find("NoReply") != std::string::npos ||
                          ServiceMissing(err.e);
        if (gone) {
            // It looks dead, but "no reply" can also mean merely slow. Close it (best effort): that makes the
            // portal drop every key/button we may still hold and remove its "remote control active" indicator,
            // instead of leaking an open session we have forgotten about.
            CloseLocked();
            error = "session_closed";
        } else {
            error = "notify_failed";          // transient: the session stays; callers can still release keys
        }
        return false;
    }
};

InputSession::Impl* InputSession::impl() {
    static Impl instance;
    return &instance;
}

InputSession& InputSession::Get() {
    static InputSession s;
    return s;
}

bool InputSession::Start(int timeout_sec, std::string& error, std::string& detail) {
    Impl* d = impl();
    std::lock_guard<std::mutex> lock(d->m);
    if (d->active) return true;

    d->conn = Connection(detail);
    if (!d->conn) { error = "portal_unavailable"; return false; }

    auto fail = [&](const Reply& r) {
        error = r.error;
        detail = r.detail;
        d->CloseLocked();
        return false;
    };

    // 1. CreateSession -> session handle
    Reply r1;
    const std::string session_token = "paisess_" + NewToken();
    CallRequest(d->conn, kRemoteDesktop, "CreateSession", [&](const std::string& tok) {
        GVariantBuilder* b = NewOptions(tok);
        g_variant_builder_add(b, "{sv}", "session_handle_token", g_variant_new_string(session_token.c_str()));
        GVariant* v = g_variant_new("(a{sv})", b);
        g_variant_builder_unref(b);
        return v;
    }, 15, r1);
    if (!r1.ok) return fail(r1);
    GVariant* sh = g_variant_lookup_value(r1.results, "session_handle", nullptr);   // 's' (or 'o' on some portals)
    if (!sh || !(g_variant_is_of_type(sh, G_VARIANT_TYPE_STRING) || g_variant_is_of_type(sh, G_VARIANT_TYPE_OBJECT_PATH))) {
        if (sh) g_variant_unref(sh);
        error = "failed"; detail = "the portal returned no session";
        return false;
    }
    d->session = g_variant_get_string(sh, nullptr);
    g_variant_unref(sh);
    if (!g_variant_is_object_path(d->session.c_str())) {
        d->session.clear();
        error = "failed"; detail = "the portal returned an invalid session handle";
        return false;
    }

    // 2. Devices: keyboard (1) + pointer (2).
    Reply r2;
    CallRequest(d->conn, kRemoteDesktop, "SelectDevices", [&](const std::string& tok) {
        GVariantBuilder* b = NewOptions(tok);
        g_variant_builder_add(b, "{sv}", "types", g_variant_new_uint32(3));
        GVariant* v = g_variant_new("(oa{sv})", d->session.c_str(), b);
        g_variant_builder_unref(b);
        return v;
    }, 15, r2);
    if (!r2.ok) return fail(r2);

    // 3. Screens (monitors): needed for absolute pointer positioning. The user picks which.
    Reply r3;
    CallRequest(d->conn, kScreenCast, "SelectSources", [&](const std::string& tok) {
        GVariantBuilder* b = NewOptions(tok);
        g_variant_builder_add(b, "{sv}", "types", g_variant_new_uint32(1));          // monitors
        g_variant_builder_add(b, "{sv}", "multiple", g_variant_new_boolean(TRUE));
        GVariant* v = g_variant_new("(oa{sv})", d->session.c_str(), b);
        g_variant_builder_unref(b);
        return v;
    }, 15, r3);
    if (!r3.ok) return fail(r3);

    // 4. Start: the compositor shows its approval dialog here.
    Reply r4;
    CallRequest(d->conn, kRemoteDesktop, "Start", [&](const std::string& tok) {
        GVariantBuilder* b = NewOptions(tok);
        GVariant* v = g_variant_new("(osa{sv})", d->session.c_str(), "", b);
        g_variant_builder_unref(b);
        return v;
    }, timeout_sec, r4);
    if (!r4.ok) return fail(r4);

    guint32 devices = 0;
    g_variant_lookup(r4.results, "devices", "u", &devices);
    d->devices = devices;
    d->streams.clear();
    if (GVariant* st = g_variant_lookup_value(r4.results, "streams", G_VARIANT_TYPE("a(ua{sv})"))) {
        GVariantIter it;
        g_variant_iter_init(&it, st);
        guint32 node = 0;
        GVariant* props = nullptr;
        while (g_variant_iter_next(&it, "(u@a{sv})", &node, &props)) {
            Stream s;
            s.node_id = node;
            gint x = 0, y = 0, w = 0, h = 0;
            if (g_variant_lookup(props, "position", "(ii)", &x, &y)) { s.x = x; s.y = y; }
            if (g_variant_lookup(props, "size", "(ii)", &w, &h)) { s.w = w; s.h = h; }
            d->streams.push_back(s);
            g_variant_unref(props);
        }
        g_variant_unref(st);
    }
    d->active = true;
    return true;
}

void InputSession::Stop() {
    Impl* d = impl();
    std::lock_guard<std::mutex> lock(d->m);
    d->CloseLocked();
}

InputStatus InputSession::Status() {
    Impl* d = impl();
    std::lock_guard<std::mutex> lock(d->m);
    InputStatus s;
    s.active = d->active;
    s.devices = d->devices;
    s.streams = d->streams;
    return s;
}

namespace {
GVariant* NoOptions() { return g_variant_new_array(G_VARIANT_TYPE("{sv}"), nullptr, 0); }
}  // namespace

bool InputSession::PointerMotionAbsolute(uint32_t stream_node, double x, double y, std::string& error) {
    Impl* d = impl();
    std::lock_guard<std::mutex> lock(d->m);
    return d->Notify("NotifyPointerMotionAbsolute",
                     g_variant_new("(o@a{sv}udd)", d->session.empty() ? "/" : d->session.c_str(), NoOptions(), stream_node, x, y),
                     error);
}

bool InputSession::PointerButton(int evdev_button, bool pressed, std::string& error) {
    Impl* d = impl();
    std::lock_guard<std::mutex> lock(d->m);
    return d->Notify("NotifyPointerButton",
                     g_variant_new("(o@a{sv}iu)", d->session.empty() ? "/" : d->session.c_str(), NoOptions(),
                                   static_cast<gint32>(evdev_button), static_cast<guint32>(pressed ? 1 : 0)),
                     error);
}

bool InputSession::PointerAxisDiscrete(uint32_t axis, int steps, std::string& error) {
    Impl* d = impl();
    std::lock_guard<std::mutex> lock(d->m);
    return d->Notify("NotifyPointerAxisDiscrete",
                     g_variant_new("(o@a{sv}ui)", d->session.empty() ? "/" : d->session.c_str(), NoOptions(),
                                   static_cast<guint32>(axis), static_cast<gint32>(steps)),
                     error);
}

bool InputSession::KeyboardKeysym(int32_t keysym, bool pressed, std::string& error) {
    Impl* d = impl();
    std::lock_guard<std::mutex> lock(d->m);
    return d->Notify("NotifyKeyboardKeysym",
                     g_variant_new("(o@a{sv}iu)", d->session.empty() ? "/" : d->session.c_str(), NoOptions(),
                                   static_cast<gint32>(keysym), static_cast<guint32>(pressed ? 1 : 0)),
                     error);
}

}  // namespace pai::portal

#pragma once
// Client for the XDG desktop portals (org.freedesktop.portal.*) used by the screen and input plugins.
//
// Why portals: on a Wayland desktop (GNOME, KDE) programs may not grab the screen or inject
// input behind the compositor's back. The portals are the sanctioned route: the compositor shows
// its own permission dialog and draws its "screen is being shared" indicator, and the user can
// revoke at any time.
//
// Built on GDBus (libgio): needed at build time (pkg-config gio-2.0); at run time any GTK/Flutter
// host already has it loaded.
#include <cstdint>
#include <string>
#include <vector>

namespace pai::portal {

/// Bus name of the portal service. $PAI_PORTAL_BUS_NAME overrides it (tests talk to a mock).
std::string BusName();

// ---------------------------------------------------------------- screenshot

struct ScreenshotResult {
    bool ok = false;
    std::string path;     // local file the portal produced
    std::string error;    // portal_unavailable | cancelled | timeout | failed
    std::string detail;
};

/// org.freedesktop.portal.Screenshot. Blocks up to `timeout_sec` (the compositor may ask first).
ScreenshotResult Screenshot(int timeout_sec);

// --------------------------------------------------------------------- input

/// One shared screen (monitor) of the remote-desktop session, in the compositor's logical pixels.
struct Stream {
    uint32_t node_id = 0;
    int x = 0, y = 0;     // position in the global desktop layout
    int w = 0, h = 0;     // logical size
};

struct InputStatus {
    bool active = false;
    uint32_t devices = 0;           // granted device types: 1 keyboard, 2 pointer, 4 touch
    std::vector<Stream> streams;
};

/// A remote-desktop session that stays open for the life of the process (the portal ties it to our
/// bus connection) so many input events reuse one user approval.
class InputSession {
public:
    static InputSession& Get();

    /// Create + select devices/screens + start. The compositor asks the user; blocks up to
    /// `timeout_sec`. Idempotent while active. On failure `error` is a short code
    /// (portal_unavailable | cancelled | timeout | failed) and `detail` says more.
    bool Start(int timeout_sec, std::string& error, std::string& detail);
    void Stop();
    InputStatus Status();

    // Each returns false and sets `error` on failure. A failure that shows the session is gone
    // (the user revoked it) marks the session inactive: error == "session_closed".
    bool PointerMotionAbsolute(uint32_t stream_node, double x, double y, std::string& error);
    bool PointerButton(int evdev_button, bool pressed, std::string& error);
    /// Wheel clicks: axis 0 = vertical (positive = down), 1 = horizontal (positive = right).
    bool PointerAxisDiscrete(uint32_t axis, int steps, std::string& error);
    bool KeyboardKeysym(int32_t keysym, bool pressed, std::string& error);

private:
    InputSession() = default;
    struct Impl;
    Impl* impl();
};

}  // namespace pai::portal

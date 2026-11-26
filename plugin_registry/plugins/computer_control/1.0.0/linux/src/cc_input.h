#pragma once
// Synthetic keyboard and pointer input behind one interface. Two backends:
//   * portal: the desktop's remote-desktop portal (works on Wayland; the compositor asks the user
//             and draws its "remote control" indicator). Reuses shared/pai_portal's session.
//   * xtest : the X11 XTEST extension, for a plain X11 session (no portal dialog exists there).
// PAI_CC_INPUT_BACKEND=auto|portal|xtest chooses; auto = xtest on X11, portal on Wayland.
#include <cstdint>
#include <string>

namespace pai::cc {

constexpr int kBtnLeft = 0x110, kBtnRight = 0x111, kBtnMiddle = 0x112;     // linux/input-event-codes.h

class InputBackend {
public:
    virtual ~InputBackend() = default;
    virtual const char* Name() const = 0;
    /// Make the backend usable (the portal asks the user the first time). On failure `error`/`hint` say why.
    virtual bool Ready(std::string& error, std::string& hint) = 0;
    /// True when events can be sent without further user approval.
    virtual bool Active() = 0;
    // Absolute desktop pixels. Each returns false and sets `error`.
    virtual bool Move(double x, double y, std::string& error) = 0;
    virtual bool Button(int evdev_button, bool pressed, std::string& error) = 0;
    /// Wheel steps: axis 0 vertical (positive = down), 1 horizontal (positive = right).
    virtual bool Wheel(int axis, int steps, std::string& error) = 0;
    virtual bool Key(int32_t keysym, bool pressed, std::string& error) = 0;
    /// Size of the desktop the coordinates live in; false if unknown.
    virtual bool DesktopSize(int& w, int& h) = 0;
};

/// The backend chosen by PAI_CC_INPUT_BACKEND / the session type. Owned by the module.
InputBackend& Backend();
/// Name of the backend that would be used ("portal" | "xtest"), without creating a session.
std::string BackendName();
/// "x11" | "wayland" | "unknown" (XDG_SESSION_TYPE, then the display variables)
std::string SessionKind();
void StopBackends();

}  // namespace pai::cc

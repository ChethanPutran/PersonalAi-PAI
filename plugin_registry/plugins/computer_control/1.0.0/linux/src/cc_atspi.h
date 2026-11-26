#pragma once
// The accessibility tree through AT-SPI2 (libatspi): what the user would call the controls of the
// active window, with names, roles, states and screen positions, and semantic actions on them.
#include "cc_model.h"

#include <string>
#include <vector>

namespace pai::cc::atspi {

struct BusStatus {
    bool bus = false;        // the accessibility bus is reachable (org.a11y.Bus answers)
    bool enabled = false;    // apps were asked to expose themselves (org.a11y.Status IsEnabled)
};

/// Asks the session bus; never starts anything. Bounded by a short timeout.
BusStatus Status();

struct AppInfo {
    std::string id, name, title;     // id: the desktop-file id when known, else the executable's name
    long pid = 0;
};

struct Observation {
    bool ok = false;
    std::string error;               // a11y_unavailable | no_window
    std::string detail;
    AppInfo app;
    Tree tree;                       // ids -> handles; the handles are owned by the caller (see Release)
};

/// The active window's tree. `fallback_pid` (the X11 active window's pid, 0 if unknown) picks the app when
/// no window reports the ACTIVE state (some Wayland apps do not).
Observation ObserveActiveWindow(const Limits& limits, long fallback_pid);

/// Only the foreground app and window title (no tree walk).
bool Foreground(AppInfo& out, long fallback_pid, std::string& error);

/// Drop the reference an observation's handle held.
void Release(Handle h);

// Semantic actions on an element handle from the CURRENT observation. All fail soft: they return false and
// say why in `error` (element_gone | not_supported | failed).
bool IsAlive(Handle h);
bool GetBounds(Handle h, Bounds& out);
bool DoClickAction(Handle h, std::string& which, std::string& error);
bool Focus(Handle h, std::string& error);
bool IsEditable(Handle h);
/// Replace (clear_first) or insert at the caret. Refuses password fields' read-back but writes are fine.
bool InsertText(Handle h, const std::string& text, bool clear_first, std::string& error);

}  // namespace pai::cc::atspi

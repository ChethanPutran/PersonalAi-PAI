#pragma once
// X11 pieces: synthetic input through the XTest extension (the fallback when no portal is wanted or
// available: a plain X11 session) and the active window of an X11 session.
#include <cstdint>
#include <string>

namespace pai::cc::x11 {

/// True when an X display can be opened AND (for input) the XTest extension is present. The display
/// connection is opened once and kept for the life of the process.
bool Available(std::string* why = nullptr);
bool XTestAvailable();

/// Size of the (virtual) screen in pixels; false when unknown.
bool ScreenSize(int& w, int& h);

// Input primitives. Each returns false and sets `error` on failure. Coordinates are absolute screen pixels.
bool MotionAbsolute(double x, double y, std::string& error);
bool Button(int x11_button, bool pressed, std::string& error);          // 1 left, 2 middle, 3 right, 4/5 wheel up/down, 6/7 left/right
bool Key(int32_t keysym, bool pressed, std::string& error);             // handles shift levels and unmapped keysyms

/// Whole-screen capture as an RGB PNG, straight from the X server (no helper program needed). Written to
/// `path` (created 0600). False with `error` when there is no display or the capture failed.
bool CaptureScreenPng(const std::string& path, int& w, int& h, std::string& error);

/// Active window of the session: pid (0 unknown) and title.
bool ActiveWindow(long& pid, std::string& title);

}  // namespace pai::cc::x11

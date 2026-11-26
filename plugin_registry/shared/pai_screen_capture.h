#pragma once
// Whole-desktop screenshot as PNG bytes: the desktop portal first (the sanctioned route on Wayland),
// then the command-line tools the screen plugin knows (grim, scrot, import, gnome-screenshot, spectacle).
//
// Extracted from the screen plugin (screen 1.1.0 keeps its own copy: a released version is immutable).
// The caller does the consent check; this only captures.
#include <string>

namespace pai::capture {

struct Shot {
    bool ok = false;
    std::string png_path;    // a private temp file the caller removes with Cleanup()
    int width = 0, height = 0;
    std::string tool;        // portal | grim | scrot | import | gnome-screenshot | spectacle
    std::string error;       // capture_denied | capture_timeout | no_screen_tool_available | capture_failed | capture_invalid
    std::string detail;
};

/// Width/height from a PNG's IHDR; false when the file is not a PNG.
bool PngSize(const std::string& path, int& w, int& h);

Shot CaptureScreen(int portal_timeout_sec);

/// Remove the temp file of a capture.
void Cleanup(Shot& shot);

}  // namespace pai::capture

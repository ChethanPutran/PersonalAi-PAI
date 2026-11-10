#include "camera_record.h"

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <dirent.h>
#include <fcntl.h>
#include <fstream>
#include <linux/videodev2.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace pai::camera {
namespace {

std::string default_output_path() {
    const char* home = std::getenv("HOME");
    std::string dir = home ? std::string(home) + "/Videos/PAI" : "/tmp/pai";
    ::mkdir(dir.c_str(), 0755);
    char buf[64];
    std::time_t t = std::time(nullptr);
    std::strftime(buf, sizeof(buf), "%Y%m%d_%H%M%S", std::localtime(&t));
    return dir + "/PAI_" + buf + ".mp4";
}

// Reject shell metacharacters / path injection.
std::string shellQuote(const std::string& s) {
    std::string out = "'";
    for (char c : s) {
        if (c == '\'') out += "'\\''";
        else           out += c;
    }
    out += "'";
    return out;
}

// If the caller passes a still-image extension, swap it for .mp4 —
// otherwise ffmpeg picks the image2 muxer and fails on the 2nd frame.
std::string normalise_video_path(const std::string& in) {
    auto ends_with = [&](const char* suf) {
        const size_t n = std::strlen(suf);
        return in.size() >= n &&
               in.compare(in.size() - n, n, suf) == 0;
    };
    if (ends_with(".jpg")  || ends_with(".jpeg") ||
        ends_with(".png")  || ends_with(".bmp")  ||
        ends_with(".webp")) {
        const size_t dot = in.find_last_of('.');
        return in.substr(0, dot) + ".mp4";
    }
    return in;
}

// Pick the first size the device accepts. Querying via V4L2 is more
// accurate than guessing, but a short try-list works fine and is
// cheaper than building a full probe.
int open_device(const std::string& device) {
    return ::open(device.c_str(), O_RDWR | O_NONBLOCK);
}

void close_device(int fd) {
    if (fd >= 0) ::close(fd);
}

}  // namespace

std::map<std::string, std::string> record(
        const std::map<std::string, std::string>& params) {

    const std::string device =
        params.count("device") ? params.at("device") : "/dev/video0";
    std::string out =
        (params.count("outputPath") && !params.at("outputPath").empty())
            ? params.at("outputPath")
            : default_output_path();
    out = normalise_video_path(out);

    const long duration_ms =
        params.count("durationMs") ? std::stol(params.at("durationMs")) : 5000L;

    // audio param exists in the manifest but this backend is video-only.
    // Report it explicitly instead of silently ignoring it.
    bool audio_requested = false;
    if (params.count("audio")) {
        const std::string& v = params.at("audio");
        audio_requested = (v == "true" || v == "1");
    }

    // Confirm the device is actually there before paying ffmpeg's cost.
    {
        int probe = open_device(device);
        if (probe < 0) {
            throw std::runtime_error("cannot open " + device +
                                     ": " + std::strerror(errno));
        }
        close_device(probe);
    }

    // Try the smallest set of sizes that cover the common laptop cameras.
    // Each attempt is fast (~100 ms) if the size is rejected by the driver.
    const char* sizes[] = { "1280x720", "960x540", "640x480", "640x360", "320x240" };
    const char* framerates[] = { "30", "15" };

    std::string last_err;
    struct stat st {};

    for (const char* size : sizes) {
        for (const char* fps : framerates) {
            // Use a per-attempt temp file so a partially written file
            // from an earlier failure doesn't get served as the output.
            std::ostringstream attempt_out;
            attempt_out << out << ".tmp." << ::getpid() << "."
                        << size << "." << fps << ".mp4";

            // Capture ffmpeg stderr so real errors surface, not
            // "ffmpeg recording failed".
            std::ostringstream logfile;
            logfile << "/tmp/pai_ffmpeg_" << ::getpid() << "_"
                    << size << "_" << fps << ".log";

            std::ostringstream cmd;
            cmd << "ffmpeg -nostdin -y -loglevel error"
                << " -f v4l2 -framerate " << fps
                << " -video_size " << size
                << " -i " << shellQuote(device)
                << " -t "   << (duration_ms / 1000.0)
                << " -c:v libx264 -preset veryfast -pix_fmt yuv420p"
                // Force the muxer so the extension can't override it
                << " -f mp4 "
                << shellQuote(attempt_out.str())
                << " 2>" << shellQuote(logfile.str());

            int rc = std::system(cmd.str().c_str());

            if (rc == 0) {
                // Atomic move into place
                if (std::rename(attempt_out.str().c_str(), out.c_str()) != 0) {
                    last_err = std::string("rename failed: ") +
                               std::strerror(errno);
                    std::remove(attempt_out.str().c_str());
                    continue;
                }
                std::remove(logfile.str().c_str());

                ::stat(out.c_str(), &st);

                std::map<std::string, std::string> result = {
                    {"ok", "true"},
                    {"path", out},
                    {"device", device},
                    {"durationMs", std::to_string(duration_ms)},
                    {"sizeBytes", std::to_string(st.st_size)},
                    {"resolution", size},
                    {"framerate", fps},
                    {"audioIncluded", "false"},
                };
                if (audio_requested) {
                    result["warning"] =
                        "audio=true requested, but this build is video-only";
                }
                return result;
            }

            // Grab ffmpeg's error text for the next message
            std::ifstream err(logfile.str());
            std::ostringstream ss;
            ss << err.rdbuf();
            last_err = ss.str();
            std::remove(logfile.str().c_str());
            std::remove(attempt_out.str().c_str());

            // Short‑circuit: no point trying other sizes if the device
            // is busy, missing, or permission-denied.
            if (last_err.find("Device or resource busy") != std::string::npos ||
                last_err.find("No such file or directory") != std::string::npos ||
                last_err.find("Permission denied") != std::string::npos) {
                throw std::runtime_error(
                    "recording " + device + ": " + last_err);
            }
            // Otherwise try the next size/fps combo.
        }
    }

    throw std::runtime_error(
        "ffmpeg recording failed (all sizes tried): " + last_err);
}

}  // namespace pai::camera
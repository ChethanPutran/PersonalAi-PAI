#include "camera_record.h"

#include <cstdlib>
#include <ctime>
#include <sstream>
#include <stdexcept>
#include <sys/stat.h>

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

}  // namespace

std::map<std::string, std::string> record(const std::map<std::string, std::string>& params) {
    const std::string device = params.count("device") ? params.at("device") : "/dev/video0";
    const std::string out = params.count("outputPath") ? params.at("outputPath") : default_output_path();
    const long duration_ms = params.count("durationMs") ? std::stol(params.at("durationMs")) : 5000L;

    std::ostringstream cmd;
    cmd << "ffmpeg -y -loglevel error"
        << " -f v4l2 -framerate 30 -video_size 1280x720 -i " << device
        << " -t " << (duration_ms / 1000.0)
        << " -c:v libx264 -preset veryfast -pix_fmt yuv420p "
        << out;

    int rc = std::system(cmd.str().c_str());
    if (rc != 0) throw std::runtime_error("ffmpeg recording failed");

    struct stat st{};
    ::stat(out.c_str(), &st);

    return {
        {"ok", "true"},
        {"path", out},
        {"durationMs", std::to_string(duration_ms)},
        {"sizeBytes", std::to_string(st.st_size)},
    };
}

}  // namespace pai::camera
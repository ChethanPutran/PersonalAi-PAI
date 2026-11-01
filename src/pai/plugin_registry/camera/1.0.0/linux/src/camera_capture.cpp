#include "camera_capture.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <dirent.h>
#include <fcntl.h>
#include <linux/videodev2.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/select.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace pai::camera {
namespace {

struct Fmt {
    uint32_t pixfmt;
    uint32_t width;
    uint32_t height;
};

int xioctl(int fd, unsigned long req, void* arg) {
    int r;
    do { r = ioctl(fd, req, arg); } while (r == -1 && errno == EINTR);
    return r;
}

// Verify the node is a real capture device (skips /dev/video1 metadata nodes).
int OpenCaptureDevice(const std::string& override_path,
                      std::string& out_path,
                      std::string& err) {
    std::vector<std::string> candidates;
    if (!override_path.empty()) {
        candidates.push_back(override_path);
    } else {
        for (int i = 0; i < 20; ++i) {
            char buf[32];
            std::snprintf(buf, sizeof(buf), "/dev/video%d", i);
            candidates.emplace_back(buf);
        }
    }

    for (const auto& node : candidates) {
        int fd = ::open(node.c_str(), O_RDWR | O_NONBLOCK);
        if (fd < 0) continue;

        struct v4l2_capability cap {};
        if (xioctl(fd, VIDIOC_QUERYCAP, &cap) < 0) { ::close(fd); continue; }

        const bool is_capture =
            (cap.capabilities & V4L2_CAP_VIDEO_CAPTURE) ||
            (cap.capabilities & V4L2_CAP_VIDEO_CAPTURE_MPLANE);
        const bool can_stream = (cap.capabilities & V4L2_CAP_STREAMING) != 0;

        if (!is_capture || !can_stream) { ::close(fd); continue; }

        out_path = node;
        return fd;
    }
    err = "no usable capture device found (/dev/video0..19)";
    return -1;
}

std::string FourccName(uint32_t v) {
    char s[5] = {
        char(v & 0xff), char((v >> 8) & 0xff),
        char((v >> 16) & 0xff), char((v >> 24) & 0xff), 0
    };
    return s;
}

// Ask the driver what it supports, then negotiate in preference order.
bool NegotiateFormat(int fd, Fmt& out, std::string& err) {
    std::vector<uint32_t> supported;
    {
        struct v4l2_fmtdesc d {};
        d.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        for (d.index = 0; xioctl(fd, VIDIOC_ENUM_FMT, &d) == 0; ++d.index) {
            supported.push_back(d.pixelformat);
        }
    }
    if (supported.empty()) { err = "device reports no capture formats"; return false; }

    auto has = [&](uint32_t f) {
        return std::find(supported.begin(), supported.end(), f) != supported.end();
    };

    std::vector<uint32_t> prefs;
    if (has(V4L2_PIX_FMT_MJPEG)) prefs.push_back(V4L2_PIX_FMT_MJPEG);
    if (has(V4L2_PIX_FMT_YUYV))  prefs.push_back(V4L2_PIX_FMT_YUYV);
    if (has(V4L2_PIX_FMT_NV12))  prefs.push_back(V4L2_PIX_FMT_NV12);
    if (prefs.empty()) prefs = supported;   // fall back to whatever it advertises

    const uint32_t sizes[][2] = {
        {1280, 720}, {960, 540}, {640, 480}, {640, 360}, {320, 240}
    };

    for (uint32_t pf : prefs) {
        for (auto& sz : sizes) {
            // Fresh struct per attempt — a failed S_FMT may leave fmt undefined
            struct v4l2_format fmt {};
            fmt.type                = V4L2_BUF_TYPE_VIDEO_CAPTURE;
            fmt.fmt.pix.width       = sz[0];
            fmt.fmt.pix.height      = sz[1];
            fmt.fmt.pix.pixelformat = pf;
            fmt.fmt.pix.field       = V4L2_FIELD_NONE;

            if (xioctl(fd, VIDIOC_S_FMT, &fmt) == 0 &&
                fmt.fmt.pix.pixelformat == pf) {
                out.pixfmt = fmt.fmt.pix.pixelformat;
                out.width  = fmt.fmt.pix.width;
                out.height = fmt.fmt.pix.height;
                return true;
            }
        }
    }

    std::string avail;
    for (uint32_t f : supported) {
        if (!avail.empty()) avail += ",";
        avail += FourccName(f);
    }
    err = "VIDIOC_S_FMT failed on every preferred format; "
          "device advertises: " + avail;
    return false;
}

std::string default_output_path() {
    const char* home = std::getenv("HOME");
    std::string dir = home ? std::string(home) + "/Pictures/PAI" : "/tmp/pai";
    struct stat st {};
    if (::stat(dir.c_str(), &st) < 0) {
        if (::mkdir(dir.c_str(), 0755) < 0 && errno != EEXIST) {
            dir = "/tmp/pai";
            ::mkdir(dir.c_str(), 0755);
        }
    }
    char buf[64];
    std::time_t t = std::time(nullptr);
    std::strftime(buf, sizeof(buf), "%Y%m%d_%H%M%S", std::localtime(&t));
    return dir + "/PAI_" + buf + ".jpg";
}

// Prevent shell injection via outputPath
std::string shellQuote(const std::string& s) {
    std::string out = "'";
    for (char c : s) {
        if (c == '\'') out += "'\\''";
        else out += c;
    }
    out += "'";
    return out;
}

}  // namespace

std::map<std::string, std::string> capture(const std::map<std::string, std::string>& params) {
    const std::string override_device =
        params.count("device") ? params.at("device") : "";
    const std::string out =
        (params.count("outputPath") && !params.at("outputPath").empty())
            ? params.at("outputPath")
            : default_output_path();

    std::string device, devErr;
    int fd = OpenCaptureDevice(override_device, device, devErr);
    if (fd < 0) throw std::runtime_error(devErr);

    Fmt chosen {};
    std::string fmtErr;
    if (!NegotiateFormat(fd, chosen, fmtErr)) {
        ::close(fd);
        throw std::runtime_error(fmtErr);
    }

    struct v4l2_requestbuffers req {};
    req.count  = 1;
    req.type   = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    req.memory = V4L2_MEMORY_MMAP;
    if (xioctl(fd, VIDIOC_REQBUFS, &req) < 0 || req.count < 1) {
        ::close(fd);
        throw std::runtime_error("VIDIOC_REQBUFS failed");
    }

    struct v4l2_buffer buf {};
    buf.type   = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    buf.memory = V4L2_MEMORY_MMAP;
    buf.index  = 0;
    if (xioctl(fd, VIDIOC_QUERYBUF, &buf) < 0) {
        ::close(fd);
        throw std::runtime_error("VIDIOC_QUERYBUF failed");
    }

    void* data = mmap(nullptr, buf.length,
                      PROT_READ | PROT_WRITE, MAP_SHARED, fd, buf.m.offset);
    if (data == MAP_FAILED) { ::close(fd); throw std::runtime_error("mmap failed"); }

    if (xioctl(fd, VIDIOC_QBUF, &buf) < 0) {
        munmap(data, buf.length); ::close(fd);
        throw std::runtime_error("VIDIOC_QBUF failed");
    }

    enum v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (xioctl(fd, VIDIOC_STREAMON, &type) < 0) {
        munmap(data, buf.length); ::close(fd);
        throw std::runtime_error("VIDIOC_STREAMON failed");
    }

    // Wait for a frame with a 5 s timeout so DQBUF can't hang forever.
    fd_set fds; FD_ZERO(&fds); FD_SET(fd, &fds);
    struct timeval tv { .tv_sec = 5, .tv_usec = 0 };
    int r = select(fd + 1, &fds, nullptr, nullptr, &tv);
    if (r <= 0) {
        xioctl(fd, VIDIOC_STREAMOFF, &type);
        munmap(data, buf.length); ::close(fd);
        throw std::runtime_error(
            r == 0 ? "DQBUF timeout (no frame in 5s)"
                   : "select() failed before DQBUF");
    }

    struct v4l2_buffer dq {};
    dq.type   = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    dq.memory = V4L2_MEMORY_MMAP;
    if (xioctl(fd, VIDIOC_DQBUF, &dq) < 0) {
        xioctl(fd, VIDIOC_STREAMOFF, &type);
        munmap(data, buf.length); ::close(fd);
        throw std::runtime_error("VIDIOC_DQBUF failed");
    }

    // ⭐ CRITICAL: copy the frame BEFORE munmap
    std::vector<char> frame(
        static_cast<const char*>(data),
        static_cast<const char*>(data) + dq.bytesused);

    xioctl(fd, VIDIOC_STREAMOFF, &type);
    munmap(data, buf.length);
    ::close(fd);

    const std::string raw = "/tmp/pai_camera_frame_"
                          + std::to_string(::getpid()) + ".raw";
    {
        FILE* f = std::fopen(raw.c_str(), "wb");
        if (!f) throw std::runtime_error("cannot write raw frame to " + raw);
        std::fwrite(frame.data(), 1, frame.size(), f);
        std::fclose(f);
    }

    const bool native_jpeg = (chosen.pixfmt == V4L2_PIX_FMT_MJPEG);
    int rc = 0;
    if (native_jpeg) {
        std::ostringstream cmd;
        cmd << "cp " << shellQuote(raw) << " " << shellQuote(out);
        rc = std::system(cmd.str().c_str());
    } else {
        const char* pix_fmt =
            (chosen.pixfmt == V4L2_PIX_FMT_YUYV) ? "yuyv422" : "nv12";
        std::ostringstream cmd;
        cmd << "ffmpeg -y -loglevel error -f rawvideo"
            << " -pix_fmt " << pix_fmt
            << " -s " << chosen.width << "x" << chosen.height
            << " -i " << shellQuote(raw)
            << " -frames:v 1 " << shellQuote(out);
        rc = std::system(cmd.str().c_str());
    }
    std::remove(raw.c_str());
    if (rc != 0) throw std::runtime_error(
        "JPEG conversion failed (native=" +
        std::string(native_jpeg ? "yes" : "no") + ")");

    struct stat st {};
    ::stat(out.c_str(), &st);

    return {
        {"ok", "true"},
        {"path", out},
        {"device", device},
        {"sizeBytes", std::to_string(st.st_size)},
        {"width", std::to_string(chosen.width)},
        {"height", std::to_string(chosen.height)},
        {"pixelFormat", FourccName(chosen.pixfmt)},
    };
}

}  // namespace pai::camera
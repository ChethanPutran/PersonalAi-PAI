#include "camera_capture.h"

#include <fcntl.h>
#include <linux/videodev2.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <dirent.h>
#include <sstream>
#include <stdexcept>

namespace pai::camera {

namespace {

std::string find_camera_device() {
    DIR* d = opendir("/dev");
    if (!d) return {};
    std::string found;
    struct dirent* e;
    while ((e = readdir(d)) != nullptr) {
        if (std::strncmp(e->d_name, "video", 5) == 0) {
            found = std::string("/dev/") + e->d_name;
            break;
        }
    }
    closedir(d);
    return found;
}

std::string default_output_path() {
    const char* home = std::getenv("HOME");
    std::string dir = home ? std::string(home) + "/Pictures/PAI" : "/tmp/pai";
    ::mkdir(dir.c_str(), 0755);
    char buf[64];
    std::time_t t = std::time(nullptr);
    std::strftime(buf, sizeof(buf), "%Y%m%d_%H%M%S", std::localtime(&t));
    return dir + "/PAI_" + buf + ".jpg";
}

int xioctl(int fd, unsigned long req, void* arg) {
    int r;
    do { r = ioctl(fd, req, arg); } while (r == -1 && errno == EINTR);
    return r;
}

}  // namespace

std::map<std::string, std::string> capture(const std::map<std::string, std::string>& params) {
    const std::string device = params.count("device") ? params.at("device") : find_camera_device();
    if (device.empty()) throw std::runtime_error("no /dev/video* device found");

    const std::string out = params.count("outputPath") ? params.at("outputPath") : default_output_path();

    int fd = ::open(device.c_str(), O_RDWR);
    if (fd < 0) throw std::runtime_error("cannot open " + device);

    v4l2_format fmt{};
    fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    fmt.fmt.pix.width = 1280;
    fmt.fmt.pix.height = 720;
    fmt.fmt.pix.pixelformat = V4L2_PIX_FMT_MJPEG;
    fmt.fmt.pix.field = V4L2_FIELD_NONE;

    if (xioctl(fd, VIDIOC_S_FMT, &fmt) < 0) {
        fmt.fmt.pix.pixelformat = V4L2_PIX_FMT_YUYV;
        if (xioctl(fd, VIDIOC_S_FMT, &fmt) < 0) {
            ::close(fd);
            throw std::runtime_error("VIDIOC_S_FMT failed");
        }
    }

    v4l2_requestbuffers req{};
    req.count = 1;
    req.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    req.memory = V4L2_MEMORY_MMAP;
    if (xioctl(fd, VIDIOC_REQBUFS, &req) < 0) {
        ::close(fd);
        throw std::runtime_error("VIDIOC_REQBUFS failed");
    }

    v4l2_buffer buf{};
    buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    buf.memory = V4L2_MEMORY_MMAP;
    buf.index = 0;
    if (xioctl(fd, VIDIOC_QUERYBUF, &buf) < 0) {
        ::close(fd);
        throw std::runtime_error("VIDIOC_QUERYBUF failed");
    }

    void* data = mmap(nullptr, buf.length, PROT_READ | PROT_WRITE, MAP_SHARED, fd, buf.m.offset);
    if (data == MAP_FAILED) {
        ::close(fd);
        throw std::runtime_error("mmap failed");
    }

    if (xioctl(fd, VIDIOC_QBUF, &buf) < 0) {
        munmap(data, buf.length);
        ::close(fd);
        throw std::runtime_error("VIDIOC_QBUF failed");
    }

    v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (xioctl(fd, VIDIOC_STREAMON, &type) < 0) {
        munmap(data, buf.length);
        ::close(fd);
        throw std::runtime_error("VIDIOC_STREAMON failed");
    }

    v4l2_buffer dq{};
    dq.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    dq.memory = V4L2_MEMORY_MMAP;
    if (xioctl(fd, VIDIOC_DQBUF, &dq) < 0) {
        xioctl(fd, VIDIOC_STREAMOFF, &type);
        munmap(data, buf.length);
        ::close(fd);
        throw std::runtime_error("VIDIOC_DQBUF failed");
    }

    xioctl(fd, VIDIOC_STREAMOFF, &type);
    munmap(data, buf.length);
    ::close(fd);

    const std::string raw = "/tmp/pai_camera_frame.raw";
    FILE* f = std::fopen(raw.c_str(), "wb");
    if (!f) throw std::runtime_error("cannot write raw frame");
    std::fwrite(data, 1, dq.bytesused, f);
    std::fclose(f);

    std::ostringstream cmd;
    bool native_jpeg = (fmt.fmt.pix.pixelformat == V4L2_PIX_FMT_MJPEG);
    if (native_jpeg) {
        cmd << "cp " << raw << " " << out;
    } else {
        cmd << "ffmpeg -y -loglevel error -f rawvideo"
            << " -pix_fmt yuyv422 -s " << fmt.fmt.pix.width << "x" << fmt.fmt.pix.height
            << " -i " << raw << " -frames:v 1 " << out;
    }

    int rc = std::system(cmd.str().c_str());
    std::remove(raw.c_str());
    if (rc != 0) throw std::runtime_error("ffmpeg transcode failed");

    struct stat st{};
    ::stat(out.c_str(), &st);

    return {
        {"ok", "true"},
        {"path", out},
        {"sizeBytes", std::to_string(st.st_size)},
        {"width", std::to_string(fmt.fmt.pix.width)},
        {"height", std::to_string(fmt.fmt.pix.height)},
    };
}

}  // namespace pai::camera
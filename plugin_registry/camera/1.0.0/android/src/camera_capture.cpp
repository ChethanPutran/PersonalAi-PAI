#include "camera_capture.h"

#include <android/log.h>
#include <camera/NdkCameraDevice.h>
#include <camera/NdkCameraManager.h>
#include <camera/NdkCameraCaptureSession.h>
#include <media/NdkImage.h>
#include <media/NdkImageReader.h>

#include <cstdio>
#include <cstring>
#include <ctime>
#include <mutex>
#include <stdexcept>
#include <condition_variable>
#include <thread>
#include <chrono>

#define LOG_TAG "PAI-Camera"
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

namespace pai::camera {

namespace {

struct State {
    std::mutex mtx;
    std::condition_variable cv;
    bool got_image = false;
    std::string path;
    int width = 0;
    int height = 0;
    int error = 0;
};

std::string default_output_path() {
    // /data/local/tmp is always writable by the shell user;
    // the host typically passes an explicit outputPath anyway.
    char buf[96];
    std::time_t t = std::time(nullptr);
    std::strftime(buf, sizeof(buf), "/data/local/tmp/PAI_%Y%m%d_%H%M%S.jpg",
                  std::localtime(&t));
    return std::string(buf);
}

void OnImageAvailable(void* ctx, AImageReader* reader) {
    auto* state = static_cast<State*>(ctx);

    AImage* image = nullptr;
    if (AImageReader_acquireNextImage(reader, &image) != AMEDIA_OK || image == nullptr) {
        return;
    }

    int32_t width = 0, height = 0;
    AImage_getWidth(image, &width);
    AImage_getHeight(image, &height);

    uint8_t* data = nullptr;
    int32_t len = 0;
    // JPEG images have one plane (index 0).
    media_status_t st = AImage_getPlaneData(image, 0, &data, &len);
    if (st != AMEDIA_OK || data == nullptr || len <= 0) {
        AImage_delete(image);
        return;
    }

    std::string path;
    {
        std::lock_guard<std::mutex> lk(state->mtx);
        path = state->path;
        state->width = width;
        state->height = height;
    }

    FILE* f = std::fopen(path.c_str(), "wb");
    if (f) {
        std::fwrite(data, 1, len, f);
        std::fclose(f);
        std::lock_guard<std::mutex> lk(state->mtx);
        state->got_image = true;
    } else {
        std::lock_guard<std::mutex> lk(state->mtx);
        state->error = 2;
    }
    state->cv.notify_all();

    AImage_delete(image);
}

}  // namespace

std::map<std::string, std::string> capture(const std::map<std::string, std::string>& params) {
    const std::string out_path =
        params.count("outputPath") ? params.at("outputPath") : default_output_path();
    const std::string lens =
        params.count("lens") ? params.at("lens") : "back";

    State state;
    state.path = out_path;

    ACameraManager* mgr = ACameraManager_create();
    if (!mgr) throw std::runtime_error("ACameraManager_create failed");

    ACameraIdList* ids = nullptr;
    if (ACameraManager_getCameraIdList(mgr, &ids) != ACAMERA_OK || ids == nullptr) {
        ACameraManager_delete(mgr);
        throw std::runtime_error("no camera id list");
    }

    // Front cameras are usually "1", back are usually "0".
    const char* camera_id = nullptr;
    if (ids->numCameras >= 1) {
        if (lens == "front" && ids->numCameras >= 2) {
            camera_id = ids->cameraIds[1];
        } else {
            camera_id = ids->cameraIds[0];
        }
    }
    if (!camera_id) {
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error("no camera available");
    }

    ACameraDevice* device = nullptr;
    if (ACameraManager_openCamera(mgr, camera_id, nullptr, &device) != ACAMERA_OK) {
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error("failed to open camera");
    }

    // AImageReader with JPEG output.
    AImageReader* reader = nullptr;
    if (AImageReader_new(1280, 720, AIMAGE_FORMAT_JPEG, 2, &reader) != AMEDIA_OK) {
        ACameraDevice_close(device);
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error("AImageReader_new failed");
    }

    AImageReader_ImageListener listener{};
    listener.context = &state;
    listener.onImageAvailable = OnImageAvailable;
    AImageReader_setImageListener(reader, &listener);

    ANativeWindow* window = nullptr;
    AImageReader_getWindow(reader, &window);

    ACaptureSessionOutputContainer* outputs = nullptr;
    ACaptureSessionOutputContainer_create(&outputs);

    ACaptureSessionOutput* session_output = nullptr;
    ACaptureSessionOutput_create(window, &session_output);
    ACaptureSessionOutputContainer_add(outputs, session_output);

    ACameraOutputTarget* target = nullptr;
    ACameraOutputTarget_create(window, &target);

    ACameraDevice_StateCallbacks device_callbacks{};
    // Not wiring device callbacks for this minimal version.

    ACameraCaptureSession* session = nullptr;
    ACameraCaptureSession_stateCallbacks session_callbacks{};
    session_callbacks.context = nullptr;

    if (ACameraDevice_createCaptureSession(
            device, outputs, &session_callbacks, &session) != ACAMERA_OK) {
        // Cleanup on failure
        ACameraOutputTarget_free(target);
        ACaptureSessionOutput_free(session_output);
        ACaptureSessionOutputContainer_free(outputs);
        AImageReader_delete(reader);
        ACameraDevice_close(device);
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error("createCaptureSession failed");
    }

    ACaptureRequest* request = nullptr;
    ACameraDevice_createCaptureRequest(device, TEMPLATE_STILL_CAPTURE, &request);

    ACaptureRequest_addTarget(request, target);

    // Fire the capture.
    ACameraCaptureSession_capture(session, nullptr, 1, &request, nullptr);

    // Wait up to 10 seconds.
    {
        std::unique_lock<std::mutex> lk(state.mtx);
        state.cv.wait_for(lk, std::chrono::seconds(10),
                          [&] { return state.got_image || state.error != 0; });
    }

    const bool ok = state.got_image;
    const int width = state.width;
    const int height = state.height;

    // Cleanup
    ACaptureRequest_free(request);
    ACameraCaptureSession_close(session);
    ACameraOutputTarget_free(target);
    ACaptureSessionOutput_free(session_output);
    ACaptureSessionOutputContainer_free(outputs);
    AImageReader_delete(reader);
    ACameraDevice_close(device);
    ACameraManager_deleteCameraIdList(ids);
    ACameraManager_delete(mgr);

    if (!ok) throw std::runtime_error("capture timed out or failed");

    std::map<std::string, std::string> result = {
        {"ok", "true"},
        {"path", out_path},
        {"width", std::to_string(width)},
        {"height", std::to_string(height)},
    };
    return result;
}

}  // namespace pai::camera
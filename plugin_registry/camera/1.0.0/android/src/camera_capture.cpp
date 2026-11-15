#include "camera_capture.h"

#include <android/log.h>
#include <camera/NdkCameraDevice.h>
#include <camera/NdkCameraManager.h>
#include <camera/NdkCameraCaptureSession.h>
#include <camera/NdkCameraMetadata.h>
#include <media/NdkImage.h>
#include <media/NdkImageReader.h>

#include <cerrno>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <map>
#include <mutex>
#include <stdexcept>
#include <string>

#define LOG_TAG "PAI-Camera"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO,  LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

namespace pai::camera {
namespace {

struct State {
    std::mutex mtx;
    std::condition_variable cv;
    bool got_image = false;
    bool session_ready = false;
    std::string path;
    int width = 0;
    int height = 0;
    int error = 0;
};

std::string default_output_path() {
    const char* tmp = std::getenv("TMPDIR");
    std::string dir = (tmp && *tmp) ? std::string(tmp) : "/data/local/tmp";
    char ts[32];
    std::time_t t = std::time(nullptr);
    std::strftime(ts, sizeof(ts), "%Y%m%d_%H%M%S", std::localtime(&t));
    return dir + "/PAI_" + ts + ".jpg";
}

void OnImageAvailable(void* ctx, AImageReader* reader) {
    auto* state = static_cast<State*>(ctx);

    AImage* image = nullptr;
    if (AImageReader_acquireNextImage(reader, &image) != AMEDIA_OK || image == nullptr) {
        LOGE("acquireNextImage failed");
        return;
    }

    int32_t width = 0, height = 0;
    AImage_getWidth(image, &width);
    AImage_getHeight(image, &height);

    uint8_t* data = nullptr;
    int32_t len = 0;
    media_status_t st = AImage_getPlaneData(image, 0, &data, &len);
    if (st != AMEDIA_OK || data == nullptr || len <= 0) {
        LOGE("getPlaneData failed: %d", static_cast<int>(st));
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
        LOGI("Wrote %d bytes to %s", len, path.c_str());
        std::lock_guard<std::mutex> lk(state->mtx);
        state->got_image = true;
    } else {
        LOGE("fopen(%s) failed: %s", path.c_str(), std::strerror(errno));
        std::lock_guard<std::mutex> lk(state->mtx);
        state->error = 2;
    }
    state->cv.notify_all();

    AImage_delete(image);
}

void OnDeviceDisconnected(void* /*ctx*/, ACameraDevice* /*device*/) {
    LOGE("Camera device disconnected");
}

void OnDeviceError(void* ctx, ACameraDevice* /*device*/, int error) {
    LOGE("Camera device error %d", error);
    if (!ctx) return;
    auto* state = static_cast<State*>(ctx);
    {
        std::lock_guard<std::mutex> lk(state->mtx);
        if (state->error == 0) state->error = error;
    }
    state->cv.notify_all();
}

void OnSessionClosed(void* /*ctx*/, ACameraCaptureSession* /*session*/) {}

void OnSessionReady(void* ctx, ACameraCaptureSession* /*session*/) {
    LOGI("onReady fired");
    if (!ctx) return;
    auto* state = static_cast<State*>(ctx);
    {
        std::lock_guard<std::mutex> lk(state->mtx);
        state->session_ready = true;
    }
    state->cv.notify_all();
}

std::string pick_camera_id(
    ACameraManager* mgr,
    ACameraIdList* ids,
    const std::string& lens) {

    const int32_t want_facing = (lens == "front")
        ? ACAMERA_LENS_FACING_FRONT
        : ACAMERA_LENS_FACING_BACK;

    for (int i = 0; i < ids->numCameras; ++i) {
        const char* id = ids->cameraIds[i];
        ACameraMetadata* chars = nullptr;
        if (ACameraManager_getCameraCharacteristics(mgr, id, &chars) != ACAMERA_OK) {
            continue;
        }
        ACameraMetadata_const_entry entry{};
        bool match = false;
        if (ACameraMetadata_getConstEntry(chars, ACAMERA_LENS_FACING, &entry) == ACAMERA_OK
            && entry.type == ACAMERA_TYPE_INT32
            && entry.count >= 1) {
            match = (entry.data.i32[0] == want_facing);
        }
        ACameraMetadata_free(chars);
        if (match) return std::string(id);
    }

    if (ids->numCameras > 0) return std::string(ids->cameraIds[0]);
    return "";
}

}  // namespace

std::map<std::string, std::string> capture(const std::map<std::string, std::string>& params) {
    const std::string override_path =
        params.count("outputPath") ? params.at("outputPath") : "";
    const std::string out_path =
        override_path.empty() ? default_output_path() : override_path;

    const std::string lens =
        (params.count("lens") && !params.at("lens").empty())
            ? params.at("lens") : "back";

    LOGI("capture: path=%s lens=%s", out_path.c_str(), lens.c_str());

    State state;
    state.path = out_path;

    ACameraManager* mgr = ACameraManager_create();
    if (!mgr) throw std::runtime_error("ACameraManager_create failed");

    ACameraIdList* ids = nullptr;
    if (ACameraManager_getCameraIdList(mgr, &ids) != ACAMERA_OK || ids == nullptr) {
        ACameraManager_delete(mgr);
        throw std::runtime_error("no camera id list");
    }

    const std::string camera_id = pick_camera_id(mgr, ids, lens);
    if (camera_id.empty()) {
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error("no camera available");
    }

    ACameraDevice_StateCallbacks device_callbacks{};
    device_callbacks.context        = &state;
    device_callbacks.onDisconnected = OnDeviceDisconnected;
    device_callbacks.onError        = OnDeviceError;

    ACameraDevice* device = nullptr;
    camera_status_t st = ACameraManager_openCamera(
        mgr, camera_id.c_str(), &device_callbacks, &device);
    if (st != ACAMERA_OK) {
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error(
            "ACameraManager_openCamera failed: " + std::to_string(static_cast<int>(st)));
    }

    // maxImages 4 (was 2) — the emulator's camera HAL hangs the session
    // creation on some images when maxImages < 3.
    AImageReader* reader = nullptr;
    if (AImageReader_new(1280, 720, AIMAGE_FORMAT_JPEG, 4, &reader) != AMEDIA_OK) {
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
    if (AImageReader_getWindow(reader, &window) != AMEDIA_OK || window == nullptr) {
        AImageReader_delete(reader);
        ACameraDevice_close(device);
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error("AImageReader_getWindow returned null");
    }

    ACaptureSessionOutputContainer* outputs = nullptr;
    ACaptureSessionOutputContainer_create(&outputs);

    ACaptureSessionOutput* session_output = nullptr;
    ACaptureSessionOutput_create(window, &session_output);
    ACaptureSessionOutputContainer_add(outputs, session_output);

    ACameraOutputTarget* target = nullptr;
    ACameraOutputTarget_create(window, &target);

    ACameraCaptureSession_stateCallbacks session_callbacks{};
    session_callbacks.context  = &state;
    session_callbacks.onClosed = OnSessionClosed;
    session_callbacks.onReady  = OnSessionReady;

    ACameraCaptureSession* session = nullptr;
    st = ACameraDevice_createCaptureSession(
        device, outputs, &session_callbacks, &session);
    if (st != ACAMERA_OK) {
        ACameraOutputTarget_free(target);
        ACaptureSessionOutput_free(session_output);
        ACaptureSessionOutputContainer_free(outputs);
        AImageReader_delete(reader);
        ACameraDevice_close(device);
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error(
            "createCaptureSession failed: " + std::to_string(static_cast<int>(st)));
    }

    // Wait for session READY.
    //
    // 15s (was 5s) — the emulator's ranchu HAL cold-start can take
    // 10-15s on first session creation. On subsequent runs it's 1-2s.
    //
    // If onReady still didn't fire, do NOT hard-fail here. Several HALs
    // (including the emulator under some load conditions) skip the
    // callback entirely but still accept setRepeatingRequest below.
    // Let that be the real validation — if the session isn't usable,
    // setRepeatingRequest returns the actual error code.
    {
        std::unique_lock<std::mutex> lk(state.mtx);
        state.cv.wait_for(
            lk, std::chrono::seconds(15),
            [&] { return state.session_ready || state.error != 0; });

        if (state.error != 0) {
            const int err = state.error;
            lk.unlock();
            ACameraCaptureSession_close(session);
            ACameraOutputTarget_free(target);
            ACaptureSessionOutput_free(session_output);
            ACaptureSessionOutputContainer_free(outputs);
            AImageReader_delete(reader);
            ACameraDevice_close(device);
            ACameraManager_deleteCameraIdList(ids);
            ACameraManager_delete(mgr);
            throw std::runtime_error(
                "camera error before capture: " + std::to_string(err));
        }
        if (!state.session_ready) {
            LOGI("WARNING: onReady did not fire in 15s — "
                 "proceeding; setRepeatingRequest will validate the session");
            // fall through, no throw
        }
    }

    ACaptureRequest* request = nullptr;
    st = ACameraDevice_createCaptureRequest(
        device, TEMPLATE_STILL_CAPTURE, &request);
    if (st != ACAMERA_OK) {
        ACameraCaptureSession_close(session);
        ACameraOutputTarget_free(target);
        ACaptureSessionOutput_free(session_output);
        ACaptureSessionOutputContainer_free(outputs);
        AImageReader_delete(reader);
        ACameraDevice_close(device);
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error(
            "createCaptureRequest failed: " + std::to_string(static_cast<int>(st)));
    }

    ACaptureRequest_addTarget(request, target);

    int32_t orientation = 90;
    ACaptureRequest_setEntry_i32(request, ACAMERA_JPEG_ORIENTATION, 1, &orientation);

    // Submit the capture. This is also the true "is the session alive" test
    // when onReady was skipped.
    st = ACameraCaptureSession_capture(session, nullptr, 1, &request, nullptr);
    if (st != ACAMERA_OK) {
        ACaptureRequest_free(request);
        ACameraCaptureSession_close(session);
        ACameraOutputTarget_free(target);
        ACaptureSessionOutput_free(session_output);
        ACaptureSessionOutputContainer_free(outputs);
        AImageReader_delete(reader);
        ACameraDevice_close(device);
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error(
            "ACameraCaptureSession_capture failed: "
            + std::to_string(static_cast<int>(st))
            + " (session was NOT ready after 15s either)");
    }

    // Wait up to 10s for the image callback.
    {
        std::unique_lock<std::mutex> lk(state.mtx);
        state.cv.wait_for(
            lk, std::chrono::seconds(10),
            [&] { return state.got_image || state.error != 0; });
    }

    const bool ok = state.got_image;
    const int width = state.width;
    const int height = state.height;
    const int err = state.error;

    ACameraCaptureSession_close(session);
    ACaptureRequest_free(request);
    ACameraOutputTarget_free(target);
    ACaptureSessionOutput_free(session_output);
    ACaptureSessionOutputContainer_free(outputs);
    AImageReader_delete(reader);
    ACameraDevice_close(device);
    ACameraManager_deleteCameraIdList(ids);
    ACameraManager_delete(mgr);

    if (!ok) {
        throw std::runtime_error(
            err != 0
                ? "capture failed with camera error " + std::to_string(err)
                : "capture timed out waiting for image");
    }

    std::map<std::string, std::string> result = {
        {"ok", "true"},
        {"path", out_path},
        {"width", std::to_string(width)},
        {"height", std::to_string(height)},
    };
    return result;
}

}  // namespace pai::camera

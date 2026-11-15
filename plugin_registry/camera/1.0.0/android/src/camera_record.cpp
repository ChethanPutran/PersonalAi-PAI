#include "camera_record.h"

#include <android/log.h>
#include <camera/NdkCameraCaptureSession.h>
#include <camera/NdkCameraDevice.h>
#include <camera/NdkCameraManager.h>
#include <camera/NdkCameraMetadata.h>
#include <media/NdkMediaCodec.h>
#include <media/NdkMediaFormat.h>
#include <media/NdkMediaMuxer.h>

#include <cerrno>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fcntl.h>
#include <map>
#include <mutex>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>

#define LOG_TAG "PAI-Camera"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO,  LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

#ifndef COLOR_FormatSurface
#define COLOR_FormatSurface 0x7F000789
#endif

namespace pai::camera {
namespace {

struct RecordState {
    std::mutex mtx;
    std::condition_variable cv;
    bool session_ready = false;
    int error = 0;
};

void OnDeviceDisconnected(void* /*ctx*/, ACameraDevice* /*device*/) {
    LOGE("camera device disconnected during recording");
}

void OnDeviceError(void* ctx, ACameraDevice* /*device*/, int error) {
    LOGE("camera device error during recording: %d", error);
    if (!ctx) return;
    auto* s = static_cast<RecordState*>(ctx);
    {
        std::lock_guard<std::mutex> lk(s->mtx);
        s->error = error;
    }
    s->cv.notify_all();
}

void OnSessionReady(void* ctx, ACameraCaptureSession* /*session*/) {
    if (!ctx) return;
    auto* s = static_cast<RecordState*>(ctx);
    {
        std::lock_guard<std::mutex> lk(s->mtx);
        s->session_ready = true;
    }
    s->cv.notify_all();
}

void OnSessionClosed(void* /*ctx*/, ACameraCaptureSession* /*session*/) {}

std::string default_output_path() {
    const char* tmp = std::getenv("TMPDIR");
    std::string dir = (tmp && *tmp) ? std::string(tmp) : "/data/local/tmp";
    char ts[32];
    std::time_t t = std::time(nullptr);
    std::strftime(ts, sizeof(ts), "%Y%m%d_%H%M%S", std::localtime(&t));
    return dir + "/PAI_" + ts + ".mp4";
}

AMediaFormat* make_video_format(int width, int height, int bitrate, int fps) {
    AMediaFormat* fmt = AMediaFormat_new();
    AMediaFormat_setString(fmt, AMEDIAFORMAT_KEY_MIME, "video/avc");
    AMediaFormat_setInt32(fmt, AMEDIAFORMAT_KEY_WIDTH,  width);
    AMediaFormat_setInt32(fmt, AMEDIAFORMAT_KEY_HEIGHT, height);
    AMediaFormat_setInt32(fmt, AMEDIAFORMAT_KEY_BIT_RATE, bitrate);
    AMediaFormat_setInt32(fmt, AMEDIAFORMAT_KEY_FRAME_RATE, fps);
    AMediaFormat_setInt32(fmt, AMEDIAFORMAT_KEY_I_FRAME_INTERVAL, 1);
    AMediaFormat_setInt32(fmt, AMEDIAFORMAT_KEY_COLOR_FORMAT,
                          COLOR_FormatSurface);
    return fmt;
}

std::string pick_camera_id(ACameraManager* mgr, ACameraIdList* ids, int32_t want) {
    for (int i = 0; i < ids->numCameras; ++i) {
        ACameraMetadata* chars = nullptr;
        if (ACameraManager_getCameraCharacteristics(
                mgr, ids->cameraIds[i], &chars) != ACAMERA_OK) {
            continue;
        }
        ACameraMetadata_const_entry e{};
        bool match = false;
        if (ACameraMetadata_getConstEntry(chars, ACAMERA_LENS_FACING, &e) == ACAMERA_OK
            && e.type == ACAMERA_TYPE_INT32
            && e.count >= 1) {
            match = (e.data.i32[0] == want);
        }
        ACameraMetadata_free(chars);
        if (match) return std::string(ids->cameraIds[i]);
    }
    return ids->numCameras > 0 ? std::string(ids->cameraIds[0]) : "";
}

}  // namespace


std::map<std::string, std::string> record(
    const std::map<std::string, std::string>& params) {

    const std::string override_path =
        params.count("outputPath") ? params.at("outputPath") : "";
    const std::string out_path =
        override_path.empty() ? default_output_path() : override_path;

    int duration_ms = 5000;
    if (params.count("durationMs") && !params.at("durationMs").empty()) {
        try { duration_ms = std::stoi(params.at("durationMs")); }
        catch (...) { duration_ms = 5000; }
    }
    if (duration_ms <= 0)      duration_ms = 5000;
    if (duration_ms > 120000)  duration_ms = 120000;

    bool audio = true;
    if (params.count("audio")) {
        audio = (params.at("audio") == "true" || params.at("audio") == "1");
    }

    const std::string lens =
        (params.count("lens") && !params.at("lens").empty())
            ? params.at("lens") : "back";

    LOGI("record: path=%s duration=%dms audio=%d lens=%s",
         out_path.c_str(), duration_ms, audio ? 1 : 0, lens.c_str());

    if (audio) {
        return {
            {"ok", "false"},
            {"error", "audio_not_supported_yet"},
            {"hint", "pass audio=false for video-only recording"},
        };
    }

    RecordState state;

    ACameraManager* mgr = ACameraManager_create();
    if (!mgr) throw std::runtime_error("ACameraManager_create failed");

    ACameraIdList* ids = nullptr;
    if (ACameraManager_getCameraIdList(mgr, &ids) != ACAMERA_OK || !ids) {
        ACameraManager_delete(mgr);
        throw std::runtime_error("no camera id list");
    }

    const int32_t want = (lens == "front")
        ? ACAMERA_LENS_FACING_FRONT : ACAMERA_LENS_FACING_BACK;
    const std::string camera_id = pick_camera_id(mgr, ids, want);
    if (camera_id.empty()) {
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error("no camera available");
    }

    ACameraDevice_StateCallbacks device_cb{};
    device_cb.context        = &state;
    device_cb.onDisconnected = OnDeviceDisconnected;
    device_cb.onError        = OnDeviceError;

    ACameraDevice* device = nullptr;
    camera_status_t cam_st = ACameraManager_openCamera(
        mgr, camera_id.c_str(), &device_cb, &device);
    if (cam_st != ACAMERA_OK) {
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error(
            "openCamera failed: " + std::to_string(static_cast<int>(cam_st)));
    }

    constexpr int kWidth   = 1280;
    constexpr int kHeight  = 720;
    constexpr int kBitrate = 5'000'000;
    constexpr int kFps     = 30;

    AMediaFormat* vfmt = make_video_format(kWidth, kHeight, kBitrate, kFps);

    AMediaCodec* encoder = AMediaCodec_createEncoderByType("video/avc");
    if (!encoder) {
        AMediaFormat_delete(vfmt);
        ACameraDevice_close(device);
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error("AMediaCodec_createEncoderByType failed");
    }

    media_status_t mst = AMediaCodec_configure(
        encoder, vfmt, nullptr, nullptr, AMEDIACODEC_CONFIGURE_FLAG_ENCODE);
    if (mst != AMEDIA_OK) {
        AMediaCodec_delete(encoder);
        AMediaFormat_delete(vfmt);
        ACameraDevice_close(device);
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error(
            "encoder configure failed: " + std::to_string(static_cast<int>(mst)));
    }

    ANativeWindow* input_window = nullptr;
    mst = AMediaCodec_createInputSurface(encoder, &input_window);
    if (mst != AMEDIA_OK || !input_window) {
        AMediaCodec_delete(encoder);
        AMediaFormat_delete(vfmt);
        ACameraDevice_close(device);
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error("createInputSurface failed");
    }

    mst = AMediaCodec_start(encoder);
    if (mst != AMEDIA_OK) {
        ANativeWindow_release(input_window);
        AMediaCodec_delete(encoder);
        AMediaFormat_delete(vfmt);
        ACameraDevice_close(device);
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error("encoder start failed");
    }

    const int out_fd = ::open(out_path.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (out_fd < 0) {
        AMediaCodec_stop(encoder);
        AMediaCodec_delete(encoder);
        ANativeWindow_release(input_window);
        AMediaFormat_delete(vfmt);
        ACameraDevice_close(device);
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error(
            "cannot open " + out_path + ": " + std::strerror(errno));
    }

    AMediaMuxer* muxer = AMediaMuxer_new(out_fd, AMEDIAMUXER_OUTPUT_FORMAT_MPEG_4);
    if (!muxer) {
        ::close(out_fd);
        AMediaCodec_stop(encoder);
        AMediaCodec_delete(encoder);
        ANativeWindow_release(input_window);
        AMediaFormat_delete(vfmt);
        ACameraDevice_close(device);
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error("AMediaMuxer_new failed");
    }

    ACaptureSessionOutputContainer* outputs = nullptr;
    ACaptureSessionOutputContainer_create(&outputs);

    ACaptureSessionOutput* session_output = nullptr;
    ACaptureSessionOutput_create(input_window, &session_output);
    ACaptureSessionOutputContainer_add(outputs, session_output);

    ACameraOutputTarget* target = nullptr;
    ACameraOutputTarget_create(input_window, &target);

    ACameraCaptureSession_stateCallbacks session_cb{};
    session_cb.context  = &state;
    session_cb.onClosed = OnSessionClosed;
    session_cb.onReady  = OnSessionReady;

    ACameraCaptureSession* session = nullptr;
    cam_st = ACameraDevice_createCaptureSession(
        device, outputs, &session_cb, &session);
    if (cam_st != ACAMERA_OK) {
        AMediaMuxer_delete(muxer);
        ::close(out_fd);
        ACameraOutputTarget_free(target);
        ACaptureSessionOutput_free(session_output);
        ACaptureSessionOutputContainer_free(outputs);
        AMediaCodec_stop(encoder);
        AMediaCodec_delete(encoder);
        ANativeWindow_release(input_window);
        AMediaFormat_delete(vfmt);
        ACameraDevice_close(device);
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error(
            "createCaptureSession failed: " + std::to_string(static_cast<int>(cam_st)));
    }

    {
        std::unique_lock<std::mutex> lk(state.mtx);
        state.cv.wait_for(lk, std::chrono::seconds(5),
                          [&] { return state.session_ready || state.error != 0; });
        if (!state.session_ready) {
            ACameraCaptureSession_close(session);
            AMediaMuxer_delete(muxer);
            ::close(out_fd);
            ACameraOutputTarget_free(target);
            ACaptureSessionOutput_free(session_output);
            ACaptureSessionOutputContainer_free(outputs);
            AMediaCodec_stop(encoder);
            AMediaCodec_delete(encoder);
            ANativeWindow_release(input_window);
            AMediaFormat_delete(vfmt);
            ACameraDevice_close(device);
            ACameraManager_deleteCameraIdList(ids);
            ACameraManager_delete(mgr);
            throw std::runtime_error(
                state.error != 0
                    ? "camera error before capture: " + std::to_string(state.error)
                    : "session never became ready");
        }
    }

    ACaptureRequest* request = nullptr;
    cam_st = ACameraDevice_createCaptureRequest(
        device, TEMPLATE_RECORD, &request);
    if (cam_st != ACAMERA_OK) {
        ACameraCaptureSession_close(session);
        AMediaMuxer_delete(muxer);
        ::close(out_fd);
        ACameraOutputTarget_free(target);
        ACaptureSessionOutput_free(session_output);
        ACaptureSessionOutputContainer_free(outputs);
        AMediaCodec_stop(encoder);
        AMediaCodec_delete(encoder);
        ANativeWindow_release(input_window);
        AMediaFormat_delete(vfmt);
        ACameraDevice_close(device);
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error("createCaptureRequest failed");
    }

    ACaptureRequest_addTarget(request, target);

    cam_st = ACameraCaptureSession_setRepeatingRequest(
        session, nullptr, 1, &request, nullptr);
    if (cam_st != ACAMERA_OK) {
        ACaptureRequest_free(request);
        ACameraCaptureSession_close(session);
        AMediaMuxer_delete(muxer);
        ::close(out_fd);
        ACameraOutputTarget_free(target);
        ACaptureSessionOutput_free(session_output);
        ACaptureSessionOutputContainer_free(outputs);
        AMediaCodec_stop(encoder);
        AMediaCodec_delete(encoder);
        ANativeWindow_release(input_window);
        AMediaFormat_delete(vfmt);
        ACameraDevice_close(device);
        ACameraManager_deleteCameraIdList(ids);
        ACameraManager_delete(mgr);
        throw std::runtime_error(
            "setRepeatingRequest failed: " + std::to_string(static_cast<int>(cam_st)));
    }

    LOGI("Recording started, duration=%dms", duration_ms);

    bool     muxer_started = false;
    ssize_t  video_track   = -1;
    int64_t  first_pts     = -1;
    int64_t  frames        = 0;
    AMediaCodecBufferInfo info{};

    auto drain = [&](int64_t timeout_us) -> bool {
        while (true) {
            ssize_t idx = AMediaCodec_dequeueOutputBuffer(
                encoder, &info, timeout_us);
            timeout_us = 0;

            if (idx == AMEDIACODEC_INFO_TRY_AGAIN_LATER) return true;

            if (idx == AMEDIACODEC_INFO_OUTPUT_FORMAT_CHANGED) {
                AMediaFormat* out_fmt = AMediaCodec_getOutputFormat(encoder);
                if (!muxer_started) {
                    video_track = AMediaMuxer_addTrack(muxer, out_fmt);
                    if (video_track < 0) {
                        LOGE("addTrack failed: %zd", video_track);
                        AMediaFormat_delete(out_fmt);
                        return false;
                    }
                    if (AMediaMuxer_start(muxer) != AMEDIA_OK) {
                        LOGE("muxer start failed");
                        AMediaFormat_delete(out_fmt);
                        return false;
                    }
                    muxer_started = true;
                    LOGI("Muxer started, video track=%zd", video_track);
                }
                AMediaFormat_delete(out_fmt);
                continue;
            }

            if (idx < 0) {
                LOGE("dequeueOutputBuffer: %zd", idx);
                return false;
            }

            size_t out_size = 0;
            uint8_t* buf = AMediaCodec_getOutputBuffer(
                encoder, static_cast<size_t>(idx), &out_size);
            if (buf && info.size > 0 && muxer_started) {
                if (first_pts < 0) first_pts = info.presentationTimeUs;
                info.presentationTimeUs -= first_pts;
                if (info.presentationTimeUs < 0) info.presentationTimeUs = 0;
                AMediaMuxer_writeSampleData(
                    muxer, static_cast<size_t>(video_track), buf, &info);
                ++frames;
            }
            AMediaCodec_releaseOutputBuffer(encoder,
                                            static_cast<size_t>(idx), false);
            if (info.flags & AMEDIACODEC_BUFFER_FLAG_END_OF_STREAM) return true;
        }
    };

    const auto deadline = std::chrono::steady_clock::now()
                        + std::chrono::milliseconds(duration_ms);
    bool drain_ok = true;
    while (std::chrono::steady_clock::now() < deadline) {
        if (!drain(20'000)) {
            drain_ok = false;
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    LOGI("Recording loop done: %lld frames so far", (long long)frames);

    ACameraCaptureSession_stopRepeating(session);
    std::this_thread::sleep_for(std::chrono::milliseconds(120));
    AMediaCodec_signalEndOfInputStream(encoder);

    const auto eos_deadline = std::chrono::steady_clock::now()
                            + std::chrono::seconds(10);
    while (drain_ok && std::chrono::steady_clock::now() < eos_deadline) {
        ssize_t idx = AMediaCodec_dequeueOutputBuffer(encoder, &info, 20'000);
        if (idx == AMEDIACODEC_INFO_TRY_AGAIN_LATER) continue;
        if (idx == AMEDIACODEC_INFO_OUTPUT_FORMAT_CHANGED) {
            AMediaFormat* f = AMediaCodec_getOutputFormat(encoder);
            AMediaFormat_delete(f);
            continue;
        }
        if (idx < 0) break;

        size_t out_size = 0;
        uint8_t* buf = AMediaCodec_getOutputBuffer(
            encoder, static_cast<size_t>(idx), &out_size);
        if (buf && info.size > 0 && muxer_started) {
            if (first_pts < 0) first_pts = info.presentationTimeUs;
            info.presentationTimeUs -= first_pts;
            if (info.presentationTimeUs < 0) info.presentationTimeUs = 0;
            AMediaMuxer_writeSampleData(
                muxer, static_cast<size_t>(video_track), buf, &info);
            ++frames;
        }
        const bool eos = (info.flags & AMEDIACODEC_BUFFER_FLAG_END_OF_STREAM) != 0;
        AMediaCodec_releaseOutputBuffer(encoder,
                                        static_cast<size_t>(idx), false);
        if (eos) break;
    }

    if (muxer_started) AMediaMuxer_stop(muxer);
    AMediaMuxer_delete(muxer);
    ::close(out_fd);

    ACaptureRequest_free(request);
    ACameraCaptureSession_close(session);
    ACameraOutputTarget_free(target);
    ACaptureSessionOutput_free(session_output);
    ACaptureSessionOutputContainer_free(outputs);
    AMediaCodec_stop(encoder);
    AMediaCodec_delete(encoder);
    ANativeWindow_release(input_window);
    AMediaFormat_delete(vfmt);
    ACameraDevice_close(device);
    ACameraManager_deleteCameraIdList(ids);
    ACameraManager_delete(mgr);

    struct stat st{};
    size_t bytes = 0;
    if (::stat(out_path.c_str(), &st) == 0) bytes = static_cast<size_t>(st.st_size);

    LOGI("Recording complete: %s (%zu bytes, %lld frames)",
         out_path.c_str(), bytes, (long long)frames);

    if (!muxer_started || bytes == 0) {
        throw std::runtime_error(
            drain_ok ? "recording produced no output"
                     : "recording failed during drain");
    }

    return {
        {"ok", "true"},
        {"path", out_path},
        {"width", std::to_string(kWidth)},
        {"height", std::to_string(kHeight)},
        {"durationMs", std::to_string(duration_ms)},
        {"sizeBytes", std::to_string(bytes)},
        {"frames", std::to_string((long long)frames)},
    };
}

}  // namespace pai::camera

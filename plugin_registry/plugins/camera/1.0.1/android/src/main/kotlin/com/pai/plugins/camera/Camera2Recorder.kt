package com.pai.plugins.camera

import android.content.Context
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CaptureRequest
import android.media.CamcorderProfile
import android.media.MediaRecorder
import android.os.Build
import kotlinx.coroutines.delay
import java.io.File

internal data class Clip(val file: File, val width: Int, val height: Int, val durationMs: Long, val bytes: Long, val hasAudio: Boolean)

/** One video through Camera2 + MediaRecorder (H.264/AAC in MP4). */
internal object Camera2Recorder {
    suspend fun record(context: Context, lens: Lens, durationMs: Long, audio: Boolean, out: File): Clip =
        CameraOp(context).use { op ->
            val id = op.cameraIdFor(lens)
            val chars = op.manager.getCameraCharacteristics(id)
            val sensor = chars.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 90
            val hint = CaptureRules.jpegOrientation(sensor, op.displayRotationDegrees(), lens == Lens.FRONT)
            val profile = pickProfile(id)
            out.parentFile?.mkdirs()
            val tmp = File(out.parentFile, out.name + ".part")

            @Suppress("DEPRECATION")                              // MediaRecorder(Context) needs API 31
            val recorder = if (Build.VERSION.SDK_INT >= 31) MediaRecorder(context) else MediaRecorder()
            op.own(AutoCloseable { runCatching { recorder.reset() }; runCatching { recorder.release() } })
            if (audio) recorder.setAudioSource(MediaRecorder.AudioSource.MIC)
            recorder.setVideoSource(MediaRecorder.VideoSource.SURFACE)
            recorder.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            recorder.setOutputFile(tmp.absolutePath)
            recorder.setVideoEncodingBitRate(profile.videoBitRate)
            recorder.setVideoFrameRate(profile.videoFrameRate)
            recorder.setVideoSize(profile.videoFrameWidth, profile.videoFrameHeight)
            recorder.setVideoEncoder(MediaRecorder.VideoEncoder.H264)
            if (audio) {
                recorder.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
                recorder.setAudioEncodingBitRate(96_000)
                recorder.setAudioSamplingRate(44_100)
            }
            recorder.setOrientationHint(hint)
            try { recorder.prepare() } catch (e: Exception) {
                throw CameraError("camera_error", "Recording could not be prepared (${e.message})")
            }

            val device = op.open(id)
            val session = op.startSession(device, listOf(recorder.surface))
            val request = device.createCaptureRequest(CameraDevice.TEMPLATE_RECORD).apply {
                addTarget(recorder.surface)
                set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_VIDEO)
            }
            session.setRepeatingRequest(request.build(), null, op.handler)
            val t0 = System.nanoTime()
            recorder.start()
            delay(durationMs)
            val actual = (System.nanoTime() - t0) / 1_000_000
            runCatching { session.stopRepeating() }
            try { recorder.stop() } catch (e: RuntimeException) {
                tmp.delete()
                throw CameraError("camera_error", "The recording was too short or the camera stopped delivering frames")
            }
            if (!tmp.renameTo(out)) { tmp.delete(); throw CameraError("write_failed", "The video could not be saved") }
            Clip(out, profile.videoFrameWidth, profile.videoFrameHeight, actual, out.length(), audio)
        }

    /** A recording profile at or below 720p (smaller files; 4K recorders are slow to start). */
    private fun pickProfile(cameraId: String): CamcorderProfile {
        val idInt = cameraId.toIntOrNull() ?: 0
        for (q in intArrayOf(CamcorderProfile.QUALITY_720P, CamcorderProfile.QUALITY_480P, CamcorderProfile.QUALITY_LOW)) {
            if (CamcorderProfile.hasProfile(idInt, q)) return CamcorderProfile.get(idInt, q)
        }
        throw CameraError("camera_error", "This camera has no usable video profile")
    }
}

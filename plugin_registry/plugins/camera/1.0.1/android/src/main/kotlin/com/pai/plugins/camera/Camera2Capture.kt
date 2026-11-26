package com.pai.plugins.camera

import android.content.Context
import android.graphics.BitmapFactory
import android.graphics.ImageFormat
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.CaptureResult
import android.hardware.camera2.TotalCaptureResult
import android.media.ImageReader
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeout
import java.io.File
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

internal data class Shot(val file: File, val width: Int, val height: Int, val bytes: Long)

/** One still photo through Camera2, with a short auto-exposure warm-up so it is not dark. */
internal object Camera2Capture {
    private const val WARMUP_MAX_MS = 1200L

    suspend fun capture(context: Context, lens: Lens, maxDimension: Int, out: File, timeoutMs: Long): Shot =
        CameraOp(context).use { op ->
            withTimeout(timeoutMs) {
                val id = op.cameraIdFor(lens)
                val chars = op.manager.getCameraCharacteristics(id)
                val map = chars.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
                    ?: throw CameraError("camera_error", "This camera reports no output formats")
                val (w, h) = CaptureRules.chooseSize(map.getOutputSizes(ImageFormat.JPEG).orEmpty().map { it.width to it.height }, maxDimension)
                    ?: throw CameraError("camera_error", "This camera cannot produce JPEG photos")
                val sensor = chars.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 90
                val orientation = CaptureRules.jpegOrientation(sensor, op.displayRotationDegrees(), lens == Lens.FRONT)

                val jpeg = ImageReader.newInstance(w, h, ImageFormat.JPEG, 1).also { op.own(it) }
                // A small YUV stream that is drained and dropped: it lets the repeating request run so
                // auto exposure / focus settle before the real shot.
                val (pw, ph) = CaptureRules.chooseSize(map.getOutputSizes(ImageFormat.YUV_420_888).orEmpty().map { it.width to it.height }, 640) ?: (640 to 480)
                val warm = ImageReader.newInstance(pw, ph, ImageFormat.YUV_420_888, 2).also { op.own(it) }
                warm.setOnImageAvailableListener({ r -> runCatching { r.acquireLatestImage()?.close() } }, op.handler)

                val device = op.open(id)
                val session = op.startSession(device, listOf(jpeg.surface, warm.surface))
                settle(session, device, warm, op)
                val bytes = shoot(session, device, jpeg, orientation, op)
                writeAtomically(out, bytes)
                val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
                Shot(out, bounds.outWidth.takeIf { it > 0 } ?: w, bounds.outHeight.takeIf { it > 0 } ?: h, bytes.size.toLong())
            }
        }

    private suspend fun settle(session: CameraCaptureSession, device: CameraDevice, warm: ImageReader, op: CameraOp) {
        val builder = device.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).apply {
            addTarget(warm.surface)
            set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
            set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON)
        }
        suspendCancellableCoroutine<Unit> { cont ->
            val started = System.nanoTime()
            var frames = 0
            session.setRepeatingRequest(builder.build(), object : CameraCaptureSession.CaptureCallback() {
                override fun onCaptureCompleted(s: CameraCaptureSession, r: CaptureRequest, result: TotalCaptureResult) {
                    frames++
                    val ae = result.get(CaptureResult.CONTROL_AE_STATE)
                    val converged = ae == null || ae == CaptureResult.CONTROL_AE_STATE_CONVERGED ||
                        ae == CaptureResult.CONTROL_AE_STATE_FLASH_REQUIRED || ae == CaptureResult.CONTROL_AE_STATE_LOCKED
                    val waited = (System.nanoTime() - started) / 1_000_000
                    if ((converged && frames >= 4) || waited >= WARMUP_MAX_MS) {
                        runCatching { s.stopRepeating() }
                        if (cont.isActive) cont.resume(Unit)
                    }
                }
                override fun onCaptureFailed(s: CameraCaptureSession, r: CaptureRequest, f: android.hardware.camera2.CaptureFailure) {
                    if (cont.isActive) cont.resumeWithException(CameraError("camera_error", "The camera failed while getting ready"))
                }
            }, op.handler)                       // a handler is required: this coroutine's thread has no Looper
            cont.invokeOnCancellation { runCatching { session.stopRepeating() } }
        }
    }

    private suspend fun shoot(session: CameraCaptureSession, device: CameraDevice, jpeg: ImageReader, orientation: Int, op: CameraOp): ByteArray =
        suspendCancellableCoroutine { cont ->
            jpeg.setOnImageAvailableListener({ r ->
                val image = runCatching { r.acquireNextImage() }.getOrNull() ?: return@setOnImageAvailableListener
                try {
                    val buf = image.planes[0].buffer
                    val data = ByteArray(buf.remaining()).also { buf.get(it) }
                    if (cont.isActive) cont.resume(data)
                } finally {
                    image.close()
                }
            }, op.handler)
            val still = device.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE).apply {
                addTarget(jpeg.surface)
                set(CaptureRequest.JPEG_ORIENTATION, orientation)
                set(CaptureRequest.JPEG_QUALITY, 92.toByte())
                set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
                set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON)
            }
            session.capture(still.build(), object : CameraCaptureSession.CaptureCallback() {
                override fun onCaptureFailed(s: CameraCaptureSession, r: CaptureRequest, f: android.hardware.camera2.CaptureFailure) {
                    if (cont.isActive) cont.resumeWithException(CameraError("camera_error", "The camera could not take the photo"))
                }
            }, op.handler)
        }

    private fun writeAtomically(target: File, bytes: ByteArray) {
        target.parentFile?.mkdirs()
        val tmp = File(target.parentFile, target.name + ".part")
        tmp.outputStream().use { it.write(bytes) }
        if (!tmp.renameTo(target)) {
            tmp.delete()
            throw CameraError("write_failed", "The photo could not be saved")
        }
    }
}

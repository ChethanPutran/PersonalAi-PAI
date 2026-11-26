package com.pai.plugins.camera

import android.content.Context
import android.hardware.camera2.CameraAccessException
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.params.OutputConfiguration
import android.hardware.camera2.params.SessionConfiguration
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.view.Display
import android.hardware.display.DisplayManager
import android.view.Surface
import kotlinx.coroutines.suspendCancellableCoroutine
import java.util.concurrent.Executor
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * Owns everything one camera operation opens (thread, device, session) and closes it in [close],
 * whatever happened: the old code left the camera device open after a successful capture, which
 * locks the camera for every other app until this process dies.
 */
internal class CameraOp(private val context: Context) : AutoCloseable {
    val manager: CameraManager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
    private val thread = HandlerThread("pai-camera").apply { start() }
    val handler = Handler(thread.looper)
    private val executor = Executor { handler.post(it) }
    private var device: CameraDevice? = null
    private var session: CameraCaptureSession? = null
    private val closeables = mutableListOf<AutoCloseable>()

    fun own(c: AutoCloseable) { closeables += c }

    fun cameraIdFor(lens: Lens): String {
        val want = if (lens == Lens.FRONT) CameraCharacteristics.LENS_FACING_FRONT else CameraCharacteristics.LENS_FACING_BACK
        return try {
            manager.cameraIdList.firstOrNull { manager.getCameraCharacteristics(it).get(CameraCharacteristics.LENS_FACING) == want }
        } catch (e: CameraAccessException) {
            throw CameraError("camera_unavailable", "The camera could not be reached (${e.message ?: "access error"})")
        } ?: throw CameraError("lens_unavailable", "This phone has no ${lens.wire} camera")
    }

    suspend fun open(cameraId: String): CameraDevice = suspendCancellableCoroutine { cont ->
        try {
            manager.openCamera(cameraId, object : CameraDevice.StateCallback() {
                override fun onOpened(d: CameraDevice) {
                    device = d
                    if (cont.isActive) cont.resume(d) else d.close()
                }
                override fun onDisconnected(d: CameraDevice) {
                    d.close()
                    if (cont.isActive) cont.resumeWithException(CameraError("camera_in_use", "The camera was taken by another app"))
                }
                override fun onError(d: CameraDevice, error: Int) {
                    d.close()
                    if (cont.isActive) cont.resumeWithException(CameraError("camera_error", "The camera reported error $error"))
                }
            }, handler)
        } catch (e: SecurityException) {
            cont.resumeWithException(CameraError("permission_denied", "Camera permission has not been granted to PAI"))
        } catch (e: CameraAccessException) {
            cont.resumeWithException(CameraError("camera_unavailable", "The camera could not be opened (${e.message ?: "access error"})"))
        }
    }

    suspend fun startSession(d: CameraDevice, surfaces: List<Surface>): CameraCaptureSession =
        suspendCancellableCoroutine { cont ->
            val callback = object : CameraCaptureSession.StateCallback() {
                override fun onConfigured(s: CameraCaptureSession) {
                    session = s
                    if (cont.isActive) cont.resume(s) else s.close()
                }
                override fun onConfigureFailed(s: CameraCaptureSession) {
                    s.close()
                    if (cont.isActive) cont.resumeWithException(CameraError("camera_error", "The camera could not start a capture session"))
                }
            }
            try {
                if (Build.VERSION.SDK_INT >= 28) {
                    d.createCaptureSession(SessionConfiguration(
                        SessionConfiguration.SESSION_REGULAR, surfaces.map { OutputConfiguration(it) }, executor, callback))
                } else {
                    @Suppress("DEPRECATION")                     // the only API on Android 7 and 8
                    d.createCaptureSession(surfaces, callback, handler)
                }
            } catch (e: Exception) {
                cont.resumeWithException(CameraError("camera_error", "The camera could not start a capture session (${e.message})"))
            }
        }

    /** Device rotation in degrees (0, 90, 180, 270) from the default display. */
    fun displayRotationDegrees(): Int {
        val dm = context.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager
        return when (dm.getDisplay(Display.DEFAULT_DISPLAY)?.rotation) {
            Surface.ROTATION_90 -> 90
            Surface.ROTATION_180 -> 180
            Surface.ROTATION_270 -> 270
            else -> 0
        }
    }

    override fun close() {
        runCatching { session?.close() }
        runCatching { device?.close() }
        closeables.asReversed().forEach { runCatching { it.close() } }
        thread.quitSafely()
    }
}

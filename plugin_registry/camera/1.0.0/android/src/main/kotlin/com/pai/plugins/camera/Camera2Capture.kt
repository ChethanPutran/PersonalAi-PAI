package com.pai.plugins.camera

import android.content.Context
import android.graphics.ImageFormat
import android.hardware.camera2.*
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import kotlinx.coroutines.suspendCancellableCoroutine
import java.io.File
import kotlin.coroutines.resume

internal object Camera2Capture {
    private const val TAG = "PaiCamera2"

    suspend fun capture(context: Context, lens: String, outputFile: File): String =
        suspendCancellableCoroutine { cont ->
            val thread = HandlerThread("pai-camera-capture").apply { start() }
            val handler = Handler(thread.looper)
            val manager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager

            val wantFacing = if (lens == "front")
                CameraCharacteristics.LENS_FACING_FRONT
            else
                CameraCharacteristics.LENS_FACING_BACK

            val cameraId = manager.cameraIdList.firstOrNull { id ->
                manager.getCameraCharacteristics(id)
                    .get(CameraCharacteristics.LENS_FACING) == wantFacing
            } ?: manager.cameraIdList.firstOrNull()
              ?: run {
                  thread.quitSafely()
                  cont.resume("") // caller maps to error
                  return@suspendCancellableCoroutine
              }

            val reader = ImageReader.newInstance(1280, 720, ImageFormat.JPEG, 4)
            val deviceCallback = object : CameraDevice.StateCallback() {
                override fun onOpened(device: CameraDevice) {
                    val reqBuilder = device.createCaptureRequest(
                        CameraDevice.TEMPLATE_STILL_CAPTURE
                    )
                    reqBuilder.addTarget(reader.surface)
                    reqBuilder.set(
                        CaptureRequest.JPEG_ORIENTATION, 90
                    )
                    // Deprecated on API 28+ but works on every version minSdk 24 supports.
                    // createCaptureSession(SessionConfiguration) requires API 28 — using it
                    // would crash on Android 7-8.1 devices. The warning is expected.
                    @Suppress("DEPRECATION")
                    device.createCaptureSession(
                        listOf(reader.surface),
                        object : CameraCaptureSession.StateCallback() {
                            override fun onConfigured(session: CameraCaptureSession) {
                                session.capture(reqBuilder.build(), null, handler)
                            }
                            override fun onConfigureFailed(session: CameraCaptureSession) {
                                Log.e(TAG, "session configure failed")
                                device.close()
                            }
                        },
                        handler,
                    )
                }
                override fun onDisconnected(device: CameraDevice) {
                    Log.w(TAG, "device disconnected"); device.close()
                }
                override fun onError(device: CameraDevice, error: Int) {
                    Log.e(TAG, "device error $error"); device.close()
                }
            }

            reader.setOnImageAvailableListener({ r ->
                val image = r.acquireNextImage() ?: return@setOnImageAvailableListener
                try {
                    val buf = image.planes[0].buffer
                    outputFile.parentFile?.mkdirs()
                    outputFile.outputStream().use { out ->
                        val bytes = ByteArray(buf.remaining())
                        buf.get(bytes)
                        out.write(bytes)
                    }
                    Log.i(TAG, "wrote ${outputFile.length()} bytes → ${outputFile.path}")
                    if (!cont.isCompleted) cont.resume(outputFile.absolutePath)
                } finally {
                    image.close()
                    reader.close()
                    thread.quitSafely()
                }
            }, handler)

            try {
                manager.openCamera(cameraId, deviceCallback, handler)
            } catch (e: SecurityException) {
                Log.e(TAG, "permission missing", e)
                thread.quitSafely()
                if (!cont.isCompleted) cont.resume("")
            }
        }
}
package com.pai.plugins.camera

import android.util.Log
import com.pai.plugin.api.PluginContext
import com.pai.plugin.api.PluginModule
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

/**
 * Kotlin camera plugin — mirrors the C++ pai.camera plugin.
 *
 * Demonstrates the pattern: no-arg constructor, attach(context) receives
 * the host's PluginContext, invoke() dispatches capabilities.
 *
 * Real capture code (Camera2/CameraX) goes in [captureImage]; this stub
 * shows the shape. Port from your C++ implementation or write fresh.
 */
class CameraPlugin : PluginModule {

    override val name = "pai.camera"
    override val capabilities = listOf("camera.capture", "camera.record", "camera.stream")
    override val permissions = listOf("camera", "microphone", "filesystem")

    private var ctx: PluginContext? = null

    override fun attach(context: PluginContext) {
        ctx = context
        Log.i(TAG, "attach(${context.pluginId}, cache=${context.cacheDir})")
    }

    override suspend fun invoke(
        capability: String,
        params: Map<String, Any?>,
    ): Map<String, Any?> = try {
        when (capability) {
            "camera.capture" -> captureImage(params)
            "camera.record" -> mapOf("ok" to false, "error" to "record_not_implemented")
            "camera.stream" -> mapOf("ok" to false, "error" to "stream_not_implemented")
            else -> mapOf("ok" to false, "error" to "unknown_capability: $capability")
        }
    } catch (t: Throwable) {
        Log.e(TAG, "invoke($capability) failed", t)
        mapOf("ok" to false, "error" to (t.message ?: "invoke_failed"))
    }

    private suspend fun captureImage(params: Map<String, Any?>): Map<String, Any?> =
        withContext(Dispatchers.IO) {
            val c = ctx ?: return@withContext mapOf(
                "ok" to false, "error" to "no_context_attached",
            )

            val lens = (params["lens"] as? String) ?: "back"
            val overridePath = params["outputPath"] as? String
            val outFile = overridePath?.let(::File)
                ?: File(c.filesDir, "PAI_${System.currentTimeMillis()}.jpg")

            // ---- REPLACE THIS with real Camera2/CameraX capture ----
            // Minimal Camera2 code path — swap for CameraX if preferred.
            val path = Camera2Capture.capture(
                context = c.appContext,
                lens = lens,
                outputFile = outFile,
            )

            mapOf(
                "ok" to "true",
                "path" to path,
                "width" to "1280",
                "height" to "720",
            )
        }

    override fun destroy() {
        Log.i(TAG, "destroy()")
        ctx = null
    }

    companion object {
        private const val TAG = "PaiCameraKt"
    }
}
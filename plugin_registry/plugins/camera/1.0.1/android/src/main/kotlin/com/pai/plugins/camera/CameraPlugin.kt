package com.pai.plugins.camera

import android.util.Log
import com.pai.plugin.api.PluginContext
import com.pai.plugin.api.PluginModule
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.withContext
import java.io.File

/**
 * Camera plugin (`pai.camera`): still photos and short videos through Camera2.
 *
 * - One camera operation at a time (the camera is a single shared resource): a second request while
 *   one runs is refused with `camera_busy` instead of fighting over the device.
 * - Results are written only inside the plugin's own `captures` folder. With `saveToGallery: true` a COPY
 *   also goes to the shared gallery (Pictures/PAI or Movies/PAI) so the user can open it in the Gallery app.
 * - Every failure is `{"ok": false, "error": <code>, "message": <plain words>}`; success is
 *   `{"ok": true, ...}` with real values read back from the file, never placeholders.
 * - Not offered here: `camera.stream` (the plugin interface returns one result per call, it cannot
 *   carry live frames), so it is not declared in the manifest either.
 */
class CameraPlugin : PluginModule {

    override val name = "pai.camera"
    override val capabilities = listOf("camera.capture", "camera.record")
    override val permissions = listOf("camera", "microphone", "filesystem")

    private var ctx: PluginContext? = null
    private val busy = Mutex()

    override fun attach(context: PluginContext) {
        ctx = context
        Log.i(TAG, "attach(${context.pluginId})")
    }

    override suspend fun invoke(capability: String, params: Map<String, Any?>): Map<String, Any?> {
        val c = ctx ?: return failure("no_context_attached", "The camera plugin is not ready")
        if (!busy.tryLock()) return failure("camera_busy", "The camera is already in use. Try again in a moment")
        return try {
            withContext(Dispatchers.IO) {
                when (capability) {
                    "camera.capture" -> capture(c, params)
                    "camera.record" -> record(c, params)
                    else -> failure("unknown_capability", "Unknown capability: $capability")
                }
            }
        } catch (e: kotlinx.coroutines.TimeoutCancellationException) {
            // Before CancellationException: a timeout IS a cancellation, but here it is our own and means "too slow".
            failure("timeout", "The camera did not answer in time")
        } catch (e: CancellationException) {
            throw e
        } catch (e: CameraError) {
            failure(e.code, e.message ?: e.code)
        } catch (t: Throwable) {
            Log.e(TAG, "invoke($capability) failed", t)
            failure("camera_error", t.message ?: "The camera failed")
        } finally {
            busy.unlock()
        }
    }

    private suspend fun capture(c: PluginContext, params: Map<String, Any?>): Map<String, Any?> {
        val lens = Lens.parse(params["lens"])
        val format = (params["format"] as? String)?.lowercase() ?: "jpeg"
        if (format != "jpeg" && format != "jpg") throw CameraError("bad_parameter", "Only jpeg photos are supported")
        val out = CaptureRules.resolveOutput(capturesDir(c), params["outputPath"], "PAI_${System.currentTimeMillis()}.jpg")
        val shot = Camera2Capture.capture(c.appContext, lens, CaptureRules.maxDimension(params["maxDimension"]), out, TIMEOUT_MS)
        val result = mapOf("ok" to true, "path" to shot.file.absolutePath, "width" to shot.width, "height" to shot.height,
            "size_bytes" to shot.bytes, "lens" to lens.wire, "mime" to "image/jpeg")
        return withGalleryCopy(c, params, shot.file, MediaKind.PHOTO, result)
    }

    private suspend fun record(c: PluginContext, params: Map<String, Any?>): Map<String, Any?> {
        val lens = Lens.parse(params["lens"])
        val duration = CaptureRules.clampDuration(params["durationMs"])
        val audio = CaptureRules.bool(params["audio"], true)
        val out = CaptureRules.resolveOutput(capturesDir(c), params["outputPath"], "PAI_${System.currentTimeMillis()}.mp4")
        val clip = Camera2Recorder.record(c.appContext, lens, duration, audio, out)
        val result = mapOf("ok" to true, "path" to clip.file.absolutePath, "width" to clip.width, "height" to clip.height,
            "duration_ms" to clip.durationMs, "size_bytes" to clip.bytes, "has_audio" to clip.hasAudio,
            "lens" to lens.wire, "mime" to "video/mp4")
        return withGalleryCopy(c, params, clip.file, MediaKind.VIDEO, result)
    }

    /**
     * `saveToGallery: true` also puts a copy in the shared gallery (Pictures/PAI, Movies/PAI). The capture itself
     * has already succeeded, so a failed copy is reported next to the result (`gallery_saved: false` and why)
     * instead of failing the whole call and losing a good photo.
     */
    private fun withGalleryCopy(
        c: PluginContext, params: Map<String, Any?>, file: File, kind: MediaKind, result: Map<String, Any?>,
    ): Map<String, Any?> {
        if (!CaptureRules.bool(params["saveToGallery"], false)) return result
        return try {
            val saved = GallerySaver.save(c.appContext, file, kind)
            result + mapOf("gallery_saved" to true, "gallery_uri" to saved.uri, "gallery_location" to saved.location)
        } catch (e: CameraError) {
            result + mapOf("gallery_saved" to false, "gallery_error" to e.code, "gallery_message" to (e.message ?: e.code))
        }
    }

    private fun capturesDir(c: PluginContext): File = File(c.filesDir, "captures").apply { mkdirs() }

    private fun failure(code: String, message: String): Map<String, Any?> =
        mapOf("ok" to false, "error" to code, "message" to message)

    override fun destroy() {
        Log.i(TAG, "destroy()")
        ctx = null
    }

    private companion object {
        const val TAG = "PaiCameraKt"
        const val TIMEOUT_MS = 20_000L
    }
}

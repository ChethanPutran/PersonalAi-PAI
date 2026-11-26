package com.pai.plugins.camera

import java.io.File

/** A camera plugin failure the user can read: a stable [code] plus a plain [message]. */
internal class CameraError(val code: String, message: String) : Exception(message)

internal enum class Lens(val wire: String) {
    BACK("back"), FRONT("front");

    companion object {
        fun parse(raw: Any?): Lens = when ((raw as? String)?.lowercase()) {
            null, "", "back" -> BACK
            "front" -> FRONT
            else -> throw CameraError("bad_parameter", "lens must be \"front\" or \"back\"")
        }
    }
}

/** Pure rules (no Android types) so they can be unit tested on the JVM. */
internal object CaptureRules {
    const val DEFAULT_MAX_DIMENSION = 2048          // longest side of a photo; plenty for an assistant, small enough to move around
    const val MAX_RECORD_MS = 60_000L
    const val MIN_RECORD_MS = 500L
    const val DEFAULT_RECORD_MS = 5_000L

    /**
     * Where a result may be written: only inside [base] (the plugin's own folder). A relative name is
     * resolved against it; an absolute path is accepted only if it already lies inside it (the backend
     * checks caller-chosen paths against the manifest's allowedRoots, so callers pass absolute paths).
     * `..`, symlinks out of the folder and anything else outside it are refused, so the plugin cannot be
     * used to overwrite arbitrary files of the app or the phone.
     */
    fun resolveOutput(base: File, requested: Any?, defaultName: String): File {
        val root = base.canonicalFile
        val name = (requested as? String)?.trim().orEmpty().ifEmpty { defaultName }
        if (name.contains('\u0000') || name.contains('\\')) {
            throw CameraError("path_not_allowed", "Files can only be saved inside the camera folder: use a plain file name")
        }
        val target = (if (name.startsWith("/")) File(name) else File(root, name)).canonicalFile
        if (target.path != root.path && !target.path.startsWith(root.path + File.separator)) {
            throw CameraError("path_not_allowed", "Files can only be saved inside the camera folder")
        }
        if (target.isDirectory) throw CameraError("path_not_allowed", "That name is a folder, not a file")
        return target
    }

    /** The largest size whose longest side is <= [maxDimension]; else the smallest available. */
    fun chooseSize(sizes: List<Pair<Int, Int>>, maxDimension: Int): Pair<Int, Int>? {
        if (sizes.isEmpty()) return null
        val fitting = sizes.filter { maxOf(it.first, it.second) <= maxDimension }
        return (fitting.ifEmpty { listOf(sizes.minByOrNull { it.first.toLong() * it.second }!!) })
            .maxByOrNull { it.first.toLong() * it.second }
    }

    /** JPEG_ORIENTATION so the picture is upright: sensor mounting angle and the device's rotation. */
    fun jpegOrientation(sensorOrientation: Int, deviceRotationDegrees: Int, frontFacing: Boolean): Int {
        val rotation = ((deviceRotationDegrees % 360) + 360) % 360
        val sign = if (frontFacing) 1 else -1
        return ((sensorOrientation + sign * rotation) % 360 + 360) % 360
    }

    fun clampDuration(raw: Any?): Long {
        val ms = when (raw) {
            null -> DEFAULT_RECORD_MS
            is Number -> raw.toLong()
            else -> throw CameraError("bad_parameter", "durationMs must be a number of milliseconds")
        }
        return ms.coerceIn(MIN_RECORD_MS, MAX_RECORD_MS)
    }

    fun maxDimension(raw: Any?): Int = when (raw) {
        null -> DEFAULT_MAX_DIMENSION
        is Number -> raw.toInt().coerceIn(320, 4096)
        else -> throw CameraError("bad_parameter", "maxDimension must be a number of pixels")
    }

    fun bool(raw: Any?, default: Boolean): Boolean = when (raw) {
        null -> default
        is Boolean -> raw
        is String -> raw.equals("true", true)
        else -> default
    }
}

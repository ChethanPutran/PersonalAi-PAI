package com.pai.plugins.camera

import java.io.File

enum class MediaKind { PHOTO, VIDEO }

/**
 * Where a gallery copy goes and how it is named. Pure (no Android classes) so it is unit-tested on the JVM;
 * [GallerySaver] does the actual MediaStore work.
 */
object GalleryRules {
    /** MediaStore's RELATIVE_PATH column exists from Android 10; below it a copy would need the old storage permission. */
    const val MIN_SDK = 29

    class Target(val relativePath: String, val mime: String)

    fun supported(sdk: Int): Boolean = sdk >= MIN_SDK

    /** Photos go to Pictures/PAI, videos to Movies/PAI: one tidy album the Gallery app shows. */
    fun target(kind: MediaKind): Target = when (kind) {
        MediaKind.PHOTO -> Target("Pictures/PAI", "image/jpeg")
        MediaKind.VIDEO -> Target("Movies/PAI", "video/mp4")
    }

    /**
     * The name shown in the gallery: the file's own last path segment only (a plugin-relative sub folder never
     * leaks into the shared album), no control characters, with the extension the media type needs.
     */
    fun displayName(file: File, kind: MediaKind): String {
        val ext = if (kind == MediaKind.PHOTO) "jpg" else "mp4"
        val base = file.name.filter { it >= ' ' && it != '/' && it != '\\' }.trim().ifEmpty { "PAI_${System.currentTimeMillis()}" }
        val stem = base.substringBeforeLast('.', base)
        val given = if (base.contains('.')) base.substringAfterLast('.').lowercase() else ""
        val keep = if (kind == MediaKind.PHOTO) given in setOf("jpg", "jpeg") else given == "mp4"
        return if (keep) base else "$stem.$ext"
    }
}

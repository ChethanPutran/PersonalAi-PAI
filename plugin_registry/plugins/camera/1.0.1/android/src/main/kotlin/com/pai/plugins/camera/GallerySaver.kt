package com.pai.plugins.camera

import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.provider.MediaStore
import java.io.File

/**
 * Saves a COPY of a capture into the phone's shared gallery (Pictures/PAI or Movies/PAI), so the user can open
 * it in the Gallery app. The original stays in the plugin's private folder.
 *
 * Android 10+ lets an app add its own media through MediaStore without any storage permission, which is why
 * this needs no new permission. The file is inserted as "pending" (invisible to other apps) and published only
 * after it has been copied completely, so the gallery never shows a half-written video.
 */
object GallerySaver {
    class Saved(val uri: String, val location: String)

    fun save(context: Context, source: File, kind: MediaKind, sdk: Int = Build.VERSION.SDK_INT): Saved {
        if (!GalleryRules.supported(sdk)) {
            throw CameraError("gallery_unsupported", "Saving to the gallery needs Android 10 or newer")
        }
        if (!source.isFile) throw CameraError("gallery_failed", "The capture is missing, so there is nothing to copy")
        val target = GalleryRules.target(kind)
        val name = GalleryRules.displayName(source, kind)
        val resolver = context.contentResolver
        val collection = if (kind == MediaKind.PHOTO) {
            MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        } else {
            MediaStore.Video.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        }
        val pending = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, name)
            put(MediaStore.MediaColumns.MIME_TYPE, target.mime)
            put(MediaStore.MediaColumns.RELATIVE_PATH, target.relativePath)
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }
        val uri = resolver.insert(collection, pending)
            ?: throw CameraError("gallery_failed", "The gallery did not accept the file")
        try {
            val out = resolver.openOutputStream(uri) ?: throw CameraError("gallery_failed", "Could not write to the gallery")
            out.use { sink -> source.inputStream().use { it.copyTo(sink) } }
            resolver.update(uri, ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }, null, null)
        } catch (t: Throwable) {
            runCatching { resolver.delete(uri, null, null) }          // never leave a broken entry behind
            if (t is CameraError) throw t
            throw CameraError("gallery_failed", "Could not copy the file to the gallery: ${t.message ?: "unknown error"}")
        }
        return Saved(uri.toString(), "${target.relativePath}/$name")
    }
}

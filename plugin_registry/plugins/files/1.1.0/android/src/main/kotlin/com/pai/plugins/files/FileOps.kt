package com.pai.plugins.files

import java.io.File
import java.nio.charset.CodingErrorAction
import java.nio.file.Files
import java.nio.file.StandardCopyOption

/** The file operations, on plain java.io so they are unit tested on the JVM. */
internal class FileOps(private val jail: FileJail) {

    companion object {
        const val DEFAULT_READ = 1L shl 20            // 1 MB
        const val MAX_READ = 10L shl 20               // 10 MB hard cap
        const val MAX_WRITE = 10L shl 20
        const val MAX_LIST = 5000
    }

    private fun entry(f: File) = mapOf(
        "path" to f.path, "name" to f.name, "type" to if (f.isDirectory) "dir" else "file",
        "size" to if (f.isFile) f.length() else 0L, "modified_ms" to f.lastModified())

    fun list(path: String, recursive: Boolean, limit: Int): Map<String, Any?> {
        val dir = jail.resolve(path)
        if (!dir.isDirectory) throw FilesError("not_a_directory", "That is not a folder")
        val max = limit.coerceIn(1, MAX_LIST)
        val out = ArrayList<Map<String, Any?>>()
        var truncated = false
        fun walk(d: File) {
            val children = d.listFiles()?.sortedBy { it.name.lowercase() } ?: return
            for (c in children) {
                // Folders switched off in the settings (and links that lead out of the roots) are not shown at all.
                val canon = c.canonicalFile
                if (!jail.inside(canon)) continue
                if (out.size >= max) { truncated = true; return }
                out += entry(c)
                if (recursive && c.isDirectory) walk(c)
            }
        }
        walk(dir)
        return mapOf("ok" to true, "path" to dir.path, "entries" to out, "truncated" to truncated)
    }

    fun read(path: String, maxBytes: Long): Map<String, Any?> {
        val f = jail.resolve(path)
        if (!f.exists()) throw FilesError("not_found", "No such file")
        if (!f.isFile) throw FilesError("not_a_file", "That is a folder, not a file")
        val limit = maxBytes.coerceIn(1, MAX_READ)
        val size = f.length()
        val n = minOf(size, limit).toInt()
        val bytes = ByteArray(n)
        f.inputStream().use { var off = 0; while (off < n) { val r = it.read(bytes, off, n - off); if (r < 0) break; off += r } }
        // Decode as UTF-8 and refuse binary: sending raw bytes as "text" would be garbage to the assistant.
        val decoder = Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT)
        val text = try { decoder.decode(java.nio.ByteBuffer.wrap(bytes)).toString() } catch (e: Exception) {
            if (size > limit) String(bytes, Charsets.UTF_8) // a multi-byte char cut at the limit: keep what decodes
            else throw FilesError("not_text", "That file is not text")
        }
        return mapOf("ok" to true, "path" to f.path, "text" to text, "size" to size, "truncated" to (size > limit))
    }

    fun write(path: String, text: String, append: Boolean, createDirs: Boolean): Map<String, Any?> {
        jail.requireWritable()
        val f = jail.resolve(path)
        val bytes = text.toByteArray(Charsets.UTF_8)
        if (bytes.size > MAX_WRITE) throw FilesError("too_large", "That is too large to write in one go (10 MB limit)")
        if (f.isDirectory) throw FilesError("not_a_file", "That is a folder, not a file")
        val parent = f.parentFile ?: throw FilesError("path_not_allowed", "That path is not valid")
        if (!parent.exists()) {
            if (!createDirs) throw FilesError("parent_missing", "The folder does not exist (set createDirs to create it)")
            if (!parent.mkdirs() && !parent.isDirectory) throw FilesError("write_failed", "The folder could not be created")
        }
        if (append) {
            f.appendBytes(bytes)
        } else {                                     // write beside, then rename: a crash never leaves half a file
            val tmp = File(parent, f.name + ".part")
            tmp.writeBytes(bytes)
            try { Files.move(tmp.toPath(), f.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE) }
            catch (e: Exception) { tmp.delete(); throw FilesError("write_failed", "The file could not be saved") }
        }
        return mapOf("ok" to true, "path" to f.path, "bytes_written" to bytes.size, "size" to f.length())
    }

    fun stat(path: String): Map<String, Any?> {
        val f = jail.resolve(path)
        if (!f.exists()) return mapOf("ok" to true, "path" to f.path, "exists" to false)
        return entry(f) + mapOf("ok" to true, "exists" to true)
    }

    fun delete(path: String, recursive: Boolean): Map<String, Any?> {
        jail.requireWritable()
        val f = jail.resolve(path)
        if (jail.isRoot(f)) throw FilesError("path_not_allowed", "An allowed folder itself cannot be deleted")
        if (!f.exists()) throw FilesError("not_found", "No such file")
        jail.requireNoDeniedInside(f)
        if (f.isDirectory) {
            if (!recursive && (f.list()?.isNotEmpty() == true)) throw FilesError("not_empty", "That folder is not empty (set recursive to delete it with everything in it)")
            if (!f.deleteRecursively()) throw FilesError("delete_failed", "The folder could not be deleted")
        } else if (!f.delete()) throw FilesError("delete_failed", "The file could not be deleted")
        return mapOf("ok" to true, "path" to f.path)
    }

    fun copy(source: String, destination: String, overwrite: Boolean): Map<String, Any?> {
        jail.requireWritable()
        val src = jail.resolve(source); val dst = jail.resolve(destination)
        if (!src.exists()) throw FilesError("not_found", "No such file")
        jail.requireNoDeniedInside(src)
        if (dst.exists() && !overwrite) throw FilesError("exists", "The destination already exists (set overwrite to replace it)")
        if (src.isDirectory) {
            if (dst.canonicalPath.startsWith(src.canonicalPath + File.separator)) throw FilesError("bad_parameter", "A folder cannot be copied into itself")
            if (!src.copyRecursively(dst, overwrite)) throw FilesError("copy_failed", "The folder could not be copied")
        } else {
            dst.parentFile?.mkdirs()
            src.copyTo(dst, overwrite)
        }
        return mapOf("ok" to true, "source" to src.path, "destination" to dst.path)
    }

    fun move(source: String, destination: String, overwrite: Boolean): Map<String, Any?> {
        jail.requireWritable()
        val src = jail.resolve(source); val dst = jail.resolve(destination)
        if (jail.isRoot(src)) throw FilesError("path_not_allowed", "An allowed folder itself cannot be moved")
        if (!src.exists()) throw FilesError("not_found", "No such file")
        jail.requireNoDeniedInside(src)
        if (dst.exists() && !overwrite) throw FilesError("exists", "The destination already exists (set overwrite to replace it)")
        if (src.isDirectory && dst.canonicalPath.startsWith(src.canonicalPath + File.separator)) throw FilesError("bad_parameter", "A folder cannot be moved into itself")
        dst.parentFile?.mkdirs()
        if (!src.renameTo(dst)) {
            if (src.isDirectory) { if (!src.copyRecursively(dst, overwrite)) throw FilesError("move_failed", "The folder could not be moved"); src.deleteRecursively() }
            else { src.copyTo(dst, overwrite); src.delete() }
        }
        return mapOf("ok" to true, "source" to src.path, "destination" to dst.path)
    }
}

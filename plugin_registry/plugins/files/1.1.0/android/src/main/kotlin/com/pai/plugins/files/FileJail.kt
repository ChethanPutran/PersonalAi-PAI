package com.pai.plugins.files

import java.io.File

/** A failure the user can read: a stable [code] plus plain words. */
internal class FilesError(val code: String, message: String) : Exception(message)

/**
 * The only places this plugin may touch. Everything is checked on CANONICAL paths, so `..` and
 * symlinks cannot lead out; a relative path is resolved against the first root.
 */
internal class FileJail(roots: List<File>) {
    val roots: List<File> = roots.map { it.canonicalFile }.distinctBy { it.path }

    init { require(this.roots.isNotEmpty()) { "no roots" } }

    fun resolve(path: String?): File {
        val raw = path?.trim().orEmpty()
        if (raw.isEmpty()) throw FilesError("bad_parameter", "path is required")
        if (raw.contains('\u0000')) throw FilesError("path_not_allowed", "That path is not valid")
        val target = (if (raw.startsWith("/")) File(raw) else File(roots.first(), raw)).canonicalFile
        if (!inside(target)) {
            throw FilesError("path_not_allowed",
                "PAI can only use files inside its own folder on this phone (${roots.first().path})")
        }
        return target
    }

    fun inside(f: File): Boolean = roots.any { f.path == it.path || f.path.startsWith(it.path + File.separator) }

    fun isRoot(f: File): Boolean = roots.any { it.path == f.canonicalPath }
}

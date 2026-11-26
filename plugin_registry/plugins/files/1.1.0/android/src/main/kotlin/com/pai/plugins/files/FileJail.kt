package com.pai.plugins.files

import java.io.File

/** A failure the user can read: a stable [code] plus plain words. */
internal class FilesError(val code: String, message: String) : Exception(message)

/**
 * The only places this plugin may touch. Everything is checked on CANONICAL paths, so `..` and
 * symlinks cannot lead out; a relative path is resolved against the first root.
 *
 * [denied] folders are refused even inside an allowed root. When a denied folder and an allowed root
 * both contain a path, the MORE SPECIFIC one wins: "/Android/data" denied does not lock PAI out of an
 * allowed "/Android/data/<app>/files", but "/DCIM/private" denied does beat an allowed "/DCIM".
 * [readOnly] refuses anything that changes files.
 */
internal class FileJail(
    roots: List<File>,
    denied: List<File> = emptyList(),
    val readOnly: Boolean = false,
) {
    val roots: List<File> = roots.map { it.canonicalFile }.distinctBy { it.path }
    val denied: List<File> = denied.map { it.canonicalFile }.distinctBy { it.path }

    fun resolve(path: String?): File {
        val raw = path?.trim().orEmpty()
        if (raw.isEmpty()) throw FilesError("bad_parameter", "path is required")
        if (raw.contains('\u0000')) throw FilesError("path_not_allowed", "That path is not valid")
        if (roots.isEmpty()) throw FilesError("path_not_allowed", "No folder is allowed yet. Add one in the Files plugin settings.")
        val target = (if (raw.startsWith("/")) File(raw) else File(roots.first(), raw)).canonicalFile
        if (isDenied(target) && roots.any { contains(it, target) }) {
            throw FilesError("path_not_allowed", "That folder is switched off for PAI in the Files plugin settings (${target.path})")
        }
        if (!inside(target)) {
            throw FilesError("path_not_allowed",
                "PAI can only use the folders allowed in the Files plugin settings (for example ${roots.first().path})")
        }
        return target
    }

    /** True when [f] (canonical) is within an allowed root and not shut out by a denied folder. */
    fun inside(f: File): Boolean {
        val root = roots.filter { contains(it, f) }.maxByOrNull { it.path.length } ?: return false
        return denied.none { contains(it, f) && it.path.length >= root.path.length }
    }

    private fun isDenied(f: File): Boolean = !inside(f) && denied.any { contains(it, f) }

    /** True when a denied folder sits somewhere below [dir]: acting on the whole of [dir] would reach into it. */
    fun hasDeniedInside(dir: File): Boolean = denied.any { it.path != dir.path && contains(dir, it) && !inside(it) }

    fun requireNoDeniedInside(dir: File) {
        if (dir.isDirectory && hasDeniedInside(dir)) {
            throw FilesError("path_not_allowed", "That folder contains a folder PAI must never touch (see the Files plugin settings), so it cannot be changed as a whole")
        }
    }

    fun isRoot(f: File): Boolean = roots.any { it.path == f.canonicalPath }

    /** Call before write, delete, move and copy. */
    fun requireWritable() {
        if (readOnly) throw FilesError("read_only", "The Files plugin is set to read-only, so PAI cannot change, copy, move or delete files. Turn off \"Read-only\" in the plugin settings to allow it.")
    }

    companion object {
        /** Prefix-sibling safe: "/a/b" does not contain "/a/bc". */
        fun contains(parent: File, f: File): Boolean = f.path == parent.path || f.path.startsWith(parent.path.trimEnd('/') + File.separator)
    }
}

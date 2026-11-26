package com.pai.plugins.files

import java.io.File

/**
 * Android's "All files access" (MANAGE_EXTERNAL_STORAGE) is needed for anything outside PAI's own app
 * folders (Pictures, Download, DCIM...). Pure logic so it is unit tested on the JVM.
 */
internal object SharedStorage {
    const val MESSAGE = "PAI needs Android's \"All files access\" to use that folder. Turn it on in " +
        "Settings > Special app access > All files access > PAI (or open the Files plugin settings in the PAI app and tap \"Open Android settings\"), then try again."

    /** True when [f] is outside every one of PAI's own app folders ([appRoots]). */
    fun isShared(f: File, appRoots: List<File>): Boolean =
        appRoots.none { FileJail.contains(it.canonicalFile, f.canonicalFile) }

    /** The paths this request touches, as the jail would resolve them (relative = under the first root). */
    fun touched(params: Map<String, Any?>, firstRoot: File?): List<File> =
        listOf("path", "source", "destination").mapNotNull { k ->
            val raw = (params[k] as? String)?.trim()?.takeIf { it.isNotEmpty() } ?: return@mapNotNull null
            if (raw.startsWith("/")) File(raw) else firstRoot?.let { File(it, raw) }
        }
}

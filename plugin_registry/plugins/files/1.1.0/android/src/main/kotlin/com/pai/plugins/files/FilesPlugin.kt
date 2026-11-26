package com.pai.plugins.files

import android.util.Log
import android.os.Build
import android.os.Environment
import com.pai.plugin.api.ConfigurablePlugin
import com.pai.plugin.api.PluginContext
import com.pai.plugin.api.PluginModule
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

/**
 * Files (`pai.files`): list, read, write, stat, delete, copy and move inside the folders the user allowed.
 * With no settings that is PAI's own folders (the app's external files folder, visible over USB, plus a
 * private one). Shared folders (Pictures, Download...) are added in the plugin settings and need Android's
 * "All files access". Denied folders and read-only are enforced here too, not only by the backend.
 * Capability ids and parameters match the desktop files plugin.
 */
class FilesPlugin : PluginModule, ConfigurablePlugin {

    override val name = "pai.files"
    override val capabilities = listOf("files.list", "files.read", "files.write", "files.stat", "files.delete", "files.copy", "files.move")
    // App-scoped storage needs no Android permission, so nothing is asked of the user.
    override val permissions = emptyList<String>()

    private var ops: FileOps? = null
    private var appRoots: List<File> = emptyList()
    private var firstRoot: File? = null

    /** Test seam: whether Android's "All files access" is on. */
    internal var allFilesAccess: () -> Boolean = { Build.VERSION.SDK_INT < 30 || Environment.isExternalStorageManager() }

    override fun attach(context: PluginContext) {
        appRoots = buildList {
            context.appContext.getExternalFilesDir(null)?.let { add(it) }       // /sdcard/Android/data/<app>/files
            add(File(context.filesDir, "user_files").apply { mkdirs() })
        }
        build(null)                                  // no settings yet: exactly 1.0.0 (PAI's own folders only)
    }

    /** The user's settings (allowedPaths, deniedPaths, readOnly). Called after attach and on every change. */
    override fun configure(config: Map<String, Any?>) = build(config)

    internal fun build(config: Map<String, Any?>?) {
        if (config == null || config.isEmpty()) {
            ops = FileOps(FileJail(appRoots)); firstRoot = appRoots.firstOrNull(); return
        }
        fun paths(k: String) = (config[k] as? List<*>).orEmpty().mapNotNull { (it as? String)?.trim()?.takeIf { s -> s.startsWith("/") } }.map { File(it) }
        val allowed = if (config.containsKey("allowedPaths")) paths("allowedPaths") else appRoots
        val jail = FileJail(allowed, paths("deniedPaths"), config["readOnly"] == true)
        ops = FileOps(jail); firstRoot = jail.roots.firstOrNull()
    }

    override suspend fun invoke(capability: String, params: Map<String, Any?>): Map<String, Any?> {
        val o = ops ?: return failure("no_context_attached", "The files plugin is not ready")
        if (!allFilesAccess() && SharedStorage.touched(params, firstRoot).any { SharedStorage.isShared(it, appRoots) }) {
            return failure("all_files_access_required", SharedStorage.MESSAGE)
        }
        return try {
            withContext(Dispatchers.IO) {
                when (capability) {
                    "files.list" -> o.list(str(params, "path"), bool(params["recursive"]), int(params["limit"], 500))
                    "files.read" -> o.read(str(params, "path"), long(params["maxBytes"], FileOps.DEFAULT_READ))
                    "files.write" -> o.write(str(params, "path"), (params["content"] ?: params["text"]) as? String
                        ?: throw FilesError("bad_parameter", "content is required"), bool(params["append"]), bool(params["createDirs"], true))
                    "files.stat" -> o.stat(str(params, "path"))
                    "files.delete" -> o.delete(str(params, "path"), bool(params["recursive"]))
                    "files.copy" -> o.copy(str(params, "source"), str(params, "destination"), bool(params["overwrite"]))
                    "files.move" -> o.move(str(params, "source"), str(params, "destination"), bool(params["overwrite"]))
                    else -> failure("unknown_capability", "Unknown capability: $capability")
                }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: FilesError) {
            failure(e.code, e.message ?: e.code)
        } catch (e: SecurityException) {
            failure("permission_denied", "Android has not allowed PAI to do that")
        } catch (t: Throwable) {
            Log.e(TAG, "invoke($capability) failed", t)
            failure("files_error", t.message ?: "That did not work")
        }
    }

    private fun str(p: Map<String, Any?>, k: String): String = (p[k] as? String)?.takeIf { it.isNotBlank() }
        ?: throw FilesError("bad_parameter", "$k is required")
    private fun bool(v: Any?, default: Boolean = false): Boolean = when (v) { null -> default; is Boolean -> v; is String -> v.equals("true", true); else -> default }
    private fun int(v: Any?, default: Int): Int = (v as? Number)?.toInt() ?: default
    private fun long(v: Any?, default: Long): Long = (v as? Number)?.toLong() ?: default
    private fun failure(code: String, message: String) = mapOf("ok" to false, "error" to code, "message" to message)

    override fun destroy() { ops = null; appRoots = emptyList(); firstRoot = null }

    private companion object { const val TAG = "PaiFilesKt" }
}

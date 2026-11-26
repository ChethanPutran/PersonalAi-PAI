package com.pai.plugins.files

import android.util.Log
import com.pai.plugin.api.PluginContext
import com.pai.plugin.api.PluginModule
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

/**
 * Files (`pai.files`): list, read, write, stat, delete, copy and move inside PAI's own folders on the phone
 * (the app's external files folder, visible over USB, plus a private one). Nothing outside them is
 * reachable: Android's shared storage needs a user-chosen folder (Storage Access Framework), which a
 * background capability cannot ask for. Capability ids and parameters match the desktop files plugin.
 */
class FilesPlugin : PluginModule {

    override val name = "pai.files"
    override val capabilities = listOf("files.list", "files.read", "files.write", "files.stat", "files.delete", "files.copy", "files.move")
    // App-scoped storage needs no Android permission, so nothing is asked of the user.
    override val permissions = emptyList<String>()

    private var ops: FileOps? = null

    override fun attach(context: PluginContext) {
        val roots = buildList {
            context.appContext.getExternalFilesDir(null)?.let { add(it) }       // /sdcard/Android/data/<app>/files
            add(File(context.filesDir, "user_files").apply { mkdirs() })
        }
        ops = FileOps(FileJail(roots))
    }

    override suspend fun invoke(capability: String, params: Map<String, Any?>): Map<String, Any?> {
        val o = ops ?: return failure("no_context_attached", "The files plugin is not ready")
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

    override fun destroy() { ops = null }

    private companion object { const val TAG = "PaiFilesKt" }
}

package com.pai.agent.permissions

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Maps a plugin's logical permissions (from its native manifest) to the
 * Android runtime permissions that back them, and shows the OS dialog.
 *
 * Only the permissions actually needed by the plugin being installed
 * are requested — nothing else. Every plugin that needs CAMERA will
 * trigger its own CAMERA dialog at its own install time.
 */
class PermissionManager(private val activity: Activity) {

    /** logical name  →  Android permission strings */
    private val logicalToOs: Map<String, List<String>> = buildMap {
        put("camera", listOf(Manifest.permission.CAMERA))
        put("microphone", listOf(Manifest.permission.RECORD_AUDIO))

        // "filesystem" plugins (camera, files) only touch PAI's own app-scoped folders, which need no Android
        // permission. It used to map to READ_MEDIA_IMAGES / READ_EXTERNAL_STORAGE, so installing the camera
        // plugin asked for access to the user's photos it never reads, and a "no" rolled the install back.
        put("filesystem", emptyList())

        put("contacts", listOf(Manifest.permission.READ_CONTACTS))

        put("notifications", buildList {
            if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.POST_NOTIFICATIONS)
        })

        put("location", listOf(
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.ACCESS_COARSE_LOCATION,
        ))

        // Background location only exists on Android 10+; must be requested
        // AFTER foreground location has been granted (Google Play policy).
        put("background_location", buildList {
            if (Build.VERSION.SDK_INT >= 29) add(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
        })

        // Bluetooth split in two on Android 12+.
        put("bluetooth", buildList {
            if (Build.VERSION.SDK_INT >= 31) {
                add(Manifest.permission.BLUETOOTH_CONNECT)
                add(Manifest.permission.BLUETOOTH_SCAN)
            } else {
                add(Manifest.permission.BLUETOOTH)
            }
        })
    }

    private var pendingCallback: ((Map<String, Boolean>) -> Unit)? = null

    // -----------------------------------------------------------------
    // Public API
    // -----------------------------------------------------------------

    /**
     * Show the OS dialog for each still-missing OS permission. Returns:
     *   {
     *     "ok": bool,             // true if every requested logical perm is granted
     *     "granted": [String],    // logical names fully granted
     *     "denied": [String],     // logical names denied (at least one OS perm missing)
     *   }
     *
     * Never throws. If there's nothing to request (already granted),
     * returns immediately with ok=true.
     */
    suspend fun request(logical: List<String>): Map<String, Any?> =
        withContext(Dispatchers.Main) {
            val osToLogical = mutableMapOf<String, MutableList<String>>()
            for (l in logical) {
                for (os in logicalToOs[l].orEmpty()) {
                    osToLogical.getOrPut(os) { mutableListOf() }.add(l)
                }
            }
            val osPerms = osToLogical.keys.toList()

            val missing = osPerms.filter {
                ContextCompat.checkSelfPermission(activity, it) !=
                    PackageManager.PERMISSION_GRANTED
            }

            if (missing.isEmpty()) {
                return@withContext mapOf(
                    "ok" to true,
                    "granted" to logical,
                    "denied" to emptyList<String>(),
                )
            }

            // Suspend here while the OS dialog is showing.
            val deferred = CompletableDeferred<Map<String, Boolean>>()
            pendingCallback = { deferred.complete(it) }

            ActivityCompat.requestPermissions(
                activity, missing.toTypedArray(), REQUEST_CODE,
            )

            val results = deferred.await()

            // A logical permission is "granted" only if ALL its OS
            // permissions were granted.
            val granted = mutableListOf<String>()
            val denied = mutableListOf<String>()
            for (l in logical) {
                val needed = logicalToOs[l].orEmpty()
                if (needed.all { results[it] == true }) granted += l
                else denied += l
            }

            mapOf(
                "ok" to denied.isEmpty(),
                "granted" to granted,
                "denied" to denied,
            )
        }

    /**
     * "All files access" (MANAGE_EXTERNAL_STORAGE): the Files plugin needs it for shared folders such as Pictures
     * and Download. It is not a runtime-dialog permission: the user switches it on in Android Settings.
     * Below Android 11 the ordinary storage permission is used instead.
     */
    fun allFilesAccessGranted(): Boolean =
        if (Build.VERSION.SDK_INT >= 30) android.os.Environment.isExternalStorageManager()
        else ContextCompat.checkSelfPermission(activity, Manifest.permission.READ_EXTERNAL_STORAGE) ==
            PackageManager.PERMISSION_GRANTED

    /** Opens the "All files access" screen for PAI (falls back to the list of apps, then to app details). */
    fun openAllFilesAccessSettings(): Boolean {
        val flags = android.content.Intent.FLAG_ACTIVITY_NEW_TASK
        val attempts = buildList {
            if (Build.VERSION.SDK_INT >= 30) {
                add(android.content.Intent(android.provider.Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                    android.net.Uri.parse("package:${activity.packageName}")))
                add(android.content.Intent(android.provider.Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION))
            }
            add(android.content.Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                android.net.Uri.parse("package:${activity.packageName}")))
        }
        for (i in attempts) {
            try { activity.startActivity(i.addFlags(flags)); return true } catch (_: Exception) { }
        }
        return false
    }

    /** Non-requesting check — used by PluginManager.invoke() before execution. */
    fun missing(logical: List<String>): List<String> =
        logical.filter { name ->
            logicalToOs[name].orEmpty().any {
                ContextCompat.checkSelfPermission(activity, it) !=
                    PackageManager.PERMISSION_GRANTED
            }
        }

    // -----------------------------------------------------------------

    /** Call from MainActivity.onRequestPermissionsResult. */
    fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        if (requestCode != REQUEST_CODE) return
        val results = permissions.indices.associate { i ->
            permissions[i] to (grantResults.getOrNull(i) == PackageManager.PERMISSION_GRANTED)
        }
        pendingCallback?.invoke(results)
        pendingCallback = null
    }

    companion object {
        const val REQUEST_CODE = 0xCA11
    }
}

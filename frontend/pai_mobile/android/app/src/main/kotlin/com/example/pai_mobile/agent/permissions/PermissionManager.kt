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

        // Scoped storage: API 33+ uses READ_MEDIA_*, older uses READ_EXTERNAL_STORAGE.
        put("filesystem", buildList {
            if (Build.VERSION.SDK_INT >= 33) {
                add(Manifest.permission.READ_MEDIA_IMAGES)
            } else {
                add(Manifest.permission.READ_EXTERNAL_STORAGE)
            }
        })

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

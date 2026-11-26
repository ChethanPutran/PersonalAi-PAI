package com.pai.plugins.phone

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Notification
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.AudioManager
import android.net.Uri
import android.os.Build
import android.util.Log
import android.view.KeyEvent
import com.pai.plugin.api.PluginContext
import com.pai.plugin.api.PluginModule
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Phone helpers (`pai.phone`): the Android counterpart of the desktop plugin's everyday actions.
 * Capability ids match the desktop plugin where the meaning is the same (clipboard.*, audio.volume,
 * media.control, notifications.send, app.launch/open/list) so a request works on either device.
 *
 * Every failure is `{"ok": false, "error": <code>, "message": <plain words>}`.
 */
class PhonePlugin : PluginModule {

    override val name = "pai.phone"
    override val capabilities = listOf(
        "clipboard.read", "clipboard.write", "notifications.send", "audio.volume", "media.control",
        "flashlight.set", "app.list", "app.launch", "app.open",
    )
    // clipboard / notifications are logical names the host maps to OS permissions (POST_NOTIFICATIONS on 13+).
    override val permissions = listOf("clipboard", "notifications")

    private var ctx: PluginContext? = null

    override fun attach(context: PluginContext) { ctx = context }

    override suspend fun invoke(capability: String, params: Map<String, Any?>): Map<String, Any?> {
        val c = ctx?.appContext ?: return failure("no_context_attached", "The phone plugin is not ready")
        return try {
            when (capability) {
                "clipboard.read" -> withContext(Dispatchers.Main) { clipboardRead(c) }
                "clipboard.write" -> withContext(Dispatchers.Main) { clipboardWrite(c, params) }
                "notifications.send" -> withContext(Dispatchers.IO) { notify(c, params) }
                "audio.volume" -> withContext(Dispatchers.IO) { volume(c, params) }
                "media.control" -> withContext(Dispatchers.IO) { media(c, params) }
                "flashlight.set" -> withContext(Dispatchers.IO) { flashlight(c, params) }
                "app.list" -> withContext(Dispatchers.IO) { appList(c, params) }
                "app.launch" -> withContext(Dispatchers.IO) { appLaunch(c, params) }
                "app.open" -> withContext(Dispatchers.IO) { appOpen(c, params) }
                else -> failure("unknown_capability", "Unknown capability: $capability")
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: PhoneError) {
            failure(e.code, e.message ?: e.code)
        } catch (e: SecurityException) {
            failure("permission_denied", "Android has not allowed PAI to do that (${e.message ?: "permission"})")
        } catch (t: Throwable) {
            Log.e(TAG, "invoke($capability) failed", t)
            failure("phone_error", t.message ?: "That did not work")
        }
    }

    // ------------------------------------------------------------------ clipboard (main thread)
    private fun clipboardRead(c: Context): Map<String, Any?> {
        val cm = c.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        if (!cm.hasPrimaryClip()) return ok("text" to "", "has_text" to false)
        // Android 10+ lets only the foreground app read the clipboard: null here means "not allowed right now".
        val clip = cm.primaryClip ?: return failure("clipboard_unavailable", "Android only lets PAI read the clipboard while PAI is on screen")
        val text = (0 until clip.itemCount).mapNotNull { clip.getItemAt(it).coerceToText(c)?.toString() }.firstOrNull { it.isNotEmpty() }.orEmpty()
        return ok("text" to text.take(MAX_CLIP), "has_text" to text.isNotEmpty(), "truncated" to (text.length > MAX_CLIP))
    }

    private fun clipboardWrite(c: Context, p: Map<String, Any?>): Map<String, Any?> {
        val text = (p["text"] as? String) ?: throw PhoneError("bad_parameter", "text is required")
        if (text.length > MAX_CLIP) throw PhoneError("bad_parameter", "That text is too long for the clipboard")
        (c.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(ClipData.newPlainText("PAI", text))
        return ok("length" to text.length)
    }

    // ------------------------------------------------------------------ notifications
    private fun notify(c: Context, p: Map<String, Any?>): Map<String, Any?> {
        val title = PhoneRules.text(p["title"], "title", 120)
        val message = PhoneRules.text(p["message"] ?: p["body"], "message", 1000)
        if (Build.VERSION.SDK_INT >= 33 && c.checkSelfPermission("android.permission.POST_NOTIFICATIONS") != PackageManager.PERMISSION_GRANTED) {
            throw PhoneError("permission_denied", "Notifications are not allowed for PAI. Turn them on in Android settings")
        }
        val nm = c.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val builder = if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "PAI assistant", NotificationManager.IMPORTANCE_DEFAULT))
            Notification.Builder(c, CHANNEL)
        } else {
            @Suppress("DEPRECATION") Notification.Builder(c)
        }
        val id = (System.currentTimeMillis() % Int.MAX_VALUE).toInt()
        nm.notify(id, builder.setSmallIcon(android.R.drawable.ic_dialog_info).setContentTitle(title)
            .setContentText(message).setStyle(Notification.BigTextStyle().bigText(message)).setAutoCancel(true).build())
        return ok("id" to id)
    }

    // ------------------------------------------------------------------ volume / media
    private fun streamOf(name: String) = when (name) {
        "ring" -> AudioManager.STREAM_RING
        "alarm" -> AudioManager.STREAM_ALARM
        "notification" -> AudioManager.STREAM_NOTIFICATION
        "voice_call" -> AudioManager.STREAM_VOICE_CALL
        else -> AudioManager.STREAM_MUSIC
    }

    private fun volume(c: Context, p: Map<String, Any?>): Map<String, Any?> {
        val am = c.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val action = PhoneRules.oneOf(p["action"], PhoneRules.VOLUME_ACTIONS, "action", "get")
        val streamName = PhoneRules.oneOf(p["stream"], PhoneRules.VOLUME_STREAMS, "stream", "music")
        val stream = streamOf(streamName)
        val min = if (Build.VERSION.SDK_INT >= 28) am.getStreamMinVolume(stream) else 0
        val max = am.getStreamMaxVolume(stream)
        when (action) {
            "set" -> {
                val level = (p["level"] as? Number)?.toInt() ?: throw PhoneError("bad_parameter", "level (0 to 100) is required to set the volume")
                am.setStreamVolume(stream, PhoneRules.levelToIndex(level, min, max), 0)
            }
            "up" -> am.adjustStreamVolume(stream, AudioManager.ADJUST_RAISE, 0)
            "down" -> am.adjustStreamVolume(stream, AudioManager.ADJUST_LOWER, 0)
            "mute" -> am.adjustStreamVolume(stream, AudioManager.ADJUST_MUTE, 0)
            "unmute" -> am.adjustStreamVolume(stream, AudioManager.ADJUST_UNMUTE, 0)
        }
        val now = am.getStreamVolume(stream)
        return ok("stream" to streamName, "level" to PhoneRules.indexToLevel(now, min, max), "muted" to
            (if (Build.VERSION.SDK_INT >= 23) am.isStreamMute(stream) else now == 0))
    }

    private fun media(c: Context, p: Map<String, Any?>): Map<String, Any?> {
        val action = PhoneRules.oneOf(p["action"], PhoneRules.MEDIA_ACTIONS, "action")
        val key = when (action) {
            "play" -> KeyEvent.KEYCODE_MEDIA_PLAY
            "pause" -> KeyEvent.KEYCODE_MEDIA_PAUSE
            "toggle" -> KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE
            "next" -> KeyEvent.KEYCODE_MEDIA_NEXT
            "previous" -> KeyEvent.KEYCODE_MEDIA_PREVIOUS
            else -> KeyEvent.KEYCODE_MEDIA_STOP
        }
        val am = c.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        am.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, key))
        am.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, key))
        return ok("action" to action)
    }

    // ------------------------------------------------------------------ flashlight
    private fun flashlight(c: Context, p: Map<String, Any?>): Map<String, Any?> {
        val on = p["on"] as? Boolean ?: throw PhoneError("bad_parameter", "on (true or false) is required")
        val cm = c.getSystemService(Context.CAMERA_SERVICE) as CameraManager
        val id = cm.cameraIdList.firstOrNull { cm.getCameraCharacteristics(it).get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true }
            ?: throw PhoneError("no_flashlight", "This phone has no flashlight")
        cm.setTorchMode(id, on)
        return ok("on" to on)
    }

    // ------------------------------------------------------------------ apps
    private fun launchable(c: Context): List<AppInfo> {
        val pm = c.packageManager
        val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val infos = if (Build.VERSION.SDK_INT >= 33) {
            pm.queryIntentActivities(intent, PackageManager.ResolveInfoFlags.of(0))
        } else {
            @Suppress("DEPRECATION") pm.queryIntentActivities(intent, 0)
        }
        return infos.map { AppInfo(it.activityInfo.packageName, it.loadLabel(pm).toString()) }.distinctBy { it.packageName }
    }

    private fun appList(c: Context, p: Map<String, Any?>): Map<String, Any?> {
        val limit = (p["limit"] as? Number)?.toInt() ?: 100
        val apps = PhoneRules.filterApps(launchable(c), p["query"] as? String, limit)
        return ok("apps" to apps.map { mapOf("id" to it.packageName, "name" to it.label) })
    }

    private fun appLaunch(c: Context, p: Map<String, Any?>): Map<String, Any?> {
        val query = ((p["id"] ?: p["package"] ?: p["name"] ?: p["app"]) as? String)?.trim().orEmpty()
        if (query.isEmpty()) throw PhoneError("bad_parameter", "name (or id) of the app is required")
        val app = PhoneRules.matchApp(query, launchable(c)) ?: throw PhoneError("app_not_found", "No installed app matches \"$query\"")
        val intent = c.packageManager.getLaunchIntentForPackage(app.packageName)
            ?: throw PhoneError("app_not_launchable", "${app.label} cannot be opened")
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        c.startActivity(intent)
        return ok("launched" to mapOf("id" to app.packageName, "name" to app.label))
    }

    private fun appOpen(c: Context, p: Map<String, Any?>): Map<String, Any?> {
        val uri = PhoneRules.checkOpenable(((p["url"] ?: p["uri"]) as? String) ?: throw PhoneError("bad_parameter", "url is required"))
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(uri)).addCategory(Intent.CATEGORY_BROWSABLE).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (intent.resolveActivity(c.packageManager) == null) throw PhoneError("no_app_for_link", "No app on this phone can open that link")
        c.startActivity(intent)
        return ok("opened" to uri)
    }

    private fun ok(vararg kv: Pair<String, Any?>): Map<String, Any?> = mapOf("ok" to true) + kv.toMap()
    private fun failure(code: String, message: String): Map<String, Any?> = mapOf("ok" to false, "error" to code, "message" to message)

    override fun destroy() { ctx = null }

    private companion object {
        const val TAG = "PaiPhoneKt"
        const val CHANNEL = "pai_assistant"
        const val MAX_CLIP = 100_000
    }
}

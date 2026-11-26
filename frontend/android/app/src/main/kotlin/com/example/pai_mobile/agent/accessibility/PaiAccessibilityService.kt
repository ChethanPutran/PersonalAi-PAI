package com.pai.agent.accessibility

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Bitmap
import android.graphics.Path
import android.os.Build
import android.util.DisplayMetrics
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.pai.plugin.api.AccessibilityBridge
import com.pai.plugin.api.GestureStroke
import com.pai.plugin.api.PaiHost
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

/**
 * The host's Accessibility Service. It does nothing by itself: it exists only while the user has switched
 * it on in Android Settings, and then exposes the screen and gestures to the computer-control plugin
 * through [PaiHost]. It listens to window changes only (to know the foreground app), never to typing.
 */
class PaiAccessibilityService : AccessibilityService(), AccessibilityBridge {

    @Volatile private var lastPackage: String? = null

    override fun onServiceConnected() {
        super.onServiceConnected()
        PaiHost.accessibility = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event?.eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) return
        val pkg = event.packageName?.toString() ?: return
        // The notification shade / keyboard are windows too; they are not "the app the user is in".
        if (pkg != "com.android.systemui" && !pkg.contains("inputmethod")) lastPackage = pkg
    }

    override fun onInterrupt() {}

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        PaiHost.accessibility = null
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        PaiHost.accessibility = null
        super.onDestroy()
    }

    // ---------------------------------------------------------------- AccessibilityBridge

    override fun activeRoot(): AccessibilityNodeInfo? = rootInActiveWindow

    override fun activePackage(): String? = rootInActiveWindow?.packageName?.toString() ?: lastPackage

    override fun screenSize(): Pair<Int, Int> {
        val m = DisplayMetrics()
        @Suppress("DEPRECATION")
        (getSystemService(WINDOW_SERVICE) as android.view.WindowManager).defaultDisplay.getRealMetrics(m)
        return m.widthPixels to m.heightPixels
    }

    override suspend fun dispatchGesture(strokes: List<GestureStroke>): Boolean = suspendCancellableCoroutine { cont ->
        val builder = GestureDescription.Builder()
        try {
            for (s in strokes) {
                val path = Path().apply {
                    moveTo(s.points.first().first, s.points.first().second)
                    if (s.points.size == 1) lineTo(s.points.first().first, s.points.first().second)   // a tap needs a path
                    else s.points.drop(1).forEach { lineTo(it.first, it.second) }
                }
                builder.addStroke(GestureDescription.StrokeDescription(path, s.startDelayMs, s.durationMs.coerceAtLeast(1)))
            }
        } catch (e: IllegalArgumentException) {                 // e.g. a stroke longer than the system allows
            cont.resume(false); return@suspendCancellableCoroutine
        }
        val accepted = dispatchGesture(builder.build(), object : GestureResultCallback() {
            override fun onCompleted(g: GestureDescription?) { if (cont.isActive) cont.resume(true) }
            override fun onCancelled(g: GestureDescription?) { if (cont.isActive) cont.resume(false) }
        }, null)
        if (!accepted && cont.isActive) cont.resume(false)
    }

    override suspend fun takeScreenshot(): Bitmap? {
        if (Build.VERSION.SDK_INT < 30) return null
        return suspendCancellableCoroutine { cont ->
            takeScreenshot(Display.DEFAULT_DISPLAY, mainExecutor, object : TakeScreenshotCallback {
                override fun onSuccess(result: ScreenshotResult) {
                    val hw = Bitmap.wrapHardwareBuffer(result.hardwareBuffer, result.colorSpace)
                    val soft = hw?.copy(Bitmap.Config.ARGB_8888, false)       // software copy: the buffer is closed next
                    hw?.recycle(); result.hardwareBuffer.close()
                    if (cont.isActive) cont.resume(soft)
                }
                override fun onFailure(errorCode: Int) { if (cont.isActive) cont.resume(null) }
            })
        }
    }
}

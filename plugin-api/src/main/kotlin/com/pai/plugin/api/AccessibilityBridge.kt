package com.pai.plugin.api

import android.graphics.Bitmap
import android.view.accessibility.AccessibilityNodeInfo

/** One finger stroke: [points] are screen pixels; a single point is a tap/press, several are a swipe. */
data class GestureStroke(val points: List<Pair<Float, Float>>, val startDelayMs: Long = 0L, val durationMs: Long = 50L)

/**
 * What the host's Accessibility Service offers plugins (computer control). The service lives in the host
 * app (only an app can declare one) and registers itself in [PaiHost] while the user has it switched on in
 * Android Settings; [PaiHost.accessibility] is null otherwise, and plugins must say so, not pretend.
 */
interface AccessibilityBridge {
    /** The root of the window the user is looking at, or null (do not recycle it). */
    fun activeRoot(): AccessibilityNodeInfo?

    /** Package name of the app on screen, or null when unknown. */
    fun activePackage(): String?

    fun screenSize(): Pair<Int, Int>

    fun performGlobalAction(action: Int): Boolean

    /** Perform the strokes together; true only if the system completed them (false: cancelled or refused). */
    suspend fun dispatchGesture(strokes: List<GestureStroke>): Boolean

    /** A screenshot of the default display (Android 11+), or null when not possible. */
    suspend fun takeScreenshot(): Bitmap?
}

/** Process-wide hooks the host fills in for plugins. */
object PaiHost {
    @Volatile
    var accessibility: AccessibilityBridge? = null
}

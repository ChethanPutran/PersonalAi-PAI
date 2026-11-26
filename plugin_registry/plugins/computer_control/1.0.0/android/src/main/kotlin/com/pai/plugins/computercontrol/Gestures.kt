package com.pai.plugins.computercontrol

import com.pai.plugin.api.GestureStroke
import kotlin.math.roundToInt

/** Plans finger strokes in screen pixels. Pure, so the geometry is unit tested. */
internal object Gestures {
    const val TAP_MS = 50L
    const val DEFAULT_LONG_PRESS_MS = 600L
    const val MAX_LONG_PRESS_MS = 5_000L
    const val DEFAULT_SWIPE_MS = 300L
    const val MAX_SWIPE_MS = 3_000L
    const val DOUBLE_TAP_GAP_MS = 80L

    fun clampPoint(x: Float, y: Float, w: Int, h: Int): Pair<Float, Float> =
        x.coerceIn(0f, (w - 1).coerceAtLeast(0).toFloat()) to y.coerceIn(0f, (h - 1).coerceAtLeast(0).toFloat())

    fun tap(x: Float, y: Float, count: Int): List<GestureStroke> {
        val n = count.coerceIn(1, 2)
        return List(n) { GestureStroke(listOf(x to y), startDelayMs = it * (TAP_MS + DOUBLE_TAP_GAP_MS), durationMs = TAP_MS) }
    }

    fun longPress(x: Float, y: Float, durationMs: Long): List<GestureStroke> =
        listOf(GestureStroke(listOf(x to y), durationMs = durationMs.coerceIn(TAP_MS, MAX_LONG_PRESS_MS)))

    fun swipe(fx: Float, fy: Float, tx: Float, ty: Float, durationMs: Long): List<GestureStroke> =
        listOf(GestureStroke(listOf(fx to fy, tx to ty), durationMs = durationMs.coerceIn(50L, MAX_SWIPE_MS)))

    /**
     * One scroll step: a swipe the other way (scrolling DOWN moves the content up, so the finger travels from
     * the lower part of the region to the upper part), about 60% of the region long and always inside it.
     * `computer.scroll` repeats it `amount` times.
     */
    fun scroll(direction: String, region: IntArray): GestureStroke {
        val (l, t, r, b) = region.toList()
        val cx = (l + r) / 2f; val cy = (t + b) / 2f
        val dx = (r - l) * 0.3f; val dy = (b - t) * 0.3f
        val (from, to) = when (direction) {
            "down" -> (cx to cy + dy) to (cx to cy - dy)
            "up" -> (cx to cy - dy) to (cx to cy + dy)
            "right" -> (cx + dx to cy) to (cx - dx to cy)
            "left" -> (cx - dx to cy) to (cx + dx to cy)
            else -> throw ControlError("bad_parameter", "direction must be up, down, left or right")
        }
        return GestureStroke(listOf(from, to), durationMs = DEFAULT_SWIPE_MS)
    }

    fun duration(raw: Any?, default: Long, max: Long): Long = when (raw) {
        null -> default
        is Number -> raw.toLong().coerceIn(50L, max)
        else -> throw ControlError("bad_parameter", "duration_ms must be a number of milliseconds")
    }

    fun intIn(raw: Any?, name: String, default: Int, min: Int, max: Int): Int = when (raw) {
        null -> default
        is Number -> raw.toDouble().roundToInt().coerceIn(min, max)
        else -> throw ControlError("bad_parameter", "$name must be a number")
    }
}

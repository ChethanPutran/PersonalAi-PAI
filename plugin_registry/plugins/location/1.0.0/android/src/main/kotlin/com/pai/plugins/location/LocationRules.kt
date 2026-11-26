package com.pai.plugins.location

internal class LocationError(val code: String, message: String) : Exception(message)

internal data class Fix(val latitude: Double, val longitude: Double, val accuracyM: Float?, val provider: String, val timeMs: Long)

internal object LocationRules {
    const val DEFAULT_TIMEOUT_MS = 10_000L
    const val MAX_TIMEOUT_MS = 30_000L
    const val MAX_CACHED_AGE_MS = 120_000L        // a "last known" fix older than 2 minutes is not "where I am"

    fun timeout(raw: Any?): Long = when (raw) {
        null -> DEFAULT_TIMEOUT_MS
        is Number -> raw.toLong().coerceIn(1_000L, MAX_TIMEOUT_MS)
        else -> throw LocationError("bad_parameter", "timeoutMs must be a number of milliseconds")
    }

    fun precision(raw: Any?): String {
        val v = (raw as? String)?.lowercase()?.trim().orEmpty().ifEmpty { "high" }
        if (v !in setOf("high", "balanced", "last_known")) throw LocationError("bad_parameter", "precision must be high, balanced or last_known")
        return v
    }

    fun validCoordinates(lat: Double, lon: Double) = lat in -90.0..90.0 && lon in -180.0..180.0 && !(lat == 0.0 && lon == 0.0)

    /** The best of several fixes: newest wins unless an older one is far more accurate. */
    fun pickBest(fixes: List<Fix>, nowMs: Long): Fix? {
        val usable = fixes.filter { validCoordinates(it.latitude, it.longitude) }
        if (usable.isEmpty()) return null
        return usable.minByOrNull { score(it, nowMs) }
    }

    private fun score(f: Fix, nowMs: Long): Double {
        val ageS = ((nowMs - f.timeMs).coerceAtLeast(0)) / 1000.0
        return (f.accuracyM ?: 5000f).toDouble() + ageS * 10.0          // 10 m of accuracy is worth 1 s of age
    }

    fun toResult(fix: Fix, nowMs: Long): Map<String, Any?> = mapOf(
        "ok" to true, "latitude" to fix.latitude, "longitude" to fix.longitude, "accuracy_m" to fix.accuracyM,
        "provider" to fix.provider, "time_ms" to fix.timeMs, "age_ms" to (nowMs - fix.timeMs).coerceAtLeast(0))
}

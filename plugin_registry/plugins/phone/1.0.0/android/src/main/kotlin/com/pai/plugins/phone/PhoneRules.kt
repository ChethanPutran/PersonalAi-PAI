package com.pai.plugins.phone

/** A failure the user can read: a stable [code] plus plain words. */
internal class PhoneError(val code: String, message: String) : Exception(message)

internal data class AppInfo(val packageName: String, val label: String)

/** Pure rules (no Android types): unit tested on the JVM. */
internal object PhoneRules {

    /** Schemes `app.open` may open. Anything else (intent:, file:, content:, javascript: ...) is refused. */
    private val OPENABLE = setOf("http", "https", "mailto", "tel", "geo", "sms", "smsto")

    fun checkOpenable(uri: String): String {
        val text = uri.trim()
        val scheme = text.substringBefore(':', "").lowercase()
        if (text.isEmpty() || !text.contains(':') || scheme !in OPENABLE) {
            throw PhoneError("scheme_not_allowed", "Only these links can be opened: ${OPENABLE.sorted().joinToString(", ")}")
        }
        if (text.length > 2000) throw PhoneError("bad_parameter", "That link is too long")
        return text
    }

    private fun norm(s: String) = s.lowercase().filter { it.isLetterOrDigit() }

    /**
     * The installed app a spoken/typed name most likely means: exact label, then label starting with the
     * name, then containing it, then package name. Null when nothing is close (never launches a guess).
     */
    fun matchApp(query: String, apps: List<AppInfo>): AppInfo? {
        val q = norm(query)
        if (q.isEmpty()) return null
        return apps.firstOrNull { norm(it.label) == q }
            ?: apps.filter { norm(it.label).startsWith(q) }.minByOrNull { it.label.length }
            ?: apps.filter { norm(it.label).contains(q) }.minByOrNull { it.label.length }
            ?: apps.firstOrNull { it.packageName.equals(query.trim(), true) }
            ?: apps.firstOrNull { it.packageName.lowercase().substringAfterLast('.') == q }
    }

    fun filterApps(apps: List<AppInfo>, query: String?, limit: Int): List<AppInfo> {
        val q = query?.let(::norm).orEmpty()
        val hits = if (q.isEmpty()) apps else apps.filter { norm(it.label).contains(q) || it.packageName.lowercase().contains(q) }
        return hits.sortedBy { it.label.lowercase() }.take(limit.coerceIn(1, 500))
    }

    /** 0..100 percent -> the stream's own step range, never below the minimum and never above the maximum. */
    fun levelToIndex(percent: Int, min: Int, max: Int): Int =
        (min + Math.round(percent.coerceIn(0, 100) / 100.0 * (max - min)).toInt()).coerceIn(min, max)

    fun indexToLevel(index: Int, min: Int, max: Int): Int =
        if (max <= min) 0 else Math.round((index - min).toDouble() / (max - min) * 100).toInt().coerceIn(0, 100)

    val VOLUME_STREAMS = listOf("music", "ring", "alarm", "notification", "voice_call")
    val VOLUME_ACTIONS = listOf("get", "set", "up", "down", "mute", "unmute")
    val MEDIA_ACTIONS = listOf("play", "pause", "toggle", "next", "previous", "stop")

    fun oneOf(raw: Any?, allowed: List<String>, name: String, default: String? = null): String {
        val v = (raw as? String)?.lowercase()?.trim().orEmpty().ifEmpty { default ?: "" }
        if (v !in allowed) throw PhoneError("bad_parameter", "$name must be one of: ${allowed.joinToString(", ")}")
        return v
    }

    fun text(raw: Any?, name: String, max: Int): String {
        val v = (raw as? String)?.trim().orEmpty()
        if (v.isEmpty()) throw PhoneError("bad_parameter", "$name is required")
        return v.take(max)
    }
}

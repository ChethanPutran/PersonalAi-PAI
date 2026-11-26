package com.pai.plugins.computercontrol

/** Keys the Android implementation understands (the contract's global actions plus ENTER / DELETE). */
internal enum class GlobalKey { BACK, HOME, RECENTS, NOTIFICATIONS, QUICK_SETTINGS, ENTER, DELETE }

internal object Keys {
    private val ALIASES = mapOf(
        "back" to GlobalKey.BACK, "home" to GlobalKey.HOME, "recents" to GlobalKey.RECENTS, "overview" to GlobalKey.RECENTS,
        "notifications" to GlobalKey.NOTIFICATIONS, "quick_settings" to GlobalKey.QUICK_SETTINGS, "quicksettings" to GlobalKey.QUICK_SETTINGS,
        "enter" to GlobalKey.ENTER, "return" to GlobalKey.ENTER, "delete" to GlobalKey.DELETE, "backspace" to GlobalKey.DELETE)

    fun parse(raw: Any?): GlobalKey {
        val k = (raw as? String)?.trim()?.lowercase()?.replace(' ', '_').orEmpty()
        if (k.isEmpty()) throw ControlError("bad_parameter", "keys is required")
        return ALIASES[k] ?: throw ControlError("unsupported_key",
            "This phone supports the keys: BACK, HOME, RECENTS, NOTIFICATIONS, QUICK_SETTINGS, ENTER and DELETE (not \"$raw\"). " +
                "Use computer.type to enter text and computer.click to press buttons.")
    }
}

internal object TextEntry {
    const val MAX_TYPE = 5_000

    fun text(raw: Any?): String {
        val t = raw as? String ?: throw ControlError("bad_parameter", "text is required")
        if (t.length > MAX_TYPE) throw ControlError("bad_parameter", "That is too much text to type at once (limit $MAX_TYPE characters)")
        return t
    }

    /** The field's new content. Placeholder/hint text (reported as the field's text when it is empty) is not content. */
    fun compose(existing: CharSequence?, typed: String, clearFirst: Boolean, showingHint: Boolean): String =
        if (clearFirst || showingHint) typed else (existing?.toString() ?: "") + typed

    fun deleteLast(existing: CharSequence?): String = existing?.toString()?.dropLast(1) ?: ""
}

internal object ImageRules {
    /** Scale (w, h) so the longest side is at most [maxSide]; never enlarges. */
    fun targetSize(w: Int, h: Int, maxSide: Int): Pair<Int, Int> {
        val longest = maxOf(w, h)
        if (longest <= maxSide || longest <= 0) return w to h
        val f = maxSide.toDouble() / longest
        return Math.max(1, Math.round(w * f).toInt()) to Math.max(1, Math.round(h * f).toInt())
    }
}

/** Apps: matching a spoken name to an installed app (kept here: each plugin is a separate dex). */
internal data class AppInfo(val packageName: String, val label: String)

internal object Apps {
    private fun norm(s: String) = s.lowercase().filter { it.isLetterOrDigit() }

    fun match(query: String, apps: List<AppInfo>): AppInfo? {
        val q = norm(query)
        if (q.isEmpty()) return null
        return apps.firstOrNull { norm(it.label) == q }
            ?: apps.filter { norm(it.label).startsWith(q) }.minByOrNull { it.label.length }
            ?: apps.filter { norm(it.label).contains(q) }.minByOrNull { it.label.length }
            ?: apps.firstOrNull { it.packageName.equals(query.trim(), true) }
    }

    fun filter(apps: List<AppInfo>, query: String?, limit: Int = 200): List<AppInfo> {
        val q = query?.let(::norm).orEmpty()
        return (if (q.isEmpty()) apps else apps.filter { norm(it.label).contains(q) || it.packageName.lowercase().contains(q) })
            .sortedBy { it.label.lowercase() }.take(limit)
    }
}

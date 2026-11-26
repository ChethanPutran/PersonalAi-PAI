package com.pai.plugins.deviceinfo

internal object InfoRules {
    val SECTIONS = listOf("system", "battery", "network", "storage")

    /** Which sections were asked for: none given means all; unknown names are an error, not ignored. */
    fun sections(raw: Any?): List<String> {
        val list = when (raw) {
            null -> return SECTIONS
            is String -> raw.split(',')
            is List<*> -> raw.map { it.toString() }
            else -> throw IllegalArgumentException("sections must be a list of names")
        }.map { it.trim().lowercase() }.filter { it.isNotEmpty() }
        if (list.isEmpty()) return SECTIONS
        val bad = list.filter { it !in SECTIONS }
        require(bad.isEmpty()) { "unknown section(s): ${bad.joinToString()} (use: ${SECTIONS.joinToString()})" }
        return list.distinct()
    }

    fun batteryPercent(level: Int, scale: Int): Int? = if (level < 0 || scale <= 0) null else Math.round(level * 100f / scale).coerceIn(0, 100)

    fun usedPercent(total: Long, free: Long): Int = if (total <= 0) 0 else Math.round((total - free).coerceAtLeast(0) * 100f / total).coerceIn(0, 100)

    /** 1.5 GB style text for people (the exact byte counts are returned too). */
    fun human(bytes: Long): String {
        if (bytes < 1024) return "$bytes B"
        val units = listOf("KB", "MB", "GB", "TB")
        var v = bytes.toDouble(); var i = -1
        while (v >= 1024 && i < units.lastIndex) { v /= 1024; i++ }
        return String.format(java.util.Locale.US, "%.1f %s", v, units[i])
    }
}

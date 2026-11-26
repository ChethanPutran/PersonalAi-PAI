package com.pai.plugins.contacts

internal class ContactsError(val code: String, message: String) : Exception(message)

/** One row of the contacts database (a contact has one row per phone number or email). */
internal data class Row(val id: String, val name: String, val kind: String, val value: String)

internal object ContactRules {
    const val DEFAULT_LIMIT = 20
    const val MAX_LIMIT = 100

    fun limit(raw: Any?): Int = ((raw as? Number)?.toInt() ?: DEFAULT_LIMIT).coerceIn(1, MAX_LIMIT)

    /** Text for a SQL LIKE: wildcards the user typed are escaped so "50%" does not match everything. */
    fun likePattern(query: String): String = "%" + query.trim().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

    fun query(raw: Any?): String {
        val q = (raw as? String)?.trim().orEmpty()
        if (q.length < 2) throw ContactsError("bad_parameter", "query needs at least 2 characters (a name or part of a number)")
        return q.take(80)
    }

    fun digits(s: String) = s.filter { it.isDigit() }

    /** Group database rows into contacts: name, de-duplicated phone numbers and emails. */
    fun group(rows: List<Row>, limit: Int): List<Map<String, Any?>> {
        val byId = LinkedHashMap<String, MutableList<Row>>()
        for (r in rows) byId.getOrPut(r.id) { mutableListOf() }.add(r)
        return byId.entries.take(limit).map { (id, rs) ->
            val phones = rs.filter { it.kind == "phone" }.map { it.value.trim() }.distinctBy { digits(it).takeLast(10).ifEmpty { it } }
            mapOf("id" to id, "name" to rs.first().name, "phones" to phones, "emails" to rs.filter { it.kind == "email" }.map { it.value.trim() }.distinct())
        }
    }
}

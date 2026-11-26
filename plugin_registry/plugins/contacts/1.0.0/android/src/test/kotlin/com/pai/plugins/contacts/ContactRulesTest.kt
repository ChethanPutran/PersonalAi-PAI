package com.pai.plugins.contacts

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test

class ContactRulesTest {
    private fun refused(block: () -> Unit): String = try { block(); fail("expected a ContactsError"); "" } catch (e: ContactsError) { e.code }

    @Test fun `wildcards typed by the user are escaped`() {
        assertEquals("%50\\%%", ContactRules.likePattern(" 50% "))
        assertEquals("%a\\_b%", ContactRules.likePattern("a_b"))
        assertEquals("%a\\\\b%", ContactRules.likePattern("a\\b"))
    }

    @Test fun `a query needs a few characters and is bounded`() {
        assertEquals("sam", ContactRules.query(" sam "))
        assertEquals("bad_parameter", refused { ContactRules.query("s") })
        assertEquals("bad_parameter", refused { ContactRules.query(null) })
        assertEquals(80, ContactRules.query("x".repeat(500)).length)
        assertEquals(20, ContactRules.limit(null)); assertEquals(100, ContactRules.limit(9999)); assertEquals(1, ContactRules.limit(0))
    }

    @Test fun `rows become contacts with de-duplicated numbers and emails`() {
        val rows = listOf(
            Row("1", "Sam", "phone", "+91 98765 43210"), Row("1", "Sam", "phone", "098765-43210"),
            Row("1", "Sam", "phone", "+1 555 0100"), Row("1", "Sam", "email", "sam@x.com"), Row("1", "Sam", "email", "sam@x.com"),
            Row("2", "Priya", "phone", "12345"))
        val c = ContactRules.group(rows, 10)
        assertEquals(2, c.size)
        assertEquals(listOf("+91 98765 43210", "+1 555 0100"), c[0]["phones"])          // same number written two ways: one entry
        assertEquals(listOf("sam@x.com"), c[0]["emails"])
        assertEquals("Priya", c[1]["name"])
        assertEquals(1, ContactRules.group(rows, 1).size)
    }

    @Test fun `the plugin is read-only and needs the contacts permission`() = runBlocking {
        val p = ContactsPlugin()
        assertEquals(setOf("contacts.search", "contacts.get"), p.capabilities.toSet()); assertEquals(listOf("contacts"), p.permissions)
        assertEquals("no_context_attached", p.invoke("contacts.search", mapOf("query" to "sam"))["error"])
    }
}

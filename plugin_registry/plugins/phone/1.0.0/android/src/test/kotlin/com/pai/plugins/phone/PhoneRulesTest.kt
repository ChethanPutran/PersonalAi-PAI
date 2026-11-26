package com.pai.plugins.phone

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class PhoneRulesTest {
    private val apps = listOf(
        AppInfo("com.android.chrome", "Chrome"), AppInfo("com.whatsapp", "WhatsApp"),
        AppInfo("com.google.android.apps.maps", "Maps"), AppInfo("com.google.android.gm", "Gmail"),
        AppInfo("com.android.settings", "Settings"), AppInfo("com.android.settings.intelligence", "Settings Suggestions"),
    )

    private fun refused(block: () -> Unit): PhoneError =
        try { block(); fail("expected a PhoneError"); throw IllegalStateException() } catch (e: PhoneError) { e }

    @Test fun `links are opened only for safe schemes`() {
        assertEquals("https://example.com/a?b=1", PhoneRules.checkOpenable(" https://example.com/a?b=1 "))
        assertEquals("tel:+911234", PhoneRules.checkOpenable("tel:+911234"))
        for (bad in listOf("intent://x#Intent;end", "file:///etc/passwd", "content://sms/inbox", "javascript:alert(1)", "market://x", "no scheme", "")) {
            assertEquals(bad, "scheme_not_allowed", refused { PhoneRules.checkOpenable(bad) }.code)
        }
        assertEquals("bad_parameter", refused { PhoneRules.checkOpenable("https://" + "a".repeat(2100)) }.code)
    }

    @Test fun `app names resolve like a person would say them`() {
        assertEquals("com.whatsapp", PhoneRules.matchApp("whatsapp", apps)?.packageName)
        assertEquals("com.whatsapp", PhoneRules.matchApp("WhatsApp", apps)?.packageName)
        assertEquals("com.android.settings", PhoneRules.matchApp("settings", apps)?.packageName)   // exact beats prefix
        assertEquals("com.google.android.gm", PhoneRules.matchApp("gma", apps)?.packageName)       // prefix
        assertEquals("com.google.android.apps.maps", PhoneRules.matchApp("com.google.android.apps.maps", apps)?.packageName)
        assertNull(PhoneRules.matchApp("instagram", apps))                                          // never guess
        assertNull(PhoneRules.matchApp("  ", apps))
    }

    @Test fun `listing filters sorts and limits`() {
        assertEquals(setOf("Gmail", "Maps"), PhoneRules.filterApps(apps, "google", 10).map { it.label }.toSet())   // matches the package name too
        assertEquals(apps.size, PhoneRules.filterApps(apps, null, 100).size)
        assertEquals(2, PhoneRules.filterApps(apps, null, 2).size)
        assertEquals(listOf("Chrome"), PhoneRules.filterApps(apps, "chrome", 10).map { it.label })
        assertTrue(PhoneRules.filterApps(apps, null, 100).map { it.label.lowercase() }.let { it == it.sorted() })
    }

    @Test fun `volume maps percent to the stream's own steps and back`() {
        assertEquals(0, PhoneRules.levelToIndex(0, 0, 15))
        assertEquals(15, PhoneRules.levelToIndex(100, 0, 15))
        assertEquals(8, PhoneRules.levelToIndex(50, 0, 15))
        assertEquals(1, PhoneRules.levelToIndex(0, 1, 7))            // streams with a minimum above 0
        assertEquals(7, PhoneRules.levelToIndex(500, 1, 7))          // out of range is clamped
        assertEquals(0, PhoneRules.levelToIndex(-10, 0, 15))
        assertEquals(100, PhoneRules.indexToLevel(15, 0, 15))
        assertEquals(0, PhoneRules.indexToLevel(0, 0, 0))
    }

    @Test fun `choices are validated with a readable message`() {
        assertEquals("up", PhoneRules.oneOf("UP", PhoneRules.VOLUME_ACTIONS, "action"))
        assertEquals("get", PhoneRules.oneOf(null, PhoneRules.VOLUME_ACTIONS, "action", "get"))
        val e = refused { PhoneRules.oneOf("louder", PhoneRules.VOLUME_ACTIONS, "action") }
        assertEquals("bad_parameter", e.code); assertTrue(e.message!!.contains("get, set"))
        assertEquals("bad_parameter", refused { PhoneRules.text("  ", "title", 10) }.code)
        assertEquals("abc", PhoneRules.text("abcdef", "title", 3))
    }
}

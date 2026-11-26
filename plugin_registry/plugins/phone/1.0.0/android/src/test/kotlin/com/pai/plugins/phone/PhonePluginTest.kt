package com.pai.plugins.phone

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PhonePluginTest {
    @Test fun `declares the capabilities the manifest advertises`() {
        val p = PhonePlugin()
        assertEquals("pai.phone", p.name)
        assertEquals(setOf("clipboard.read", "clipboard.write", "notifications.send", "audio.volume", "media.control",
            "flashlight.set", "app.list", "app.launch", "app.open"), p.capabilities.toSet())
    }

    @Test fun `without a context it fails readably instead of crashing`() = runBlocking {
        val r = PhonePlugin().invoke("app.list", emptyMap())
        assertEquals(false, r["ok"]); assertEquals("no_context_attached", r["error"]); assertTrue(r["message"] is String)
    }
}

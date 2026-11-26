package com.pai.plugins.deviceinfo

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.Test

class InfoRulesTest {
    @Test fun `no sections means all of them and names are validated`() {
        assertEquals(InfoRules.SECTIONS, InfoRules.sections(null))
        assertEquals(InfoRules.SECTIONS, InfoRules.sections(emptyList<String>()))
        assertEquals(listOf("battery", "network"), InfoRules.sections("Battery, network,battery"))
        assertEquals(listOf("storage"), InfoRules.sections(listOf("storage")))
        try { InfoRules.sections(listOf("wifi_password")); fail() } catch (e: IllegalArgumentException) { assertEquals(true, e.message!!.contains("wifi_password")) }
    }

    @Test fun `battery and storage numbers are sane`() {
        assertEquals(50, InfoRules.batteryPercent(50, 100)); assertEquals(75, InfoRules.batteryPercent(3, 4))
        assertNull(InfoRules.batteryPercent(-1, 100)); assertNull(InfoRules.batteryPercent(5, 0))
        assertEquals(100, InfoRules.batteryPercent(500, 100))
        assertEquals(25, InfoRules.usedPercent(100, 75)); assertEquals(0, InfoRules.usedPercent(0, 0)); assertEquals(0, InfoRules.usedPercent(100, 500))
    }

    @Test fun `sizes read like a person would say them`() {
        assertEquals("512 B", InfoRules.human(512)); assertEquals("1.0 KB", InfoRules.human(1024))
        assertEquals("1.5 GB", InfoRules.human(1_610_612_736)); assertEquals("2.0 TB", InfoRules.human(2L shl 40))
    }

    @Test fun `the plugin needs no permission and fails readably before attach`() = runBlocking {
        val p = DeviceInfoPlugin()
        assertEquals(listOf("device.info"), p.capabilities); assertEquals(emptyList<String>(), p.permissions)
        assertEquals("no_context_attached", p.invoke("device.info", emptyMap())["error"])
    }
}

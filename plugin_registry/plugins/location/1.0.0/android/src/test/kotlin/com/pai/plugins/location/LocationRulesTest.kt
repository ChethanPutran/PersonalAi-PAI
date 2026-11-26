package com.pai.plugins.location

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class LocationRulesTest {
    private val now = 1_000_000L
    private fun fix(lat: Double = 12.97, lon: Double = 77.59, acc: Float? = 10f, age: Long = 0, p: String = "gps") = Fix(lat, lon, acc, p, now - age)

    private fun refused(block: () -> Unit): String = try { block(); fail("expected a LocationError"); "" } catch (e: LocationError) { e.code }

    @Test fun `a recent accurate fix beats an old one`() {
        val best = LocationRules.pickBest(listOf(fix(acc = 8f, age = 1000, p = "gps"), fix(acc = 8f, age = 90_000, p = "network")), now)
        assertEquals("gps", best?.provider)
    }

    @Test fun `a much more accurate older fix can win`() {
        val best = LocationRules.pickBest(listOf(fix(acc = 3000f, age = 0, p = "network"), fix(acc = 5f, age = 20_000, p = "gps")), now)
        assertEquals("gps", best?.provider)
    }

    @Test fun `impossible or null-island coordinates are ignored`() {
        assertNull(LocationRules.pickBest(listOf(fix(lat = 0.0, lon = 0.0), fix(lat = 91.0), fix(lon = 181.0)), now))
        assertNull(LocationRules.pickBest(emptyList(), now))
    }

    @Test fun `result carries real age and accuracy`() {
        val r = LocationRules.toResult(fix(age = 4000), now)
        assertEquals(true, r["ok"]); assertEquals(4000L, r["age_ms"]); assertEquals(10f, r["accuracy_m"])
    }

    @Test fun `parameters are validated`() {
        assertEquals(10_000L, LocationRules.timeout(null)); assertEquals(1_000L, LocationRules.timeout(1)); assertEquals(30_000L, LocationRules.timeout(10_000_000))
        assertEquals("bad_parameter", refused { LocationRules.timeout("soon") })
        assertEquals("high", LocationRules.precision(null)); assertEquals("last_known", LocationRules.precision("LAST_KNOWN"))
        assertEquals("bad_parameter", refused { LocationRules.precision("exact") })
    }

    @Test fun `the plugin declares one capability and needs the location permission`() = runBlocking {
        val p = LocationPlugin()
        assertEquals(listOf("location.get"), p.capabilities); assertEquals(listOf("location"), p.permissions)
        assertEquals("no_context_attached", p.invoke("location.get", emptyMap())["error"])
        assertTrue(p.name == "pai.location")
    }
}

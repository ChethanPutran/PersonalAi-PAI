package com.pai.plugins.camera

import com.pai.plugin.api.PluginContext
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class CameraPluginTest {
    private val plugin = CameraPlugin()

    @Test fun `declares exactly what it implements`() {
        assertEquals(listOf("camera.capture", "camera.record"), plugin.capabilities)
        assertEquals("pai.camera", plugin.name)
    }

    @Test fun `before attach every call fails with a readable error`() = runBlocking {
        val r = plugin.invoke("camera.capture", emptyMap())
        assertEquals(false, r["ok"]); assertEquals("no_context_attached", r["error"]); assertTrue(r["message"] is String)
    }

    private fun attached(): CameraPlugin {
        val dir = java.nio.file.Files.createTempDirectory("pai-cam").toFile()
        plugin.attach(object : PluginContext {
            override val appContext get() = throw IllegalStateException("no Android context in a JVM test")
            override val pluginId = "camera_android"
            override val pluginVersion = "1.0.0"
            override val cacheDir: File = dir
            override val filesDir: File = dir
        })
        return plugin
    }

    @Test fun `bad parameters are refused before the camera is touched`() = runBlocking {
        val p = attached()
        assertEquals("bad_parameter", p.invoke("camera.capture", mapOf("lens" to "wide"))["error"])
        assertEquals("bad_parameter", p.invoke("camera.capture", mapOf("format" to "png"))["error"])
        assertEquals("path_not_allowed", p.invoke("camera.capture", mapOf("outputPath" to "/etc/cron.d/x"))["error"])
        assertEquals("path_not_allowed", p.invoke("camera.record", mapOf("outputPath" to "../../x.mp4"))["error"])
        assertEquals("bad_parameter", p.invoke("camera.record", mapOf("durationMs" to "soon"))["error"])
        assertEquals("unknown_capability", p.invoke("camera.stream", emptyMap())["error"])
    }

    @Test fun `failures never claim success`() = runBlocking {
        val p = attached()
        val r = p.invoke("camera.capture", mapOf("lens" to "wide"))
        assertFalse(r["ok"] as Boolean)
    }
}

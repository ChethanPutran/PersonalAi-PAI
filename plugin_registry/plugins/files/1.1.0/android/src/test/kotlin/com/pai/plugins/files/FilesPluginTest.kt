package com.pai.plugins.files

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Test

class FilesPluginTest {
    @Test fun `declares the capabilities of the desktop files plugin and needs no Android permission`() {
        val p = FilesPlugin()
        assertEquals(setOf("files.list", "files.read", "files.write", "files.stat", "files.delete", "files.copy", "files.move"), p.capabilities.toSet())
        assertEquals(emptyList<String>(), p.permissions)
    }

    @Test fun `before attach it fails readably`() = runBlocking {
        val r = FilesPlugin().invoke("files.list", mapOf("path" to "."))
        assertEquals(false, r["ok"]); assertEquals("no_context_attached", r["error"])
    }
}

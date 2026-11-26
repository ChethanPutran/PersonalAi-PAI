package com.pai.plugins.files

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/** The per-device settings rules: allowed roots, denied folders, read-only, and the All files access check. */
class FileJailSettingsTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun code(block: () -> Unit): String =
        try { block(); fail("expected a FilesError"); "" } catch (e: FilesError) { e.code }

    private fun dir(vararg parts: String): File = File(tmp.root, parts.joinToString("/")).apply { mkdirs() }

    @Test fun `several allowed roots with absolute paths`() {
        val pics = dir("sd", "Pictures"); val dl = dir("sd", "Download"); val other = dir("sd", "Other")
        File(pics, "a.txt").writeText("a"); File(dl, "b.txt").writeText("b"); File(other, "c.txt").writeText("c")
        val ops = FileOps(FileJail(listOf(pics, dl)))
        assertEquals("a", ops.read(File(pics, "a.txt").path, 10)["text"])
        assertEquals("b", ops.read(File(dl, "b.txt").path, 10)["text"])
        assertEquals("path_not_allowed", code { ops.read(File(other, "c.txt").path, 10) })
    }

    @Test fun `a denied folder is refused even inside an allowed root`() {
        val sd = dir("sd"); val secret = dir("sd", "private"); File(secret, "k.txt").writeText("k"); File(sd, "ok.txt").writeText("ok")
        val ops = FileOps(FileJail(listOf(sd), listOf(secret)))
        assertEquals("ok", ops.read(File(sd, "ok.txt").path, 10)["text"])
        assertEquals("path_not_allowed", code { ops.read(File(secret, "k.txt").path, 10) })
        assertEquals("path_not_allowed", code { ops.list(secret.path, false, 10) })
        assertEquals("path_not_allowed", code { ops.write(File(secret, "n.txt").path, "x", false, true) })
        assertEquals("path_not_allowed", code { ops.stat(secret.path) })
        assertEquals("path_not_allowed", code { ops.read("private/k.txt", 10) })           // relative to the root
        assertEquals("path_not_allowed", code { ops.read("x/../private/k.txt", 10) })      // dot-dot cannot sneak in
    }

    @Test fun `a symlink into a denied folder is refused`() {
        val sd = dir("sd"); val secret = dir("sd", "private"); File(secret, "k.txt").writeText("k")
        java.nio.file.Files.createSymbolicLink(File(sd, "shortcut").toPath(), secret.toPath())
        val ops = FileOps(FileJail(listOf(sd), listOf(secret)))
        assertEquals("path_not_allowed", code { ops.read("shortcut/k.txt", 10) })
    }

    @Test fun `a denied folder is hidden from listings and whole-folder actions are refused`() {
        val sd = dir("sd"); val secret = dir("sd", "private"); dir("sd", "public"); File(secret, "k.txt").writeText("k")
        val ops = FileOps(FileJail(listOf(sd), listOf(secret)))
        val names = (ops.list(sd.path, true, 100)["entries"] as List<*>).map { (it as Map<*, *>)["name"] }
        assertFalse(names.contains("private")); assertFalse(names.contains("k.txt")); assertTrue(names.contains("public"))
        assertEquals("path_not_allowed", code { ops.delete(sd.path + "/", true) })     // would reach into private/
        assertEquals("path_not_allowed", code { ops.copy(sd.path, File(sd, "public/copy").path, true) })
        assertEquals("k", File(secret, "k.txt").readText())
    }

    @Test fun `prefix siblings are not inside a root or a denied folder`() {
        val pics = dir("sd", "Pictures"); val sibling = dir("sd", "Pictures2"); File(sibling, "x.txt").writeText("x")
        assertEquals("path_not_allowed", code { FileOps(FileJail(listOf(pics))).read(File(sibling, "x.txt").path, 10) })
        val sd = dir("sd"); val den = dir("sd", "priv"); val denSibling = dir("sd", "private2"); File(denSibling, "y.txt").writeText("y")
        assertEquals("y", FileOps(FileJail(listOf(sd), listOf(den))).read(File(denSibling, "y.txt").path, 10)["text"])
    }

    @Test fun `the more specific rule wins between an allowed root and a denied folder`() {
        // Android/data denied, but PAI's own folder inside it stays usable (the default settings).
        val data = dir("sd", "Android", "data"); val mine = dir("sd", "Android", "data", "app", "files"); val theirs = dir("sd", "Android", "data", "other")
        File(mine, "m.txt").writeText("m"); File(theirs, "t.txt").writeText("t")
        val jail = FileJail(listOf(mine), listOf(data))
        val ops = FileOps(jail)
        assertEquals("m", ops.read(File(mine, "m.txt").path, 10)["text"])
        assertEquals("path_not_allowed", code { ops.read(File(theirs, "t.txt").path, 10) })
        // ...and a denied folder inside that root still beats it.
        val inner = dir("sd", "Android", "data", "app", "files", "keep")
        File(inner, "z.txt").writeText("z")
        assertEquals("path_not_allowed", code { FileOps(FileJail(listOf(mine), listOf(data, inner))).read(File(inner, "z.txt").path, 10) })
        // The same folder denied AND allowed: denied.
        assertEquals("path_not_allowed", code { FileOps(FileJail(listOf(mine), listOf(mine))).read(File(mine, "m.txt").path, 10) })
    }

    @Test fun `read-only refuses every change with a plain message but still reads`() {
        val sd = dir("sd"); File(sd, "a.txt").writeText("a")
        val ops = FileOps(FileJail(listOf(sd), readOnly = true))
        assertEquals("a", ops.read("a.txt", 10)["text"])
        assertEquals(true, ops.stat("a.txt")["exists"])
        assertEquals(1, (ops.list(".", false, 10)["entries"] as List<*>).size)
        assertEquals("read_only", code { ops.write("b.txt", "x", false, true) })
        assertEquals("read_only", code { ops.delete("a.txt", false) })
        assertEquals("read_only", code { ops.move("a.txt", "c.txt", false) })
        assertEquals("read_only", code { ops.copy("a.txt", "c.txt", false) })
        assertEquals("a", File(sd, "a.txt").readText()); assertFalse(File(sd, "b.txt").exists())
        val e = try { ops.write("b.txt", "x", false, true); null } catch (e: FilesError) { e }
        assertTrue(e!!.message!!.contains("read-only"))
    }

    @Test fun `no allowed folder refuses everything with a hint`() {
        val e = try { FileOps(FileJail(emptyList())).read("a.txt", 10); null } catch (e: FilesError) { e }
        assertEquals("path_not_allowed", e!!.code); assertTrue(e.message!!.contains("settings"))
    }

    @Test fun `default behaviour is unchanged without settings`() {
        val root = dir("own"); val outside = dir("elsewhere"); File(outside, "s.txt").writeText("s")
        val ops = FileOps(FileJail(listOf(root)))
        ops.write("n.txt", "1", false, true)
        assertEquals("1", ops.read("n.txt", 10)["text"])
        assertEquals("path_not_allowed", code { ops.read(File(outside, "s.txt").path, 10) })
        assertEquals("path_not_allowed", code { ops.delete(root.path, true) })           // a root cannot be deleted
    }

    @Test fun `shared storage needs the all files grant only outside the app folders`() {
        val app = dir("app", "files"); val shared = dir("sd", "DCIM")
        assertFalse(SharedStorage.isShared(File(app, "x/y.txt"), listOf(app)))
        assertTrue(SharedStorage.isShared(File(shared, "p.jpg"), listOf(app)))
        assertTrue(SharedStorage.isShared(File(tmp.root, "app/files2/a"), listOf(app)))     // prefix sibling
        val touched = SharedStorage.touched(mapOf("path" to "rel.txt", "destination" to shared.path + "/d.txt"), app)
        assertEquals(listOf(File(app, "rel.txt"), File(shared, "d.txt")), touched)
        assertTrue(SharedStorage.MESSAGE.contains("Settings > Special app access > All files access > PAI"))
    }

    @Test fun `plugin settings map to a jail and the grant is enforced`() {
        val app = dir("app", "files"); val shared = dir("sd", "Pictures"); File(shared, "p.txt").writeText("p")
        val plugin = FilesPlugin()
        plugin.allFilesAccess = { false }
        // attach needs an Android context; build the same state the way attach does.
        val f = FilesPlugin::class.java.getDeclaredField("appRoots").apply { isAccessible = true }
        f.set(plugin, listOf(app)); plugin.build(null)
        plugin.configure(mapOf("allowedPaths" to listOf(app.path, shared.path), "deniedPaths" to emptyList<String>(), "readOnly" to false))
        val denied = kotlinx.coroutines.runBlocking { plugin.invoke("files.read", mapOf("path" to File(shared, "p.txt").path)) }
        assertEquals("all_files_access_required", denied["error"])
        assertTrue((denied["message"] as String).contains("All files access"))
        plugin.allFilesAccess = { true }
        assertEquals("p", kotlinx.coroutines.runBlocking { plugin.invoke("files.read", mapOf("path" to File(shared, "p.txt").path)) }["text"])
        plugin.configure(mapOf("allowedPaths" to listOf(shared.path), "readOnly" to true))
        assertEquals("read_only", kotlinx.coroutines.runBlocking { plugin.invoke("files.write", mapOf("path" to File(shared, "n.txt").path, "content" to "x")) }["error"])
    }
}

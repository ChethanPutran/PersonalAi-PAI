package com.pai.plugins.files

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class FileOpsTest {
    @get:Rule val tmp = TemporaryFolder()
    private lateinit var root: File
    private lateinit var ops: FileOps

    @Before fun setUp() { root = tmp.newFolder("root"); ops = FileOps(FileJail(listOf(root))) }

    private fun code(block: () -> Unit): String =
        try { block(); fail("expected a FilesError"); "" } catch (e: FilesError) { e.code }

    @Test fun `relative and absolute paths resolve inside the root`() {
        ops.write("a.txt", "hi", false, true)
        assertEquals("hi", ops.read(File(root, "a.txt").absolutePath, 100)["text"])
        assertEquals("hi", ops.read("a.txt", 100)["text"])
    }

    @Test fun `paths that leave the root are refused everywhere`() {
        val outside = tmp.newFile("secret.txt").apply { writeText("x") }
        for (p in listOf("../secret.txt", outside.absolutePath, "/etc/passwd", "a/../../secret.txt", "")) {
            assertTrue(p, code { ops.read(p, 10) } in setOf("path_not_allowed", "bad_parameter"))
            assertTrue(p, code { ops.write(p, "x", false, true) } in setOf("path_not_allowed", "bad_parameter"))
            assertTrue(p, code { ops.delete(p, true) } in setOf("path_not_allowed", "bad_parameter"))
        }
        assertEquals("path_not_allowed", code { ops.copy("a", outside.absolutePath, true) })
        assertEquals("path_not_allowed", code { ops.move(outside.absolutePath, "b", true) })
        assertEquals("x", outside.readText())
    }

    @Test fun `a symlink out of the root cannot be followed`() {
        val outside = tmp.newFolder("outside"); File(outside, "s.txt").writeText("secret")
        java.nio.file.Files.createSymbolicLink(File(root, "link").toPath(), outside.toPath())
        assertEquals("path_not_allowed", code { ops.read("link/s.txt", 100) })
        ops.write("d/x.txt", "1", false, true)
        val listed = ops.list(".", true, 100)["entries"] as List<*>
        assertFalse(listed.any { (it as Map<*, *>)["path"].toString().contains("s.txt") })   // recursion does not enter it
    }

    @Test fun `write is atomic, can append and refuses to create folders unasked`() {
        assertEquals("parent_missing", code { ops.write("new/dir/a.txt", "x", false, false) })
        ops.write("new/dir/a.txt", "one", false, true)
        ops.write("new/dir/a.txt", "two", true, true)
        assertEquals("onetwo", ops.read("new/dir/a.txt", 100)["text"])
        assertEquals(listOf("a.txt"), File(root, "new/dir").list()!!.toList())   // no leftover .part file
    }

    @Test fun `read caps the size, reports truncation and refuses binary`() {
        File(root, "big.txt").writeText("a".repeat(1000))
        val r = ops.read("big.txt", 100)
        assertEquals(100, (r["text"] as String).length); assertEquals(true, r["truncated"])
        File(root, "bin").writeBytes(byteArrayOf(0xff.toByte(), 0xfe.toByte(), 0, 1, 2, 3))
        assertEquals("not_text", code { ops.read("bin", 100) })
        assertEquals("not_found", code { ops.read("missing", 100) })
        assertEquals("not_a_file", code { ops.read(".", 100) })
    }

    @Test fun `list sorts, limits and flags truncation`() {
        for (n in listOf("b", "a", "c")) ops.write("$n.txt", n, false, true)
        val r = ops.list(".", false, 2)
        assertEquals(listOf("a.txt", "b.txt"), (r["entries"] as List<*>).map { (it as Map<*, *>)["name"] })
        assertEquals(true, r["truncated"])
        assertEquals("not_a_directory", code { ops.list("a.txt", false, 10) })
    }

    @Test fun `stat reports missing files without failing`() {
        assertEquals(false, ops.stat("nope")["exists"])
        ops.write("f.txt", "12345", false, true)
        val s = ops.stat("f.txt"); assertEquals(true, s["exists"]); assertEquals(5L, s["size"]); assertEquals("file", s["type"])
    }

    @Test fun `delete protects the root and non-empty folders`() {
        ops.write("d/a.txt", "x", false, true)
        assertEquals("path_not_allowed", code { ops.delete(".", true) })
        assertEquals("path_not_allowed", code { ops.delete(root.absolutePath, true) })
        assertEquals("not_empty", code { ops.delete("d", false) })
        ops.delete("d", true); assertFalse(File(root, "d").exists())
        assertEquals("not_found", code { ops.delete("d", true) })
    }

    @Test fun `copy and move do not overwrite unless asked and keep the content`() {
        ops.write("a.txt", "A", false, true); ops.write("b.txt", "B", false, true)
        assertEquals("exists", code { ops.copy("a.txt", "b.txt", false) })
        ops.copy("a.txt", "b.txt", true); assertEquals("A", File(root, "b.txt").readText())
        ops.move("a.txt", "sub/c.txt", false)
        assertFalse(File(root, "a.txt").exists()); assertEquals("A", File(root, "sub/c.txt").readText())
        ops.write("dir/x.txt", "X", false, true)
        assertEquals("bad_parameter", code { ops.copy("dir", "dir/inner", false) })
        assertEquals("bad_parameter", code { ops.move("dir", "dir/inner", false) })
        assertEquals("path_not_allowed", code { ops.move(".", "z", true) })
    }
}

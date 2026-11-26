package com.pai.plugins.camera

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class CaptureRulesTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun refused(block: () -> Unit): CameraError =
        try { block(); fail("expected a CameraError"); throw IllegalStateException() } catch (e: CameraError) { e }

    @Test fun `a plain file name is saved inside the camera folder`() {
        val base = tmp.newFolder("captures")
        val f = CaptureRules.resolveOutput(base, "shot.jpg", "default.jpg")
        assertEquals(base.canonicalFile, f.parentFile)
        assertEquals("default.jpg", CaptureRules.resolveOutput(base, null, "default.jpg").name)
        assertEquals("default.jpg", CaptureRules.resolveOutput(base, "  ", "default.jpg").name)
        assertEquals("a/b.jpg", CaptureRules.resolveOutput(base, "a/b.jpg", "d.jpg").relativeTo(base.canonicalFile).path)
    }

    @Test fun `paths that escape the camera folder are refused`() {
        val base = tmp.newFolder("captures")
        for (bad in listOf("/etc/passwd", "../x.jpg", "a/../../x.jpg", "..", "\\windows", "x\u0000.jpg", "/tmp/../etc/x")) {
            assertEquals(bad, "path_not_allowed", refused { CaptureRules.resolveOutput(base, bad, "d.jpg") }.code)
        }
    }

    @Test fun `an absolute path is fine when it is already inside the camera folder`() {
        val base = tmp.newFolder("captures")
        val inside = java.io.File(base, "ok.jpg").absolutePath
        assertEquals(java.io.File(base.canonicalFile, "ok.jpg"), CaptureRules.resolveOutput(base, inside, "d.jpg"))
        assertEquals("path_not_allowed", refused { CaptureRules.resolveOutput(base, base.parentFile.absolutePath + "/other.jpg", "d.jpg") }.code)
    }

    @Test fun `a symlink out of the folder is refused`() {
        val base = tmp.newFolder("captures")
        val outside = tmp.newFolder("outside")
        java.nio.file.Files.createSymbolicLink(java.io.File(base, "link").toPath(), outside.toPath())
        assertEquals("path_not_allowed", refused { CaptureRules.resolveOutput(base, "link/x.jpg", "d.jpg") }.code)
    }

    @Test fun `a folder is not a file name`() {
        val base = tmp.newFolder("captures")
        java.io.File(base, "dir").mkdirs()
        assertEquals("path_not_allowed", refused { CaptureRules.resolveOutput(base, "dir", "d.jpg") }.code)
    }

    @Test fun `size choice prefers the largest that fits and falls back to the smallest`() {
        val sizes = listOf(4000 to 3000, 1920 to 1080, 1280 to 720, 640 to 480)
        assertEquals(1920 to 1080, CaptureRules.chooseSize(sizes, 2048))
        assertEquals(4000 to 3000, CaptureRules.chooseSize(sizes, 4096))
        assertEquals(640 to 480, CaptureRules.chooseSize(sizes, 100))        // nothing fits: the smallest
        assertNull(CaptureRules.chooseSize(emptyList(), 2048))
    }

    @Test fun `jpeg orientation follows the sensor and the device rotation`() {
        assertEquals(90, CaptureRules.jpegOrientation(90, 0, frontFacing = false))
        assertEquals(0, CaptureRules.jpegOrientation(90, 90, frontFacing = false))
        assertEquals(270, CaptureRules.jpegOrientation(90, 180, frontFacing = false))
        assertEquals(270, CaptureRules.jpegOrientation(270, 0, frontFacing = true))
        assertEquals(0, CaptureRules.jpegOrientation(270, 90, frontFacing = true))
        assertTrue(CaptureRules.jpegOrientation(90, -90, false) in 0..359)
    }

    @Test fun `recording length is bounded`() {
        assertEquals(5_000L, CaptureRules.clampDuration(null))
        assertEquals(CaptureRules.MIN_RECORD_MS, CaptureRules.clampDuration(1))
        assertEquals(CaptureRules.MAX_RECORD_MS, CaptureRules.clampDuration(10_000_000))
        assertEquals("bad_parameter", refused { CaptureRules.clampDuration("soon") }.code)
    }

    @Test fun `lens must be front or back`() {
        assertEquals(Lens.BACK, Lens.parse(null))
        assertEquals(Lens.FRONT, Lens.parse("FRONT"))
        assertEquals("bad_parameter", refused { Lens.parse("wide") }.code)
    }
}

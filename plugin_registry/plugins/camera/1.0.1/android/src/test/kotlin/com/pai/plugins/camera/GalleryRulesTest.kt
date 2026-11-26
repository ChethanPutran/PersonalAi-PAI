package com.pai.plugins.camera

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class GalleryRulesTest {
    @Test fun `photos and videos go to their own albums`() {
        assertEquals("Pictures/PAI", GalleryRules.target(MediaKind.PHOTO).relativePath)
        assertEquals("image/jpeg", GalleryRules.target(MediaKind.PHOTO).mime)
        assertEquals("Movies/PAI", GalleryRules.target(MediaKind.VIDEO).relativePath)
        assertEquals("video/mp4", GalleryRules.target(MediaKind.VIDEO).mime)
    }

    @Test fun `gallery copies need Android 10 or newer`() {
        assertFalse(GalleryRules.supported(24))
        assertFalse(GalleryRules.supported(28))
        assertTrue(GalleryRules.supported(29))
        assertTrue(GalleryRules.supported(35))
    }

    @Test fun `the gallery name is the file name with the right extension`() {
        assertEquals("PAI_1.jpg", GalleryRules.displayName(File("/data/x/captures/PAI_1.jpg"), MediaKind.PHOTO))
        assertEquals("shot.jpeg", GalleryRules.displayName(File("shot.jpeg"), MediaKind.PHOTO))
        assertEquals("clip.mp4", GalleryRules.displayName(File("a/b/clip.mp4"), MediaKind.VIDEO))
        // wrong or missing extensions are corrected so the gallery recognises the type
        assertEquals("clip.jpg", GalleryRules.displayName(File("clip.mp4"), MediaKind.PHOTO))
        assertEquals("clip.mp4", GalleryRules.displayName(File("clip"), MediaKind.VIDEO))
        assertEquals("archive.tar.mp4", GalleryRules.displayName(File("archive.tar.gz"), MediaKind.VIDEO))
    }

    @Test fun `a sub folder or odd characters never reach the shared album`() {
        assertEquals("b.jpg", GalleryRules.displayName(File("a/b.jpg"), MediaKind.PHOTO))
        val weird = GalleryRules.displayName(File("x\u0000y\u0007z.jpg"), MediaKind.PHOTO)
        assertTrue(weird, weird.none { it < ' ' } && !weird.contains('/') && weird.endsWith(".jpg"))
        assertTrue(GalleryRules.displayName(File(""), MediaKind.VIDEO).endsWith(".mp4"))
    }

    @Test fun `saveToGallery is off unless asked for`() {
        assertFalse(CaptureRules.bool(null, false))
        assertTrue(CaptureRules.bool(true, false))
        assertTrue(CaptureRules.bool("true", false))
        assertFalse(CaptureRules.bool("yes", false))
    }
}

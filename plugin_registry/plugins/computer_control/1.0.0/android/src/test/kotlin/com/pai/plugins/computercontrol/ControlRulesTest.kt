package com.pai.plugins.computercontrol

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class ControlRulesTest {
    private fun code(block: () -> Unit): String = try { block(); fail("expected a ControlError"); "" } catch (e: ControlError) { e.code }

    // ---- tree ------------------------------------------------------------------------------
    private class N(
        override val className: String? = "android.view.View", override val text: String? = null, override val description: String? = null,
        override val viewId: String? = null, override val bounds: IntArray = intArrayOf(0, 0, 100, 100), override val visible: Boolean = true,
        override val enabled: Boolean = true, override val focused: Boolean = false, override val clickable: Boolean = false,
        override val scrollable: Boolean = false, override val editable: Boolean = false, override val password: Boolean = false,
        override val checkable: Boolean = false, override val checked: Boolean = false, val kids: List<N> = emptyList(),
    ) : NodeSource {
        override val childCount get() = kids.size
        override fun child(index: Int): NodeSource? = kids.getOrNull(index)
    }

    @Suppress("UNCHECKED_CAST")
    private fun flat(tree: List<Map<String, Any?>>): List<Map<String, Any?>> = tree.flatMap { listOf(it) + flat(it["children"] as List<Map<String, Any?>>) }

    @Test fun `the tree has the contract's fields and plain wrappers are hoisted away`() {
        val root = N(kids = listOf(N(kids = listOf(N(className = "android.widget.Button", text = "Save", clickable = true, bounds = intArrayOf(10, 20, 110, 70))))))
        val built = TreeBuilder.build(root, 100, 12)
        assertEquals(1, built.tree.size)                                    // two plain wrappers collapsed into the button
        val n = built.tree[0]
        assertEquals("n1", n["id"]); assertEquals("button", n["role"]); assertEquals("Save", n["name"])
        assertEquals(listOf(10, 20, 100, 50), n["bounds"]); assertEquals(true, n["clickable"]); assertNull(n["checked"])
        assertEquals(setOf("n1"), built.nodes.keys)
    }

    @Test fun `invisible subtrees and empty decoration are dropped`() {
        val root = N(text = "root", kids = listOf(N(text = "hidden", visible = false), N(), N(text = "shown")))
        assertEquals(listOf("root", "shown"), flat(TreeBuilder.build(root, 100, 12).tree).map { it["text"] })
    }

    @Test fun `password text is never reported`() {
        val root = N(text = "x", kids = listOf(N(className = "android.widget.EditText", text = "hunter2", editable = true, password = true)))
        val field = flat(TreeBuilder.build(root, 100, 12).tree).first { it["role"] == "input" }
        assertEquals("", field["text"]); assertEquals(true, field["password"])
    }

    @Test fun `limits truncate and say so`() {
        val many = N(text = "r", kids = List(50) { N(text = "item$it", clickable = true) })
        val b = TreeBuilder.build(many, 10, 12)
        assertEquals(10, b.nodes.size); assertTrue(b.truncated)
        val deep = (1..20).fold(N(text = "leaf")) { acc, i -> N(text = "level$i", kids = listOf(acc)) }
        val d = TreeBuilder.build(deep, 1000, 5)
        assertTrue(d.truncated); assertEquals(5, d.nodes.size)
        assertEquals(0, TreeBuilder.build(null, 10, 5).tree.size)
    }

    @Test fun `ids are assigned in reading order and resolve to the nodes`() {
        val a = N(text = "a", clickable = true); val b = N(text = "b", clickable = true)
        val built = TreeBuilder.build(N(text = "root", kids = listOf(a, b)), 100, 12)
        assertEquals(listOf("n1", "n2", "n3"), built.nodes.keys.toList())
        assertTrue(built.nodes["n2"] === a && built.nodes["n3"] === b)
        assertEquals(50f to 50f, TreeBuilder.center(a))
    }

    @Test fun `roles are normalised to the contract's vocabulary`() {
        val cases = mapOf(
            "android.widget.Button" to "button", "android.widget.EditText" to "input", "android.widget.CheckBox" to "checkbox",
            "androidx.appcompat.widget.SwitchCompat" to "switch", "android.widget.RadioButton" to "radio", "android.widget.TextView" to "text",
            "android.widget.ImageView" to "image", "androidx.recyclerview.widget.RecyclerView" to "list", "android.widget.ScrollView" to "scroll",
            "android.view.View" to "other")
        for ((cls, role) in cases) assertEquals(cls, role, Roles.normalize(cls, false, false, false, false))
        assertEquals("button", Roles.normalize("android.widget.TextView", true, false, false, true))   // a clickable label is a button
        assertEquals("input", Roles.normalize("android.view.View", false, true, false, false))
        assertTrue(cases.values.all { it in Roles.ALL })
    }

    // ---- gestures ---------------------------------------------------------------------------
    @Test fun `taps double taps and presses`() {
        assertEquals(1, Gestures.tap(5f, 6f, 1).size)
        val d = Gestures.tap(5f, 6f, 2)
        assertEquals(2, d.size); assertTrue(d[1].startDelayMs > d[0].startDelayMs + d[0].durationMs)   // the second tap starts after the first ends
        assertEquals(Gestures.MAX_LONG_PRESS_MS, Gestures.longPress(1f, 1f, 999_999).first().durationMs)
        assertEquals(Gestures.DEFAULT_SWIPE_MS, Gestures.scroll("down", intArrayOf(0, 0, 100, 200)).durationMs)
        assertEquals(50f to 0f, Gestures.clampPoint(50f, -5f, 100, 100))
        assertEquals(99f to 99f, Gestures.clampPoint(500f, 500f, 100, 100))
    }

    @Test fun `scrolling down swipes up and the finger stays inside the region`() {
        val region = intArrayOf(0, 100, 200, 500)
        val down = Gestures.scroll("down", region).points
        assertTrue(down[0].second > down[1].second)                          // starts low, ends high
        val up = Gestures.scroll("up", region).points
        assertTrue(up[0].second < up[1].second)
        val left = Gestures.scroll("left", region).points; val right = Gestures.scroll("right", region).points
        assertTrue(left[0].first < left[1].first && right[0].first > right[1].first)
        for (d in listOf("up", "down", "left", "right")) for (p in Gestures.scroll(d, region).points) {
            assertTrue(p.first in 0f..200f && p.second in 100f..500f)
        }
        assertEquals("bad_parameter", code { Gestures.scroll("sideways", region) })
    }

    @Test fun `numeric parameters are bounded`() {
        assertEquals(600L, Gestures.duration(null, 600, 5000)); assertEquals(5000L, Gestures.duration(99999, 600, 5000)); assertEquals(50L, Gestures.duration(1, 600, 5000))
        assertEquals("bad_parameter", code { Gestures.duration("long", 600, 5000) })
        assertEquals(3, Gestures.intIn(null, "amount", 3, 1, 10)); assertEquals(10, Gestures.intIn(50, "amount", 3, 1, 10))
    }

    // ---- keys / text / images / apps ----------------------------------------------------------------
    @Test fun `keys are the contract's Android global actions`() {
        assertEquals(GlobalKey.BACK, Keys.parse("BACK")); assertEquals(GlobalKey.ENTER, Keys.parse("Return")); assertEquals(GlobalKey.QUICK_SETTINGS, Keys.parse("quick settings"))
        val e = try { Keys.parse("ctrl+c"); null } catch (e: ControlError) { e }
        assertEquals("unsupported_key", e?.code); assertTrue(e!!.message!!.contains("BACK"))
        assertEquals("bad_parameter", code { Keys.parse(null) })
    }

    @Test fun `typing adds to the field, replaces it on request, and ignores a hint`() {
        assertEquals("hello world", TextEntry.compose("hello ", "world", false, false))
        assertEquals("new", TextEntry.compose("old", "new", true, false))
        assertEquals("typed", TextEntry.compose("Search...", "typed", false, true))
        assertEquals("abc", TextEntry.compose(null, "abc", false, false)); assertEquals("ab", TextEntry.deleteLast("abc")); assertEquals("", TextEntry.deleteLast(null))
        assertEquals("bad_parameter", code { TextEntry.text(null) }); assertEquals("bad_parameter", code { TextEntry.text("x".repeat(6000)) })
    }

    @Test fun `screenshots are scaled down, never up, keeping the shape`() {
        assertEquals(1080 to 2400, ImageRules.targetSize(1080, 2400, 2400)); assertEquals(540 to 1200, ImageRules.targetSize(1080, 2400, 1200))
        assertEquals(800 to 600, ImageRules.targetSize(800, 600, 1000))
    }

    @Test fun `apps are matched by name like a person says it, never guessed`() {
        val apps = listOf(AppInfo("com.android.settings", "Settings"), AppInfo("com.whatsapp", "WhatsApp"), AppInfo("com.android.chrome", "Chrome"))
        assertEquals("com.android.settings", Apps.match("settings", apps)?.packageName); assertEquals("com.whatsapp", Apps.match("whats", apps)?.packageName)
        assertNull(Apps.match("instagram", apps)); assertEquals(listOf("Chrome"), Apps.filter(apps, "chro").map { it.label })
        assertEquals(listOf("Chrome", "Settings", "WhatsApp"), Apps.filter(apps, null).map { it.label })
    }
}

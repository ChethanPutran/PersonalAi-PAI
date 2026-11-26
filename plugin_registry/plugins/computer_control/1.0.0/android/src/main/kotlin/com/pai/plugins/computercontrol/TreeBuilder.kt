package com.pai.plugins.computercontrol

/** The nodes of one observation by id, so a later click/type can name an element. */
internal class Observation(val id: String, val nodes: Map<String, NodeSource>)

internal class BuiltTree(val tree: List<Map<String, Any?>>, val nodes: Map<String, NodeSource>, val truncated: Boolean)

/**
 * Turns the accessibility tree into the contract's `Node` shape.
 *
 * - Invisible nodes and their subtrees are dropped; a plain wrapper (no text, no id, not interactive)
 *   with one child is replaced by that child, which keeps the tree small enough for a model to read.
 * - Ids (`n1`, `n2`, ...) are assigned in reading order and are only valid for this observation.
 * - Text of password fields is never reported.
 */
internal object TreeBuilder {
    const val DEFAULT_MAX_NODES = 150
    const val DEFAULT_MAX_DEPTH = 12
    private const val MAX_TEXT = 300

    fun build(root: NodeSource?, maxNodes: Int, maxDepth: Int): BuiltTree {
        if (root == null) return BuiltTree(emptyList(), emptyMap(), false)
        val state = State(maxNodes.coerceIn(1, 1000), maxDepth.coerceIn(1, 40))
        val top = visit(root, 0, state)
        return BuiltTree(top, state.ids, state.truncated)
    }

    private class State(val maxNodes: Int, val maxDepth: Int) {
        val ids = LinkedHashMap<String, NodeSource>()
        var next = 1
        var truncated = false
    }

    private fun interesting(n: NodeSource) =
        n.clickable || n.scrollable || n.editable || n.checkable || n.focused ||
            !n.text.isNullOrEmpty() || !n.description.isNullOrEmpty() || !n.viewId.isNullOrEmpty()

    private fun visit(n: NodeSource, depth: Int, s: State): List<Map<String, Any?>> {
        if (!n.visible) return emptyList()
        val kids = ArrayList<NodeSource>()
        for (i in 0 until n.childCount) n.child(i)?.let { kids += it }

        if (!interesting(n) && kids.size == 1) return visit(kids[0], depth, s)          // hoist through a plain wrapper
        if (!interesting(n) && kids.isEmpty()) return emptyList()                       // empty decoration

        if (s.ids.size >= s.maxNodes) { s.truncated = true; return emptyList() }
        val id = "n${s.next++}"
        s.ids[id] = n
        val children = if (depth + 1 >= s.maxDepth) {
            if (kids.isNotEmpty()) s.truncated = true
            emptyList()
        } else kids.flatMap { visit(it, depth + 1, s) }
        return listOf(describe(id, n, children))
    }

    private fun describe(id: String, n: NodeSource, children: List<Map<String, Any?>>): Map<String, Any?> {
        val text = if (n.password) "" else (n.text ?: "").take(MAX_TEXT)
        val name = text.ifEmpty { (n.description ?: "").take(MAX_TEXT) }.ifEmpty { n.viewId?.substringAfterLast('/')?.take(80) ?: "" }
        val b = n.bounds
        return mapOf(
            "id" to id,
            "role" to Roles.normalize(n.className, n.clickable, n.editable, n.checkable, text.isNotEmpty()),
            "name" to name, "text" to text,
            "bounds" to listOf(b[0], b[1], (b[2] - b[0]).coerceAtLeast(0), (b[3] - b[1]).coerceAtLeast(0)),
            "enabled" to n.enabled, "focused" to n.focused,
            "checked" to if (n.checkable) n.checked else null,
            "clickable" to n.clickable, "scrollable" to n.scrollable, "editable" to n.editable,
            "password" to n.password,
            "children" to children)
    }

    /** Centre of a node's bounds in screen pixels. */
    fun center(n: NodeSource): Pair<Float, Float> = ((n.bounds[0] + n.bounds[2]) / 2f) to ((n.bounds[1] + n.bounds[3]) / 2f)
}

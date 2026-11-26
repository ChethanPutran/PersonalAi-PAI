package com.pai.plugins.computercontrol

/** A failure the user can read: a stable [code] plus plain words (and optionally how to fix it). */
internal class ControlError(val code: String, message: String, val howToFix: String? = null) : Exception(message)

/** What the tree builder needs from a UI node: an abstraction over AccessibilityNodeInfo so it is testable on the JVM. */
internal interface NodeSource {
    val className: String?
    val text: String?
    val description: String?
    val viewId: String?
    /** left, top, right, bottom in screen pixels. */
    val bounds: IntArray
    val visible: Boolean
    val enabled: Boolean
    val focused: Boolean
    val clickable: Boolean
    val scrollable: Boolean
    val editable: Boolean
    val password: Boolean
    val checkable: Boolean
    val checked: Boolean
    val childCount: Int
    fun child(index: Int): NodeSource?
}

/** Roles of the cross-platform contract. */
internal object Roles {
    val ALL = setOf("button", "link", "text", "input", "checkbox", "switch", "radio", "menu", "menu_item", "tab",
        "list", "list_item", "image", "window", "dialog", "scroll", "other")

    fun normalize(className: String?, clickable: Boolean, editable: Boolean, checkable: Boolean, hasText: Boolean): String {
        val c = (className ?: "").substringAfterLast('.').lowercase()
        return when {
            editable || c.contains("edittext") || c.contains("autocompletetextview") -> "input"
            c.contains("switch") || c.contains("togglebutton") -> "switch"
            c.contains("checkbox") || (checkable && c.contains("check")) -> "checkbox"
            c.contains("radiobutton") -> "radio"
            c.contains("imagebutton") || c.endsWith("button") || c.contains("floatingactionbutton") || c.contains("chip") -> "button"
            c.contains("tab") && !c.contains("table") -> "tab"
            c.contains("menuitem") -> "menu_item"
            c.contains("menu") || c.contains("popup") -> "menu"
            c.contains("dialog") || c.contains("alert") -> "dialog"
            c.contains("recyclerview") || c.contains("listview") || c.contains("gridview") || c.contains("pager") -> "list"
            c.contains("scrollview") -> "scroll"
            c.contains("imageview") || c == "image" -> "image"
            c.contains("webview") -> "window"
            c.contains("textview") && clickable -> "button"
            c.contains("textview") -> "text"
            checkable -> "checkbox"
            clickable -> "button"
            hasText -> "text"
            else -> "other"
        }
    }
}

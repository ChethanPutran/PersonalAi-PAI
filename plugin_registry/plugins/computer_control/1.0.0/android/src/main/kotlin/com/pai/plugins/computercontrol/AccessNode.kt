package com.pai.plugins.computercontrol

import android.graphics.Rect
import android.os.Build
import android.view.accessibility.AccessibilityNodeInfo

/** [NodeSource] over a real AccessibilityNodeInfo (kept as [info] so actions can be performed on it later). */
internal class AccessNode(val info: AccessibilityNodeInfo) : NodeSource {
    override val className: String? get() = info.className?.toString()
    override val text: String? get() = info.text?.toString()
    override val description: String? get() = info.contentDescription?.toString()
    override val viewId: String? get() = info.viewIdResourceName
    override val bounds: IntArray get() = Rect().also { info.getBoundsInScreen(it) }.let { intArrayOf(it.left, it.top, it.right, it.bottom) }
    override val visible: Boolean get() = info.isVisibleToUser
    override val enabled: Boolean get() = info.isEnabled
    override val focused: Boolean get() = info.isFocused || info.isAccessibilityFocused
    override val clickable: Boolean get() = info.isClickable
    override val scrollable: Boolean get() = info.isScrollable
    override val editable: Boolean get() = info.isEditable
    override val password: Boolean get() = info.isPassword
    override val checkable: Boolean get() = info.isCheckable
    override val checked: Boolean get() = info.isChecked
    override val childCount: Int get() = info.childCount
    override fun child(index: Int): NodeSource? = info.getChild(index)?.let(::AccessNode)

    /** The hint ("Search...") Android reports as the text of an empty field is not content. */
    val showingHint: Boolean get() = Build.VERSION.SDK_INT >= 26 && info.isShowingHintText

    /** Re-read the node from the screen; false when the element is gone (the screen changed). */
    fun refresh(): Boolean = info.refresh()
}

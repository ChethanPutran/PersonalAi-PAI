package com.pai

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.os.Handler
import android.os.Looper
import org.json.JSONObject
import okhttp3.*
import okhttp3.Request.Builder
import java.io.IOException
import android.graphics.Path
import android.os.Bundle


class PAIAccesibilityService : AccessibilityService() {
    private lateinit var webSocket: WebSocket
    private val client = OkHttpClient()
    
    override fun onServiceConnected() {
        super.onServiceConnected()
        val request = Builder().url("ws://192.168.1.100:8000/ws/android").build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                val command = JSONObject(text)
                when (command.getString("action")) {
                    "click" -> performClick(command.getInt("x"), command.getInt("y"))
                    "swipe" -> performSwipe(
                        command.getJSONObject("start"), 
                        command.getJSONObject("end"),
                        command.optLong("duration", 100)
                    )
                    "type" -> performType(command.getString("text"))
                    "scroll" -> performScroll(command.getInt("steps"))
                    "back" -> performGlobalAction(GLOBAL_ACTION_BACK)
                    "home" -> performGlobalAction(GLOBAL_ACTION_HOME)
                    "find_and_click" -> findAndClick(command.getString("text"))
                }
            }
        })
    }
    private fun performClick(x: Int, y: Int) {
        val path = Path()
        path.moveTo(x.toFloat(), y.toFloat())
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, 100))
            .build()
        dispatchGesture(gesture, null, null)
    }
    
    private fun performSwipe(start: JSONObject, end: JSONObject, duration: Long) {
        val path = Path()
        path.moveTo(start.getInt("x").toFloat(), start.getInt("y").toFloat())
        path.lineTo(end.getInt("x").toFloat(), end.getInt("y").toFloat())
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, duration))
            .build()
        dispatchGesture(gesture, null, null)
    }
    
    private fun performType(text: String) {
        val arguments = Bundle()
        arguments.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        val root = rootInActiveWindow
        val focused = root?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
        focused?.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)
    }
    
    private fun performScroll(steps: Int) {
        // Perform scroll forward/backward
        val root = rootInActiveWindow
        root?.performAction(if (steps > 0) AccessibilityNodeInfo.ACTION_SCROLL_FORWARD else AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD)
    }
    
    private fun findAndClick(text: String) {
        val root = rootInActiveWindow
        val nodes = root?.findAccessibilityNodeInfosByText(text)
        nodes?.firstOrNull()?.performAction(AccessibilityNodeInfo.ACTION_CLICK)
    }
    
    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}
    override fun onInterrupt() {}
}
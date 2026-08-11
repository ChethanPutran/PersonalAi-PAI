package com.pai

import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import okhttp3.*
import org.json.JSONObject

class PAINotificationListener : NotificationListenerService() {
    private lateinit var webSocket: WebSocket
    private val client = OkHttpClient()
    
    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        super.onNotificationPosted(sbn)
        sbn?.let {
            val packageName = it.packageName
            val title = it.notification.extras.getCharSequence(android.app.Notification.EXTRA_TITLE)
            val text = it.notification.extras.getCharSequence(android.app.Notification.EXTRA_TEXT)
            val json = JSONObject().apply {
                put("type", "notification")
                put("package", packageName)
                put("title", title)
                put("text", text)
            }
            webSocket.send(json.toString())
        }
    }
    
    override fun onCreate() {
        super.onCreate()
        val request = Request.Builder().url("ws://backend_ip:8000/ws/notifications").build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {})
    }
}
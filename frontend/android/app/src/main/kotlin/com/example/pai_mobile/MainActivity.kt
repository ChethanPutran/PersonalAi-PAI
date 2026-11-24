package com.example.pai_mobile

import android.content.Intent
import android.net.Uri
import android.provider.Settings
import com.pai.agent.bridge.PluginChannel
import com.pai.agent.permissions.PermissionManager
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob

class MainActivity : FlutterActivity() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private lateinit var permissionManager: PermissionManager

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        permissionManager = PermissionManager(this)

        val channel = MethodChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            PluginChannel.CHANNEL,
        )
        channel.setMethodCallHandler(
            PluginChannel(this, scope, permissionManager)
        )
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        permissionManager.onRequestPermissionsResult(
            requestCode, permissions, grantResults,
        )
    }
}

package com.pai.agent.permissions

class PermissionManager {
    fun request(permissions: List<String>): Map<String, Any?> {
        // Android runtime permissions are requested via ActivityCompat.requestPermissions.
        // The host layer decides per-plugin which OS permissions map from logical ones.
        return mapOf("ok" to true, "granted" to permissions)
    }
}
package com.pai.agent.plugins

import com.pai.agent.permissions.PermissionManager

class PluginManager(
    private val registry: NativeModuleRegistry,
    private val permissionManager: PermissionManager,
) {

    suspend fun invoke(
        nativeModule: String,
        capability: String,
        params: Map<String, Any?>,
    ): Map<String, Any?> {
        val m = registry.get(nativeModule)
            ?: return mapOf("ok" to false, "error" to "module_not_available")

        if (capability !in m.capabilities) {
            return mapOf("ok" to false, "error" to "capability_not_supported")
        }

        // Permissions might have been revoked since install. Refuse early —
        // calling native code without the OS grant leads to opaque crashes
        // inside AOSP (ACameraDevice returns garbage on some HALs).
        val missing = permissionManager.missing(m.permissions)
        if (missing.isNotEmpty()) {
            return mapOf(
                "ok" to false,
                "error" to "permission_not_granted",
                "missing" to missing,
            )
        }

        return try {
            m.invoke(capability, params)
        } catch (t: Throwable) {
            mapOf("ok" to false, "error" to (t.message ?: "unknown_error"))
        }
    }
}

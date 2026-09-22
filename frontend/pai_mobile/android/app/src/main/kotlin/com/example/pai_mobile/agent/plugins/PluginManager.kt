package com.pai.agent.plugins

class PluginManager(private val registry: NativeModuleRegistry) {

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

        return try {
            m.invoke(capability, params)
        } catch (t: Throwable) {
            mapOf("ok" to false, "error" to (t.message ?: "unknown_error"))
        }
    }

    suspend fun requestPermissions(
        nativeModule: String,
        permissions: List<String>,
    ): Map<String, Any?> {
        val m = registry.get(nativeModule)
            ?: return mapOf("ok" to false, "error" to "module_not_available")
        return try {
            m.requestPermissions(permissions)
        } catch (t: Throwable) {
            mapOf("ok" to false, "error" to (t.message ?: "unknown_error"))
        }
    }
}
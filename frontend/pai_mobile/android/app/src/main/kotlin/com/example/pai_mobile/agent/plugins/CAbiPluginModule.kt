package com.pai.agent.plugins

class CAbiPluginModule(
    private val handle: Long,
) : PluginModule {

    init {
        System.loadLibrary("pai_agent_host")
    }

    override val name: String get() = nativeName(handle)
    override val capabilities: List<String> get() = nativeCapabilities(handle).toList()
    override val permissions: List<String> get() = nativePermissions(handle).toList()

    override suspend fun invoke(
        capability: String,
        params: Map<String, Any?>,
    ): Map<String, Any?> {
        val keys = params.keys.toTypedArray()
        val values = keys.map { params[it]?.toString() ?: "" }.toTypedArray()
        @Suppress("UNCHECKED_CAST")
        return nativeInvoke(handle, capability, keys, values) as Map<String, Any?>
    }

    override suspend fun requestPermissions(
        permissions: List<String>,
    ): Map<String, Any?> {
        // For now the permission flow is delegated to the module itself.
        return mapOf("ok" to true, "granted" to permissions)
    }

    fun destroy() {
        nativeDestroyModule(handle)
    }

    private external fun nativeName(handle: Long): String
    private external fun nativeCapabilities(handle: Long): Array<String>
    private external fun nativePermissions(handle: Long): Array<String>
    private external fun nativeInvoke(
        handle: Long,
        capability: String,
        keys: Array<String>,
        values: Array<String>,
    ): Map<String, Any?>
}
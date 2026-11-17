package com.pai.plugin.api

/**
 * Contract every plugin (native .so or Kotlin .dex) implements.
 *
 * Implementations MUST have a public no-arg constructor. The loader
 * instantiates via reflection, then calls [attach] before [invoke].
 */
interface PluginModule {
    val name: String
    val capabilities: List<String>
    val permissions: List<String>

    /** Injected by the host before any [invoke]. No-op default for backward compat. */
    fun attach(context: PluginContext) {}

    /** Called by the host on uninstall / reload. No-op default. */
    fun destroy() {}

    suspend fun invoke(
        capability: String,
        params: Map<String, Any?>,
    ): Map<String, Any?>

    /**
     * Optional. Most Kotlin plugins rely on the host's [PermissionManager]
     * and never implement this. Default returns everything granted.
     */
    suspend fun requestPermissions(permissions: List<String>): Map<String, Any?> =
        mapOf("ok" to true, "granted" to permissions)
}
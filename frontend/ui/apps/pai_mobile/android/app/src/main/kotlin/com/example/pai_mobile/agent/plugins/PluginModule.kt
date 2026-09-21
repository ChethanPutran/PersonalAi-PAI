package com.pai.agent.plugins

interface PluginModule {
    val name: String
    val capabilities: List<String>
    val permissions: List<String>

    suspend fun invoke(
        capability: String,
        params: Map<String, Any?>,
    ): Map<String, Any?>

    suspend fun requestPermissions(
        permissions: List<String>,
    ): Map<String, Any?>
}
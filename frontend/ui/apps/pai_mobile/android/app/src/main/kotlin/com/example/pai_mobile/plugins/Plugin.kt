package com.pai.app.plugins

interface Plugin {

    val id: String

    val version: String

    val capabilities: List<String>

    suspend fun initialize()

    suspend fun enable()

    suspend fun disable()

    suspend fun execute(
        operation: String,
        parameters: Map<String, Any?>
    ): Map<String, Any?>

    suspend fun dispose()
}
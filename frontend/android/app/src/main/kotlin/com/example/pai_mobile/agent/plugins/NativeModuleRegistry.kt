package com.pai.agent.plugins

import java.util.concurrent.ConcurrentHashMap

class NativeModuleRegistry {
    private val modules = ConcurrentHashMap<String, PluginModule>()

    fun register(module: PluginModule) { modules[module.name] = module }
    fun unregister(name: String) { modules.remove(name) }
    fun get(name: String): PluginModule? = modules[name]
    fun list(): List<String> = modules.keys.toList()
    fun contains(name: String): Boolean = modules.containsKey(name)
}
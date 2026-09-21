package com.pai.agent.bridge

import com.pai.agent.permissions.PermissionManager
import com.pai.agent.plugins.CAbiPluginModule
import com.pai.agent.plugins.DynamicLoader
import com.pai.agent.plugins.NativeModuleRegistry
import com.pai.agent.plugins.PluginManager
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

class PluginChannel(
    private val scope: CoroutineScope,
) : MethodChannel.MethodCallHandler {

    companion object {
        const val CHANNEL = "pai/plugin_runtime"
    }

    private val registry = NativeModuleRegistry()
    private val manager = PluginManager(registry)
    private val permissions = PermissionManager()

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "listModules" -> {
                result.success(registry.list())
            }

            "isModuleAvailable" -> {
                val name = call.argument<String>("nativeModule")!!
                result.success(registry.contains(name))
            }

            "loadNativeModule" -> {
                val path = call.argument<String>("artifactPath")!!
                val name = call.argument<String>("nativeModule")!!
                scope.launch(Dispatchers.IO) {
                    val ok = try {
                        val handle = DynamicLoader.nativeCreateModule(path)
                        if (handle == 0L) {
                            false
                        } else {
                            registry.register(CAbiPluginModule(handle))
                            true
                        }
                    } catch (t: Throwable) {
                        false
                    }
                    Dispatchers.Main.run { result.success(ok) }
                }
            }

            "unloadNativeModule" -> {
                val name = call.argument<String>("nativeModule")!!
                val mod = registry.get(name)
                if (mod is CAbiPluginModule) mod.destroy()
                registry.unregister(name)
                result.success(null)
            }

            "invoke" -> {
                val module = call.argument<String>("nativeModule")!!
                val capability = call.argument<String>("capability")!!
                @Suppress("UNCHECKED_CAST")
                val params = call.argument<Map<String, Any?>>("parameters") ?: emptyMap()

                scope.launch(Dispatchers.IO) {
                    val out = manager.invoke(module, capability, params)
                    Dispatchers.Main.run { result.success(out) }
                }
            }

            "requestPermissions" -> {
                val module = call.argument<String>("nativeModule")!!
                val perms = call.argument<List<String>>("permissions")!!
                scope.launch {
                    val out = manager.requestPermissions(module, perms)
                    Dispatchers.Main.run { result.success(out) }
                }
            }

            else -> result.notImplemented()
        }
    }
}
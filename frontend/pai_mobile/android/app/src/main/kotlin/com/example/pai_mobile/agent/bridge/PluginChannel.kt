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
import android.os.Build

class PluginChannel(
    private val scope: CoroutineScope,
    private val permissionManager: PermissionManager,
) : MethodChannel.MethodCallHandler {

    companion object {
        const val CHANNEL = "pai/plugin_runtime"
    }

    private val registry = NativeModuleRegistry()
    private val manager = PluginManager(registry, permissionManager)

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {

            "listModules" -> result.success(registry.list())

            "isModuleAvailable" -> {
                val name = call.argument<String>("nativeModule")!!
                result.success(registry.contains(name))
            }

            // ---- Install: dlopen → read manifest → request perms → register ----
            "loadNativeModule" -> {
                val path = call.argument<String>("artifactPath")!!
                scope.launch(Dispatchers.IO) {
                    val out = installModule(path)
                    Dispatchers.Main.run { result.success(out) }
                }
            }

            "getAbi" -> {
                val abi = when {
                    Build.SUPPORTED_ABIS.isNotEmpty() -> Build.SUPPORTED_ABIS[0]
                    else -> Build.CPU_ABI ?: "unknown"
                }
                result.success(abi)
            }

            "unloadNativeModule" -> {
                val name = call.argument<String>("nativeModule")!!
                (registry.get(name) as? CAbiPluginModule)?.destroy()
                registry.unregister(name)
                result.success(null)
            }

            "invoke" -> {
                val module = call.argument<String>("nativeModule")!!
                val capability = call.argument<String>("capability")!!
                @Suppress("UNCHECKED_CAST")
                val params = call.argument<Map<String, Any?>>("parameters") ?: emptyMap()
                scope.launch {
                    result.success(manager.invoke(module, capability, params))
                }
            }

            // Called from a "Grant permissions" button when the user
            // denied at install time and wants to retry.
            "requestPermissions" -> {
                val name = call.argument<String>("nativeModule")!!
                val mod = registry.get(name)
                if (mod == null) {
                    result.success(mapOf("ok" to false, "error" to "module_not_loaded"))
                    return
                }
                scope.launch {
                    result.success(permissionManager.request(mod.permissions))
                }
            }

            else -> result.notImplemented()
        }
    }

    /** Install flow: safe to call from IO; suspends on the permission dialog. */
    private suspend fun installModule(path: String): Map<String, Any?> {
        val handle = try {
            DynamicLoader.nativeCreateModule(path)
        } catch (t: Throwable) {
            return mapOf("ok" to false, "error" to (t.message ?: "dlopen_failed"))
        }
        if (handle == 0L) {
            return mapOf("ok" to false, "error" to "dlopen_failed")
        }

        val module = CAbiPluginModule(handle)
        val manifestPerms = module.permissions

        // Ask for ONLY what this plugin needs. Dialog runs on Main.
        val permResult = permissionManager.request(manifestPerms)
        val granted = permResult["ok"] == true

        if (!granted) {
            // Rollback: unload, do not register. The Dart installer
            // shows a message like "Camera plugin needs: Camera, Microphone".
            module.destroy()
            return mapOf(
                "ok" to false,
                "error" to "permissions_denied",
                "name" to module.name,
                "capabilities" to module.capabilities,
                "permissions" to manifestPerms,
                "denied" to (permResult["denied"] ?: emptyList<String>()),
            )
        }

        registry.register(module)
        return mapOf(
            "ok" to true,
            "name" to module.name,
            "capabilities" to module.capabilities,
            "permissions" to manifestPerms,
        )
    }
}

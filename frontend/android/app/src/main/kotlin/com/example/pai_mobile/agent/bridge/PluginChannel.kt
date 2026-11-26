package com.pai.agent.bridge

import com.pai.agent.permissions.PermissionManager
import com.pai.agent.plugins.CAbiPluginModule
import com.pai.agent.plugins.DefaultPluginContext
import com.pai.agent.plugins.DynamicLoader
import com.pai.agent.plugins.KotlinPluginLoader
import com.pai.agent.plugins.NativeModuleRegistry
import com.pai.agent.plugins.PluginManager
import com.pai.plugin.api.ConfigurablePlugin
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import android.os.Build
import android.app.Activity

class PluginChannel(
    private val activity: Activity,
    private val scope: CoroutineScope,
    private val permissionManager: PermissionManager,
) : MethodChannel.MethodCallHandler {

    companion object {
        const val CHANNEL = "pai/plugin_runtime"
    }

    private val registry = NativeModuleRegistry()
    private val manager = PluginManager(registry, permissionManager)
    private val kotlinLoader = KotlinPluginLoader(activity.applicationContext)

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {

            "listModules" -> result.success(registry.list())

            "isModuleAvailable" -> {
                val name = call.argument<String>("nativeModule")!!
                result.success(registry.contains(name))
            }

            // ---- Install: dlopen → read manifest → request perms → register ----
            // ---- Unified load: native OR kotlin ----
            "loadNativeModule" -> {
                val path = call.argument<String>("artifactPath")!!
                // "kotlin" (accept the Dart enum's long form "PluginLanguage.kotlin" too)
                val type = (call.argument<String>("type") ?: "native").substringAfterLast('.').lowercase()
                val entrypoint = call.argument<String>("entrypoint") ?: ""
                @Suppress("UNCHECKED_CAST")
                val config = call.argument<Map<String, Any?>>("config")
                scope.launch(Dispatchers.IO) {
                    val out = when (type) {
                        "kotlin" -> installKotlinModule(path, entrypoint, config)
                        else -> installNativeModule(path)
                    }
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
                val mod = registry.get(name)

                mod?.destroy()
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
            
            // ---- Computer control: the Accessibility Service the user must switch on in Android Settings ----
            "accessibilityStatus" -> result.success(accessibilityStatus())

            "openAccessibilitySettings" -> {
                try {
                    activity.startActivity(
                        android.content.Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS)
                            .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
                    result.success(true)
                } catch (e: Exception) {
                    result.success(false)
                }
            }

            // ---- Per-device plugin settings: the effective values, pushed to a plugin that supports them ----
            "configurePlugin" -> {
                val name = call.argument<String>("nativeModule")!!
                @Suppress("UNCHECKED_CAST")
                val config = call.argument<Map<String, Any?>>("config") ?: emptyMap()
                val mod = registry.get(name)
                val ok = try { (mod as? ConfigurablePlugin)?.configure(config) != null } catch (t: Throwable) { false }
                result.success(mapOf("ok" to ok, "loaded" to (mod != null)))
            }

            // ---- Files plugin: "All files access" (shared folders such as Pictures) ----
            "allFilesAccessStatus" -> result.success(mapOf(
                "granted" to permissionManager.allFilesAccessGranted(), "sdk" to Build.VERSION.SDK_INT))

            "openAllFilesAccessSettings" -> result.success(permissionManager.openAllFilesAccessSettings())

            "debugRegistry" -> {
                result.success(registry.list().associateWith { "<registered>" })
            }

            else -> result.notImplemented()
        }
    }

    /**
     * `enabled`: the user switched PAI's service on in Android Settings. `connected`: the system has started
     * it and plugins can use it. (Enabled but not yet connected happens for a moment after switching it on.)
     */
    private fun accessibilityStatus(): Map<String, Any?> {
        val flat = android.provider.Settings.Secure.getString(
            activity.contentResolver, android.provider.Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES).orEmpty()
        val me = "${activity.packageName}/com.pai.agent.accessibility.PaiAccessibilityService"
        return mapOf(
            "enabled" to flat.split(':').any { it.equals(me, ignoreCase = true) },
            "connected" to (com.pai.plugin.api.PaiHost.accessibility != null),
            "sdk" to Build.VERSION.SDK_INT,
        )
    }

    /** Install flow: safe to call from IO; suspends on the permission dialog. */
    // -------- Native (.so) path — unchanged behavior --------
    private suspend fun installNativeModule(path: String): Map<String, Any?> {
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

    // -------- Kotlin (.dex) path --------
    private suspend fun installKotlinModule(
        dexPath: String,
        className: String,
        config: Map<String, Any?>? = null,
    ): Map<String, Any?> {
        if (className.isBlank()) {
            return mapOf("ok" to false, "error" to "missing_entrypoint_class")
        }

        val loaded = kotlinLoader.load(dexPath, className).getOrElse { t ->
            return mapOf(
                "ok" to false,
                "error" to "kotlin_load_failed: ${t.message ?: t::class.simpleName}",
            )
        }

        // Attach context BEFORE reading perms — the plugin may want
        // to inspect its own storage during the permission phase.
        val ctx = DefaultPluginContext(
            activity.applicationContext,
            pluginId = loaded.name,
            pluginVersion = "0.0.0",
        )
        loaded.attach(ctx)
        // Settings the user chose (install dialog / earlier session) apply from the first call.
        if (config != null) (loaded as? ConfigurablePlugin)?.configure(config)

        val manifestPerms = loaded.permissions
        val permResult = permissionManager.request(manifestPerms)

        if (permResult["ok"] != true) {
            loaded.destroy()
            return mapOf(
                "ok" to false,
                "error" to "permissions_denied",
                "name" to loaded.name,
                "capabilities" to loaded.capabilities,
                "permissions" to manifestPerms,
                "denied" to (permResult["denied"] ?: emptyList<String>()),
            )
        }

        registry.register(loaded)
        return mapOf(
            "ok" to true,
            "name" to loaded.name,
            "capabilities" to loaded.capabilities,
            "permissions" to manifestPerms,
        )
    }
}

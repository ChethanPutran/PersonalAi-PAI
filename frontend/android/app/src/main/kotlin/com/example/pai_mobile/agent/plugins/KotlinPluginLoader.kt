package com.pai.agent.plugins

import android.content.Context
import com.pai.plugin.api.PluginModule
import dalvik.system.DexClassLoader
import java.io.File

/**
 * Loads a Kotlin plugin from a DEX file via [DexClassLoader].
 *
 * Parent classloader = host app's classloader. This means the plugin's
 * reference to `com.pai.plugin.api.PluginModule` resolves to the *host's*
 * copy of the interface (as long as the plugin used `compileOnly`).
 */
class KotlinPluginLoader(private val appContext: Context) {

    fun load(dexPath: String, className: String): Result<PluginModule> = runCatching {
        // 0. Android 14+ throws SecurityException ("Writable dex file ... is not allowed") for a dex the app
        //    can still write to: the downloaded plugin must be read-only before it is loaded.
        File(dexPath).let { if (it.canWrite()) it.setReadOnly() }

        // 1. Create DexClassLoader with the host's classloader as parent
        val optimizedDir = File(appContext.codeCacheDir, "pai_kotlin_plugins")
            .apply { mkdirs() }

        val loader = DexClassLoader(
            dexPath,
            optimizedDir.absolutePath,
            /* nativeLibraryDir = */ null,
            appContext.classLoader,
        )

        // 2. Load the plugin class
        val cls = loader.loadClass(className)

        // 3. Verify it implements PluginModule
        require(PluginModule::class.java.isAssignableFrom(cls)) {
            "$className does not implement com.pai.plugin.api.PluginModule. " +
            "Make sure your plugin used compileOnly(project(\":plugin-api\")) " +
            "so the interface comes from the host classloader."
        }

        // 4. Instantiate (requires a no-arg constructor)
        val ctor = cls.getDeclaredConstructor()
        ctor.isAccessible = true
        ctor.newInstance() as PluginModule
    }
}
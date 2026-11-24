package com.pai.agent.plugins

import android.content.Context
import com.pai.plugin.api.PluginContext
import java.io.File

class DefaultPluginContext(
    override val appContext: Context,
    override val pluginId: String,
    override val pluginVersion: String,
) : PluginContext {
    override val cacheDir: File =
        File(appContext.cacheDir, "plugins/$pluginId").apply { mkdirs() }
    override val filesDir: File =
        File(appContext.filesDir, "plugins/$pluginId").apply { mkdirs() }
}
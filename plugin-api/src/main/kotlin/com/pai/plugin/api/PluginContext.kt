package com.pai.plugin.api

import android.content.Context
import java.io.File

/** Host services exposed to a plugin instance. */
interface PluginContext {
    val appContext: Context
    val pluginId: String
    val pluginVersion: String
    val cacheDir: File
    val filesDir: File
}
package com.pai.plugin.api

/**
 * Optional. A plugin that has per-device settings (its manifest `configSchema`) implements this in
 * addition to [PluginModule]. The host calls [configure] right after `attach` and again whenever the user
 * changes the settings, with the EFFECTIVE values (defaults filled in). Kept as a separate interface, not a
 * new [PluginModule] method, so plugins already compiled against the old interface keep loading.
 */
interface ConfigurablePlugin {
    fun configure(config: Map<String, Any?>)
}

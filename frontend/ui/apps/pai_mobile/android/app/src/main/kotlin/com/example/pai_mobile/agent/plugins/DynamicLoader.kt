package com.pai.agent.plugins

object DynamicLoader {
    init {
        System.loadLibrary("pai_agent_host")
    }

    external fun nativeCreateModule(path: String): Long
    external fun nativeDestroyModule(handle: Long)
}
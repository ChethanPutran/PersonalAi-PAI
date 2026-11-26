pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
    plugins {
        id("com.android.library") version "8.6.0"
        id("org.jetbrains.kotlin.android") version "1.9.24"
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.PREFER_SETTINGS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "device_info-kotlin"

include(":plugin-api")
// plugin-api lives at the repo root: plugin_registry/plugins/<id>/<version>/android -> 5 levels up.
project(":plugin-api").projectDir = file("../../../../../plugin-api")

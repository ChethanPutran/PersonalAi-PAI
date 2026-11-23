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

rootProject.name = "camera-kotlin"

include(":plugin-api")
// Adjust this relative path to wherever plugin-api lives relative to
// plugin_registry/camera/1.0.0/android/
project(":plugin-api").projectDir = file("../../../../plugin-api")
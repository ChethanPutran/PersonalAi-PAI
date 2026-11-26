pluginManagement {
    val flutterSdkPath =
        run {
            val properties = java.util.Properties()
            file("local.properties").inputStream().use { properties.load(it) }
            val flutterSdkPath = properties.getProperty("flutter.sdk")
            require(flutterSdkPath != null) { "flutter.sdk not set in local.properties" }
            flutterSdkPath
        }

    includeBuild("$flutterSdkPath/packages/flutter_tools/gradle")

    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

plugins {
    id("dev.flutter.flutter-plugin-loader") version "1.0.0"
    id("com.android.application") version "8.9.1" apply false   // ← was 8.7.0
    id("org.jetbrains.kotlin.android") version "2.4.0" apply false
}

include(":app")

// The plugin interface (com.pai.plugin.api): the host implements/bundles it, plugins compile against it.
// frontend/android -> repo root is two levels up.
include(":plugin-api")
project(":plugin-api").projectDir = file("../../plugin-api")

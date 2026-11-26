plugins {
    id("com.android.library") 
    id("org.jetbrains.kotlin.android") 
}

android {
    namespace = "com.pai.plugins.camera"
    compileSdk = 35

    defaultConfig {
        minSdk = 24
    }

    testOptions {
        unitTests.isReturnDefaultValues = true          // android.util.Log etc. return defaults in JVM tests
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildTypes {
        release {
            // Plugin DEX must not be shrunk — R8 strips "unused" classes
            // that DexClassLoader looks up by name at runtime. Also
            // saves ~15-25s of build time per run.
            isMinifyEnabled = false
            isShrinkResources = false
        }
    }
}

dependencies {
    // Interface: compileOnly so it is not bundled into the DEX (the host provides it).
    compileOnly(project(":plugin-api"))

    // The host already provides coroutines; same reason as above.
    compileOnly("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")

    // Camera2 is part of the framework: nothing else is bundled into the plugin.

    testImplementation(project(":plugin-api"))
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")
    testImplementation("junit:junit:4.13.2")
}

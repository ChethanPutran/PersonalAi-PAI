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
    // Interface — compileOnly so it's not bundled into the DEX.
    compileOnly(project(":plugin-api"))

    // Host already provides coroutines; same reason as above.
    compileOnly("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")

    // Plugin-owned — safe to bundle.
    implementation("androidx.camera:camera-core:1.3.4")
    implementation("androidx.camera:camera-camera2:1.3.4")
    implementation("androidx.camera:camera-lifecycle:1.3.4")
}
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("kotlin-android")
    // The Flutter Gradle Plugin must be applied after the Android and
    // Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")

    // Google services (Firebase). Requires android/app/google-services.json.
    id("com.google.gms.google-services")
}

android {
    namespace = "com.example.pai_mobile"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
        isCoreLibraryDesugaringEnabled = true 
    }

    defaultConfig {
        applicationId = "com.example.pai_mobile"
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName

        // host JNI lib
        externalNativeBuild {
            cmake {
                // Kotlin DSL: method call with parentheses, comma-separated.
                cppFlags("-std=c++17", "-fexceptions")
            }
        }

        ndk {
            abiFilters += listOf("arm64-v8a", "armeabi-v7a", "x86_64")
        }
    }

    externalNativeBuild {
        cmake {
            // `path` is File? — wrap in file(...) explicitly.
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    // `packagingOptions` was deprecated in AGP 8; the new name is `packaging`.
    packaging {
        jniLibs {
            useLegacyPackaging = true
        }
    }

    sourceSets {
        // `main` on the container isn't callable in Kotlin DSL —
        // resolve it by name.
        getByName("main") {
            // `srcDir` (singular) accepts a path string.
            jniLibs.srcDir("src/main/jniLibs")
        }
    }

    buildTypes {
        release {
            // TODO: Add your own signing config for the release build.
            signingConfig = signingConfigs.getByName("debug")
        }
    }
}

// Migrated from `android.kotlinOptions { jvmTarget = ... }`, which is
// deprecated. Equivalent, but uses the typed enum.
kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

flutter {
    source = "../.."
}

dependencies {
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.4") 
    implementation(platform("com.google.firebase:firebase-bom:35.2.0"))
    implementation("com.google.firebase:firebase-analytics")
}
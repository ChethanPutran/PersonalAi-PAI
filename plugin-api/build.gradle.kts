plugins {
    id("com.android.library") 
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.pai.plugin.api"
    compileSdk = 35
    defaultConfig { minSdk = 24 }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

// `kotlinOptions { jvmTarget = ... }` is an error since Kotlin 2.2 (the Flutter host is on 2.4) but the
// compilerOptions DSL on the task works there and in the plugins' Kotlin 1.9 builds alike.
tasks.withType<org.jetbrains.kotlin.gradle.tasks.KotlinCompile>().configureEach {
    compilerOptions.jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
}

dependencies {
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")
}
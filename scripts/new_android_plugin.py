#!/usr/bin/env python3
"""Create the gradle project around a new Kotlin Android plugin.

    python scripts/new_android_plugin.py <plugin-dir> <id> <version> <package> [--dep group:artifact:version ...]

Example:
    python scripts/new_android_plugin.py phone phone 1.0.0 com.pai.plugins.phone

Writes plugin_registry/plugins/<plugin-dir>/<version>/android/ with the gradle wrapper, settings (plugin-api
is the repo-root module), build.gradle.kts with JVM unit tests, and an empty src/{main,test}/kotlin tree. The
wrapper, gradle.properties and .gitignore are copied from the camera plugin. Write the manifest.json and the
Kotlin sources yourself, then build with scripts/build_plugin.sh.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "plugin_registry/plugins/camera/1.0.0/android"

SETTINGS = '''pluginManagement {
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

rootProject.name = "{name}-kotlin"

include(":plugin-api")
// plugin-api lives at the repo root: plugin_registry/plugins/<id>/<version>/android -> 5 levels up.
project(":plugin-api").projectDir = file("../../../../../plugin-api")
'''

BUILD = '''plugins {
    id("com.android.library")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "{package}"
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
            // The plugin DEX must not be shrunk: R8 strips "unused" classes that DexClassLoader looks up by name.
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
{deps}
    testImplementation(project(":plugin-api"))
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")
    testImplementation("junit:junit:4.13.2")
}
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("plugin_dir")
    ap.add_argument("plugin_id")
    ap.add_argument("version")
    ap.add_argument("package")
    ap.add_argument("--dep", action="append", default=[], help="bundled dependency group:artifact:version")
    a = ap.parse_args()
    dest = ROOT / "plugin_registry/plugins" / a.plugin_dir / a.version / "android"
    if (dest / "build.gradle.kts").exists():
        print(f"error: {dest} already has a gradle project", file=sys.stderr)
        return 1
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("gradlew", "gradlew.bat", "gradle.properties", ".gitignore"):
        shutil.copy2(TEMPLATE / name, dest / name)
    shutil.copytree(TEMPLATE / "gradle", dest / "gradle", dirs_exist_ok=True)
    deps = "".join(f'    implementation("{d}")\n' for d in a.dep)
    (dest / "settings.gradle.kts").write_text(SETTINGS.replace("{name}", a.plugin_id))
    (dest / "build.gradle.kts").write_text(BUILD.replace("{package}", a.package).replace("{deps}", ("\n    // Plugin-owned: bundled.\n" + deps) if deps else ""))
    pkg = a.package.replace(".", "/")
    (dest / "src/main/kotlin" / pkg).mkdir(parents=True, exist_ok=True)
    (dest / "src/test/kotlin" / pkg).mkdir(parents=True, exist_ok=True)
    (dest / "src/main/AndroidManifest.xml").write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android" />\n')
    print(f"created {dest.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

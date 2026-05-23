**Personal AI App - a real Android app using Python**
To track daily routines and act as an assiatance

## Required libraries
* ✅ **Kivy** (for GUI in Python)
* ✅ **Buildozer** (to package your Python code into an APK)

Here’s a complete **step-by-step guide** to create and run your first Android app using Python.

---

## 📱 Step-by-Step: Create Android App Using Python (Kivy + Buildozer)

> 🛠 This works best on **Linux or WSL** (Windows Subsystem for Linux).

---

### 🔹 Step 1: Install Required Tools

Run these commands in Linux or WSL terminal:

```bash
sudo apt update
sudo apt install -y python3-pip build-essential git zip unzip openjdk-17-jdk
pip install --user buildozer
```

Also install Cython (required later):

```bash
pip install Cython
```

### 🔹 Step 3: Initialize Buildozer

In the same directory, run:

```bash
buildozer init
```

This creates a `buildozer.spec` file where all app settings live.

---

### 🔹 Step 4: Configure `buildozer.spec`

Open `buildozer.spec` and make sure these values are set:

```ini
title = MyPythonApp
package.name = mypythonapp
package.domain = org.example
source.include_exts = py,png,jpg,kv,atlas
```

Optionally add permissions like:

```ini
android.permissions = INTERNET
```

---

### 🔹 Step 5: Build APK

Now build the app (this takes time on first run):

```bash
buildozer -v android debug
```

> This downloads Android SDK, NDK, and builds the APK.

---

### 🔹 Step 6: Install on Your Phone

Connect your Android device with USB debugging enabled, then run:

```bash
buildozer android deploy run
```

Or install manually:

```bash
adb install bin/MyPythonApp-0.1-debug.apk
```

---

## 🚀 Done! You've built an Android app using Python.

### ✅ What You Can Do Next:

* Use **KivyMD** for Material Design UI:

  ```bash
  pip install kivymd
  ```
* Package a **release APK** for the Play Store.
* Add features: buttons, forms, camera, GPS, etc.

---

Would you like a downloadable starter template zip file or a sample project with buttons and screens?

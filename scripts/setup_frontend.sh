## Step 1: Install React Native CLI

# Install React Native CLI globally
npm install -g react-native-cli

# Or use npx (recommended)
npx react-native --version


## Step 2: Create Mobile Client Project
# Navigate to your openclaw project directory
cd openclaw-langgraph

# Create React Native project
npx react-native init OpenClawMobile --version 0.72.0

# Navigate into the project
cd OpenClawMobile


## Step 3: Install Required Dependencies
# Core dependencies
npm install @react-native-voice/voice@3.2.4
npm install react-native-audio-record@1.0.2
npm install react-native-fs@2.20.0
npm install react-native-document-picker@9.0.1
npm install react-native-push-notification@8.1.1
npm install @react-native-community/push-notification-ios@1.11.0
npm install moment@2.29.4
npm install react-native-safe-area-context@4.7.0

# For iOS only
cd ios && pod install && cd ..


## Step 4: Configure Android

# 4.1 Update Android Permissions
# Edit android/app/src/main/AndroidManifest.xml
```
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    
    <!-- Add these permissions -->
    <uses-permission android:name="android.permission.RECORD_AUDIO" />
    <uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE" />
    <uses-permission android:name="android.permission.WRITE_EXTERNAL_STORAGE" />
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
    <uses-permission android:name="android.permission.INTERNET" />
    
    <application>
        <!-- Add this for push notifications -->
        <meta-data 
            android:name="com.google.firebase.messaging.default_notification_channel_id" 
            android:value="openclaw" />
        
        <!-- Allow cleartext traffic for development (HTTP) -->
        android:usesCleartextTraffic="true"
    </application>
</manifest>
```

# 4.2 Update Android Build Configuration
# Edit android/app/build.gradle:
```
android {
    compileSdkVersion 33
    buildToolsVersion "33.0.0"
    
    defaultConfig {
        applicationId "com.openclawmobile"
        minSdkVersion 24
        targetSdkVersion 33
        versionCode 1
        versionName "1.0"
    }
    
    // Add this for vector drawables support
    defaultConfig {
        vectorDrawables.useSupportLibrary = true
    }
}

dependencies {
    // Add these dependencies
    implementation 'com.google.firebase:firebase-messaging:23.0.0'
    implementation 'androidx.swiperefreshlayout:swiperefreshlayout:1.1.0'
}

// Add at the bottom
apply plugin: 'com.google.gms.google-services'
```


# 4.3 Create Google Services File (for Push Notifications)
# If using Firebase push notifications:
# 1. Go to Firebase Console
# 2. Create new project
# 3. Add Android app
# 4. Download google-services.json
# 5. Place it in android/app/


# Step 9: Run the Mobile App
# For Android:
# Make sure you have Android device connected or emulator running

# List connected devices
adb devices

# Run the app
npx react-native run-android

# Or if you want to run on specific device
npx react-native run-android --deviceId=YOUR_DEVICE_ID



# Common Commands Reference
# Start Metro bundler
npx react-native start

# Run Android
npx react-native run-android

# Run iOS
npx react-native run-ios

# Clear all caches
cd android && ./gradlew clean && cd ..
npx react-native start --reset-cache

# Check connected devices
adb devices

# Debug app
npx react-devtools

# Log Android
npx react-native log-android

# Log iOS
npx react-native log-ios

# Create React Native project
npx react-native init OpenClawMobile
cd OpenClawMobile

# Install dependencies
npm install @react-native-voice/voice react-native-websocket

# Copy App.js content
# Update BACKEND_URL to your server IP

# Run on device
npx react-native run-android  # or run-ios
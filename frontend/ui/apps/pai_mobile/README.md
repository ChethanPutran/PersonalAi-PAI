# PAI Mobile App

Flutter client for the Personal AI System. The app connects to the FastAPI backend, shows backend health, lists plugins, and lets you enable or disable them from the phone.

## Run

1. Make sure the backend is running on the host configured in [lib/.env](lib/.env).
2. From this folder, run `flutter pub get`.
3. Start the app with `flutter run` or `flutter run -d <device-id>`.
4. If you are using an Android emulator, change `API_BASE_URL` and `WS_URL` in [lib/.env](lib/.env) to `10.0.2.2` instead of your machine IP.

## Backend Endpoints Used

- `GET /health`
- `GET /api/v1/plugins`
- `POST /api/v1/plugins/{plugin_id}/enable`
- `POST /api/v1/plugins/{plugin_id}/disable`
- WebSocket goal sending through `WS_URL`


PART 4 — Verification checklist
Linux:

cmake --build build/linux/x64/debug — should link cleanly.

Run the app; call listModules from Dart → [] initially.

Drop libpai_camera.so somewhere on disk, call loadNativeModule, then listModules → ["pai.camera"].

invoke(nativeModule: "pai.camera", capability: "camera.capture") → returns the map from the plugin.

unloadNativeModule → listModules → [].

Android:

./gradlew :app:assembleDebug — the host lib libpai_agent_host.so is packed into the APK.

Push a compiled libpai_camera.so for the correct ABI into the app's files dir, e.g.:

text
adb push libpai_camera.so /data/local/tmp/
adb shell run-as com.pai.agent cp /data/local/tmp/libpai_camera.so files/plugins/camera/libpai_camera.so
From Dart: loadNativeModule(artifactPath: <abs path>, nativeModule: "pai.camera") → true.

invoke("pai.camera", "camera.capture") → returns the plugin's map.

If both of those pass, the dynamic plugin system is working end-to-end and you can now write the Dart-side installer to fetch and drop the .so files automatically.


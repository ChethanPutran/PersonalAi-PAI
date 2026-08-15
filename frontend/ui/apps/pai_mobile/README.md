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

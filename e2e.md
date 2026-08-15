I'll give a compact end-to-end test plan with exact commands and quick API calls so you can run the backend on your laptop, install the apps, register devices, toggle plugins, push watch health data, exercise audio/earbuds, create tasks/alarms, and ask the agent to plan/notify.

Prereqs (on laptop)
- Python venv activated (you already have .venv). If not:
  - python -m venv .venv
  - source .venv/bin/activate
- Ensure Flutter installed on laptop and mobile device (or use Android build tools + `adb`).
- Laptop & mobile on same Wi‑Fi (or use port forwarding / ngrok).
- Ensure port 8000 is reachable from mobile (allow firewall).

1) Start backend on laptop
- From repo root:
```bash
source .venv/bin/activate
# start UVicorn (from project root where pai.main is importable)
python -m uvicorn pai.main:app --reload --host 0.0.0.0 --port 8000
```
- Find laptop local IP:
```bash
hostname -I | awk '{print $1}'
# or
ip addr show | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n1
```
Use that IP as <LAPTOP_IP> below.

2) Configure Flutter app to point to backend
- In pai_mobile, create `.env` (if not present) with:
```
API_BASE_URL=http://<LAPTOP_IP>:8000
WS_URL=ws://<LAPTOP_IP>:8000/ws
```
- Replace `<LAPTOP_IP>` with your laptop address.

3) Build & install mobile app (Android)
- Connect phone via USB (or use emulator).
- From pai_mobile:
```bash
# run directly on device (dev)
flutter run -d <device-id>
# or build release APK and install
flutter build apk --release
adb install -r build/app/outputs/flutter-apk/app-release.apk
```
4) Run desktop app on laptop (Linux)
- From pai_mobile:
```bash
flutter run -d linux
# or build:
flutter build linux
# run the binary from build output
```

5) Verify connectivity & device registration
- On mobile app: open Device Selection screen → choose input/output → Save.
  - The app will:
    - send `context_update` over WebSocket, and
    - call `POST /api/v1/devices/register` to persist device.
- Verify backend received it:
```bash
curl http://<LAPTOP_IP>:8000/api/v1/devices/list/default | jq
# (mobile uses placeholder 'default' user id unless you supply auth)
```
- If you want to manually register a device:
```bash
curl -X POST http://<LAPTOP_IP>:8000/api/v1/devices/register \
  -H "Content-Type: application/json" \
  -d '{"user_id":"me","device_id":"phone-1","device_name":"My Phone","device_type":"mobile","platform":"android","capabilities":{"input":"mic","output":"speaker"}}' | jq
```

6) List & toggle plugins (mobile/desktop)
- UI: Home → plugin list → toggle a plugin. This calls the backend and persists user plugin state.
- API (optionally test from laptop):
  - List plugins:
  ```bash
  curl http://<LAPTOP_IP>:8000/api/v1/plugins | jq
  ```
  - Enable/disable plugin (server-side, user depends on kernel context; for quick test send via curl to v1 plugin endpoints):
  ```bash
  curl -X POST http://<LAPTOP_IP>:8000/api/v1/plugins/<plugin_id>/enable
  curl -X POST http://<LAPTOP_IP>:8000/api/v1/plugins/<plugin_id>/disable
  ```
  - Confirm UI updates after toggling.

7) Connect a watch and push health data (simple flow)
- Simulate a watch by registering it as a device:
```bash
curl -X POST http://<LAPTOP_IP>:8000/api/v1/devices/register \
  -H "Content-Type: application/json" \
  -d '{"user_id":"me","device_id":"watch-1","device_name":"My Watch","device_type":"watch","platform":"wearos","capabilities":{"sensors":["heart_rate","steps"]}}' | jq
```
- Send sensor data to `health` plugin via plugin execute route:
```bash
curl -s -X POST "http://<LAPTOP_IP>:8000/api/plugins/health/execute" \
  -H "Content-Type: application/json" \
  -d '{"action":"health.track_activity","params":{"sensor_data":{"steps":4200,"heart_rate":72}}}' | jq
```
- The `health` plugin will return processed summary (steps, calories, etc.). You can build a tiny mobile UI to show this or view result in curl output.

8) Use earphones / mic on mobile or desktop
- On mobile: choose earphones as output device in Device Selection UI (or system-level selection). App uses device context and the kernel will route audio if the `speech`/`voice` plugins are enabled.
- Test voice to agent:
  - Speak in app (microphone icon) or call `ws` goal with audio context (if implemented).
  - Example WebSocket message to send a text/audio goal (use websocket client e.g., websocat or browser devtools):
```json
# send to ws://<LAPTOP_IP>:8000/ws
{"type":"goal","goal":"What is my step count today?","context":{"user_id":"me"}}
```
- You’ll receive a `result` message with the agent response and activity events will appear in Activity UI.

9) Tasks & alarms: create tasks, notify, ask agent to plan
- Create a task (via API):
```bash
curl -X POST http://<LAPTOP_IP>:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{"title":"Buy groceries","description":"Milk, eggs, bread","priority":2}' | jq
```
- List tasks:
```bash
curl http://<LAPTOP_IP>:8000/api/v1/tasks | jq
```
- Ask agent to plan a sequence for tasks via WebSocket goal:
```json
{"type":"goal","goal":"I have tasks: Buy groceries, Call bank, Finish report. Plan an efficient sequence and assign devices to each step.","context":{"user_id":"me"}}
```
- The kernel/Planner/Agent should return a plan; Activity view will show which agent/tool assigned.
- For alarms/notifications:
  - Use the notification plugin (if enabled) to schedule or send immediate notifications. Example plugin call (simplified):
```bash
curl -s -X POST "http://<LAPTOP_IP>:8000/api/plugins/notification/execute" \
  -H "Content-Type: application/json" \
  -d '{"action":"notification.send","params":{"title":"Reminder","body":"Stand up meeting in 10 minutes","target":"device-phone-1"}}' | jq
```
- If mobile has push integration, the app can receive notifications (verify push tokens saved in backend).

10) Subscriptions & realtime events
- Use the app's Activity view (it subscribes to `task.*`, `agent.assigned`, `file.access_request` events).
- If you want an external test, subscribe with a WebSocket client:
```json
# after WS connect
{"type":"subscribe","event":"task.started"}
{"type":"subscribe","event":"agent.assigned"}
```

11) Debugging tips
- If mobile cannot reach backend:
  - Check laptop firewall: allow inbound to port 8000.
  - Confirm API reachable from phone: on phone browser open http://<LAPTOP_IP>:8000/health
- If plugin toggles don't persist for a real user: backend uses kernel context to get `user_id`. Without auth the mobile uses a placeholder `default`; for multi-user tests add auth (next step).
- Simulate watch sensor stream: create a small script that POSTs sensor datapoints to `/api/plugins/health/execute` repeatedly.
- To inspect device records directly in DB: open the sqlite file created by plugin manager (user_plugin_usage.db).

What I can do next (pick one)
- Add mobile UI to list registered devices and let you pick from previously-registered devices (so you can pick laptop/phone/watch easily).
- Add simple auth (JWT) so devices/apps persist real `user_id` and the plugin enable/disable actions are tied to that user rather than the placeholder.
- Add a tiny test harness (a shell script + sample curl sequences) to run the entire demo automatically.

Which next step would you like me to implement now: device-list UI, auth integration, or automated E2E test script?
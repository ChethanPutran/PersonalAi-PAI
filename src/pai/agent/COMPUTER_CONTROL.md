# Computer control: the capability contract

One plugin id, `computer_control`, implemented natively on each device platform (Linux: C++, Android: Kotlin)
and driven by the PAI agent (`src/pai/agent`) running on the backend. Every platform exposes the SAME
capabilities with the SAME parameters and result shapes; only the implementation differs. The perception,
planning and verification loop is NOT in the device plugins: they are small, fast, stateless actions. The
backend agent calls them as tools (observe -> act -> observe -> verify).

Spec: `.idea/components/computer_control.md`.

## Reuse, don't reinvent

Linux already ships `input` (keyboard and mouse through the desktop's remote-desktop portal, with the
compositor's own approval), `screen` (screenshots) and `desktop` (windows, app launcher, clipboard...); Android
already has a Kotlin plugin system and permission manager. `computer_control` builds on that code (same
portal session, same capture path, same permission prompts), it does not duplicate it. Add only what is
missing: the accessibility UI tree, element addressing, and the common contract below.

## Permissions and safety (same model as the existing plugins)

Reuse the existing permission resources (adding a new one needs the enum, the JSON schema, the registries and
tests to change together, and nothing here needs it):

| capability | permissions | risk | requireConfirmation |
|---|---|---|---|
| `computer.status` | none | low | false |
| `computer.observe` | `screen` | medium | true (reads what is on screen) |
| `computer.apps.list`, `computer.apps.foreground` | `system` (as `desktop` declares for apps) | low | false |
| `computer.apps.launch` | `system` | medium | true |
| `computer.click`, `computer.long_press`, `computer.type`, `computer.key`, `computer.scroll`, `computer.swipe` | `input` | high | true |

A capability that declares `input` always asks (the existing rule); the device-side OS prompt (portal dialog on
Linux, enabling the Accessibility Service on Android) is a second, independent gate. `computer.status` never
needs a prompt and says what is missing (`ready: false, missing: ["accessibility_service"]`) with a
human-readable `how_to_fix`.

## Capabilities

All results are JSON objects; failures use the plugin error channel (a message the user can read), never a
silent no-op. Coordinates are screen pixels of the CURRENT observation (`screen.width/height`).

* `computer.status` -> `{ready: bool, platform, missing: [string], how_to_fix: string|null, features: {tree, screenshot, ocr, input}}`
* `computer.observe` `{include_tree=true, include_screenshot=false, max_nodes=150, max_depth=12}` ->
  `{app: {id, name, title}, screen: {width, height}, tree: [Node]|null, screenshot: {mime: "image/png", base64, width, height}|null, truncated: bool, observation_id: string}`
  * `Node = {id, role, name, text, bounds: [x, y, w, h], enabled, focused, checked: bool|null, clickable, scrollable, editable, children: [Node]}`
    Roles are normalized: `button, link, text, input, checkbox, switch, radio, menu, menu_item, tab, list, list_item, image, window, dialog, scroll, other`.
  * `id` is stable only within one `observation_id`.
* `computer.click` `{element_id | x, y; button="left"|"right"|"middle"; count=1|2}` -> `{ok, target: {id?, x, y}}`
* `computer.long_press` `{element_id | x, y; duration_ms=600}`
* `computer.type` `{text; element_id?; clear_first=false}` (focuses the element first when given)
* `computer.key` `{keys}` e.g. `"ctrl+l"`, `"Return"`, `"alt+Tab"` (Linux, xdotool-style names); on Android the
  global actions `BACK, HOME, RECENTS, NOTIFICATIONS, QUICK_SETTINGS, ENTER, DELETE`
* `computer.scroll` `{direction: up|down|left|right; amount=3 (pages-ish clicks); element_id | x, y}`
* `computer.swipe` `{from: {x, y}, to: {x, y}, duration_ms=300}` (drag on desktop)
* `computer.apps.list` `{query?}` -> `{apps: [{id, name}]}`; `computer.apps.launch` `{id | name}` ->
  `{ok, launched: {id, name}}`; `computer.apps.foreground` -> `{id, name, title}`

Interaction results SHOULD include enough to verify cheaply (`ok`, what was hit). The agent verifies by
observing again; plugins never claim success they did not check.

## Server side (Python, backend)

* The `agent` built-in plugin (enabled per user in Settings / Plugins) provides the agent runtime
  (`agent.start`, ...); a goal like "open settings and enable Bluetooth" starts an agent whose tools are the
  user's `computer.*` capabilities on a chosen device.
* The agent prompt `prompts/agent/react` gets a computer-control section: observe first, prefer
  `element_id` over coordinates, re-observe after every action, verify the outcome, stop and ask the user before
  anything destructive or sensitive.
* Vision/OCR fallback runs on the backend from `computer.observe` screenshots when the tree has no usable
  nodes.

# Writing a PAI plugin manifest

A plugin is described by a single `manifest.json` file. The manifest is
declarative: it tells the PAI runtime **what** the plugin is, **where**
it runs, **how** to load it, and **what** capabilities it exposes.

The manifest never contains executable code. It is validated on the
server during install (`manifest_install.py`) and again by the loader
at startup (`PluginRegistry`). A manifest that fails validation is
rejected before any binary is downloaded, extracted, or executed.

---

## Table of contents

1. [Quick start](#1-quick-start)
2. [Disk layout](#2-disk-layout)
3. [The two axes: `type` vs `runtime.kind`](#3-the-two-axes-type-vs-runtimekind)
4. [Complete field reference](#4-complete-field-reference)
5. [Capabilities](#5-capabilities)
6. [Parameters (JSON Schema)](#6-parameters-json-schema)
7. [Confirmation, preconditions, risk](#7-confirmation-preconditions-risk)
8. [Recipes](#8-recipes)
9. [Validation & linting](#9-validation--linting)
10. [Publishing checklist](#10-publishing-checklist)

---

## 1. Quick start

A minimal **server-side Python plugin**:

```json
{
  "schemaVersion": 1,
  "id": "hello",
  "name": "Hello",
  "version": "1.0.0",
  "description": "Say hello.",
  "author": "PAI",
  "license": "MIT",

  "type": "python",
  "runtime": {
    "kind": "server",
    "nativeModule": "pai.hello.server",
    "minPaiVersion": "0.4.0"
  },

  "platforms": {
    "linux": { "artifact": "server/hello_plugin.py" }
  },

  "architectures": ["x86_64"],
  "deviceTypes": ["server"],

  "capabilities": [
    {
      "id": "hello.greet",
      "description": "Return a greeting for a name.",
      "parameters": {
        "type": "object",
        "properties": {
          "name": { "type": "string" }
        },
        "required": ["name"]
      }
    }
  ]
}
```

Place it at `plugin_registry/plugins/hello/1.0.0/manifest.json` and run:

```bash
./plugin_registry/tests/install_plugin_manifest.sh plugin_registry/plugins
```

If it prints `OK`, the manifest is valid.

---

## 2. Disk layout

There are two supported layouts, chosen by how many platforms the
plugin targets.


```
plugin_registry/plugins/
└── camera/
    └── 1.0.0/
        ├── linux/
        │   ├── manifest.json        ← native .so
        │   └── libpai_camera.so
        └── android/
            ├── manifest.json        ← kotlin .dex
            └── plugin.dex
```


---

## 3. The two axes: `type` vs `runtime.kind`

These two fields are orthogonal. Confusing them is the #1 source of
invalid manifests.

| Field | Question it answers | Allowed values |
|---|---|---|
| `runtime.kind` | **Where does it execute?** | `"server"` \| `"device"` |
| `type` | **What is it written in?** | `"native"` \| `"kotlin"` \| `"swift"` \| `"python"` \| `"javascript"` |

Valid combinations:

| `runtime.kind` | `type` | Example |
|---|---|---|
| `server` | `python` | `browser` |
| `server` | `native` | heavy C++ worker |
| `device` | `native` | `camera` (Linux), `files`, `shell` |
| `device` | `kotlin` | `camera` (Android) |
| `device` | `swift` | future iOS plugins |
| `device` | `javascript` | webview plugins |

The pairing is enforced at validation time:

- `runtime.kind = "server"` → `deviceTypes` **must** contain `"server"`.
- `runtime.kind = "device"` → `deviceTypes` **must not** contain `"server"`.

---

## 4. Complete field reference

### Identity

| Key | Type | Required | Notes |
|---|---|---|---|
| `schemaVersion` | int | ✅ | Manifest schema version. Currently `1` or `2`. |
| `id` | string | ✅ | Lowercase, matches `^[a-z][a-z0-9._-]*$`. Unique across the registry. Becomes the `plugin_id` in the DB and the namespace prefix of every capability (`files.read`). |
| `name` | string | ✅ | Human-readable title shown in clients. |
| `version` | string | ✅ | Semver. Higher version wins on conflict. |
| `description` | string | ⬜ | Shown in listings and injected into the LLM system prompt. |
| `author` | string | ✅ | Attribution / audit. |
| `license` | string | ✅ | SPDX identifier, e.g. `"MIT"`. |

### Runtime

| Key | Type | Required | Notes |
|---|---|---|---|
| `type` | enum | ✅ | Implementation language. See §3. |
| `runtime.kind` | enum | ✅ | `"server"` or `"device"`. |
| `runtime.nativeModule` | string | ✅ | Module identifier the loader uses. Convention: `pai.<id>.<role>` (e.g. `pai.camera`, `pai.browser.server`). |
| `runtime.minPaiVersion` | string | ⬜ | Refuse to install on older PAI cores. |

### Targeting

| Key | Type | Required | Notes |
|---|---|---|---|
| `platforms` | object | ✅ | Map of platform name → `{ artifact, entrypoint }`. Platform names: `linux`, `android`, `macos`, `ios`, `windows`. |
| `architectures` | string[] | ⬜ | `x86_64`, `arm64`, `arm64-v8a`, … Must be a subset of the platform's real CPU support. |
| `deviceTypes` | string[] | ✅ | `server`, `desktop`, `mobile`, `tablet`, `embedded`, … |

`artifact` and `entrypoint` describe where the binary lives (relative to
the manifest) and the symbol the loader calls. They are **not**
evaluated during manifest validation — they describe intent; the loader
verifies them at plugin load.

### Policy

| Key | Type | Required | Notes |
|---|---|---|---|
| `permissions` | string[] | ⬜ | Global permissions the plugin needs, e.g. `filesystem`, `camera`, `microphone`. Surfaced in the user consent dialog. |
| `dependencies` | string[] | ⬜ | pip-style requirement specifiers (`playwright>=1.48,<2.0`). Server plugins only; the installer `pip install`s them. |
| `postInstall` | string[][] | ⬜ | Commands to run after install. Each entry is an argv list. Use for `playwright install chromium`. |
| `metadata` | object | ⬜ | Free-form, plugin-specific guardrails. Referenced from capability preconditions (see §7). |
| `configSchema` | object | ⬜ | JSON Schema for user-supplied configuration (stored per-user, not in the manifest). |

### Capabilities

| Key | Type | Required | Notes |
|---|---|---|---|
| `capabilities` | array | ✅ | At least one entry. Full spec in §5. |

---

## 5. Capabilities

A capability is the unit the LLM invokes. Each capability has an
`id` of the form `<plugin-id>.<verb>`, e.g. `files.read`,
`camera.capture`, `browser.navigate`.

```json
{
  "id": "files.write",
  "description": "Write content to a file. Creates parent directories.",
  "requireConfirmation": true,
  "confirmationReason": "This will overwrite the target file.",
  "confirmationPreview": { "fields": ["path", "content"], "maxChars": 500 },
  "risk": "high",
  "permissions": ["filesystem"],
  "precondition": {
    "type": "path_in_roots",
    "field": "path",
    "allowedRootsRef": "metadata.allowedRoots"
  },
  "parameters": {
    "type": "object",
    "properties": {
      "path":    { "type": "string" },
      "content": { "type": "string" },
      "append":  { "type": "boolean", "default": false }
    },
    "required": ["path", "content"]
  }
}
```

| Key | Type | Required | Notes |
|---|---|---|---|
| `id` | string | ✅ | Unique **within the plugin**. Duplicate ids are rejected. |
| `description` | string | ✅ | Fed to the LLM. Be specific: the model picks a capability based on this string alone. |
| `requireConfirmation` | bool | ⬜ (default `false`) | If true, the UI prompts the user before the plugin runs. |
| `retryable` | bool | ⬜ (default `true`) | Set `false` for actions that must not run twice (send SMS, pay, delete). The backend then never auto-retries a failure and never auto-re-sends the task after a crash/restart; the user can still retry manually. See §7. |
| `taskType` | `one_shot` \| `long_running` \| `stream` | ⬜ (default `one_shot`) | How the task runs. **Declared here, never by the planner/LLM** (whatever a plan says is ignored). `long_running` and `stream` tasks have no deadline; a stream emits `stream.frame` events. |
| `timeoutSeconds` | number (0, 3600] | ⬜ | Deadline for a `one_shot` capability (default 10s when omitted). Set it for slow actions (browser booking, file copy). Ignored for `long_running`/`stream`. |
| `controls` | object | ⬜ | What the user may do once the action is running: `{ "pause": bool, "cancel": "cooperative"\|"best_effort"\|"none" }`. Defaults: `pause: false`, `cancel: "best_effort"`. See §7 "controls". |
| `confirmationReason` | string | ⬜ | Human-readable justification shown in the prompt. Required for `risk: high`. |
| `confirmationPreview` | object | ⬜ | `{ "fields": [...], "maxChars": N }` — which parameter values to display in the confirm dialog. |
| `risk` | enum | ⬜ | `"low"` \| `"medium"` \| `"high"`. Auto-derived if omitted — see §7. |
| `permissions` | string[] | ⬜ | Capability-scoped permissions. Subset of the plugin's top-level `permissions`. |
| `precondition` | object | ⬜ | Guard evaluated *before* the plugin runs. See §7. |
| `parameters` | object | ⬜ | JSON Schema (object type). Defaults to `{ "type": "object", "properties": {} }`. See §6. |

---

## 6. Parameters (JSON Schema)

`parameters` follows **JSON Schema Draft 2020-12** with a few
enforcements:

1. The top level must be `"type": "object"`.
2. Every entry in `required` must also exist in `properties`.
3. `required` on a non-object parameter is rejected.
4. Arrays may omit `items` (any-array), but the linter warns — always
   provide `items` so the LLM knows the element shape.

### Simple parameters

```json
"parameters": {
  "type": "object",
  "properties": {
    "path":     { "type": "string",  "description": "Absolute path" },
    "recursive":{ "type": "boolean", "default": false },
    "limit":    { "type": "integer", "default": 500, "minimum": 1, "maximum": 5000 }
  },
  "required": ["path"]
}
```

### Enums

```json
"lens": {
  "type": "string",
  "enum": ["front", "back"],
  "default": "back"
}
```

### Nested objects

```json
"fields": {
  "type": "object",
  "properties": {
    "email": { "type": "string" },
    "name":  { "type": "string" }
  },
  "required": ["email"]
}
```

### Arrays

```json
"steps": {
  "type": "array",
  "items": {
    "type": "object",
    "properties": {
      "action":   { "type": "string", "enum": ["navigate", "fill", "click"] },
      "selector": { "type": "string" },
      "value":    { "type": "string" }
    },
    "required": ["action"]
  }
}
```

### What survives validation

Every JSON Schema keyword is preserved on
`manifest.capability_schemas[cap_id]["parameters"]` — including
`pattern`, `minLength`, `format`, `examples`, and any future keyword
you use. Only `type`, `description`, `default`, `enum`, `properties`,
`required`, `items` are additionally surfaced as typed
`CapabilityParameter` objects for programmatic form builders.

---

## 7. Confirmation, preconditions, risk

### `requireConfirmation`

Set `true` for anything that:

- reads or writes user data (`clipboard.read`, `files.write`)
- captures the screen / camera / microphone
- executes code (`shell.exec`)
- navigates to arbitrary URLs (`browser.navigate`)
- spends money or books services (`browser.book`)

Show the user the reason in `confirmationReason`. Be specific — "confirm?"
is not a reason.

### `retryable`

By default a failed capability call is retried automatically (exponential backoff, a
few attempts) and an in-flight call interrupted by a server restart is re-sent. Every
re-send carries the same `idempotency_key`, but a device can only de-duplicate if it
remembers the key — so for anything with an external side effect that is **not**
idempotent, declare:

```json
{ "id": "sms.send", "retryable": false, "requireConfirmation": true }
```

With `retryable: false`:

- a failure is final (`task.failed` is emitted once; no `task.retrying`);
- a task that was **running** when the server stopped or crashed is marked failed
  ("Interrupted … not retryable") instead of being re-sent — it may already have run;
  a task that never reached the device is still queued normally;
- the user can still press **Retry** (`POST /tasks/{id}/retry`) — that is an explicit decision.

The flag is read from the manifest only; a plan (or the LLM) cannot turn it back on.

### `controls`

Pause and cancel are decided by **what the capability can do**, not by the task type
(`one_shot` / `long_running` / `stream` only change timeouts). Declare it per capability:

```json
{ "id": "camera.record", "controls": { "pause": true,  "cancel": "cooperative" } }
{ "id": "sms.send",      "controls": { "pause": false, "cancel": "none" } }
```

| Field | Meaning |
|---|---|
| `pause: true` | The plugin can really pause **and acknowledges** the pause (a `control_ack` frame). If the device does not ack, the pause fails with HTTP 409 `control_not_acknowledged` and the task state is **not** changed. `false` (default): once the call is dispatched it cannot be paused (409 `control_not_supported`). |
| `cancel: "cooperative"` | The plugin checks for cancellation and stops promptly. |
| `cancel: "best_effort"` (default) | The backend stops waiting at once and asks the device to stop; the device may still finish. The task records `cancel_outcome: "abandoned"` ("may have completed") unless the device acks (`"acked"`). |
| `cancel: "none"` | Cannot be cancelled once dispatched (HTTP 409 `control_not_supported`). |

Before the call reaches the device (queued, starting, waiting for confirmation, waiting to
retry) pause and cancel always work — nothing has run yet (`cancel_outcome: "not_started"`).
The flags are read from the manifest only; a plan (or the LLM) cannot grant itself pause.

### `maxGrantScope` (how long an approval may last)

When a capability asks for confirmation (`requireConfirmation: true`) the user chooses at the
prompt: **once** (just this run), **this session**, or **always** (persistent, revocable,
expires after 90 days by default; optionally tied to specific parameters). The broadest choice
offered is decided by the server:

| Risk | Broadest approval offered |
|---|---|
| `low`, `medium` | `always` |
| `high` | `session` |
| `critical` | `once` only |

`maxGrantScope` lets a manifest make that **stricter** — `{ "id": "files.delete", "maxGrantScope": "once" }`
means "always ask" — but never broader. Actions on the always-approve list (e.g. camera control)
can only ever be approved once. The limit is enforced server-side: a client asking for more is
rejected (HTTP 422). Like `controls`, it is read from the manifest only; a plan (or the LLM)
cannot grant itself a wider approval.

### Preconditions

A precondition is checked **before** the plugin runs. If it fails, the
call is rejected with a structured error and no side effect occurs.

| `type` | Meaning | Extra fields |
|---|---|---|
| `path_in_roots` | `field` (a string parameter) must resolve to a path inside one of the roots. | `allowedRootsRef` → dotted path into `metadata` |
| `command_prefix` | `field` must begin with one of the whitelisted prefixes. | `allowedPrefixesRef` → dotted path into `metadata` |
| `env_present` | The named env var must be set. | `env` |
| `consent_flag` | The named env var must equal `"1"`. | `env` |

**The `*Ref` values are dotted paths into `metadata`.** Example:

```json
"metadata": {
  "allowedRoots": ["~/Documents", "~/Downloads", "/tmp/pai"]
},
"capabilities": [{
  "id": "files.write",
  ...
  "precondition": {
    "type": "path_in_roots",
    "field": "path",
    "allowedRootsRef": "metadata.allowedRoots"
  }
}]
```

> **Gotcha:** `allowedRootsRef` is camelCase; the `metadata` key it
> points at must be spelled **exactly** the same (also camelCase).

### Risk levels

| Level | When |
|---|---|
| `low` | Read-only, non-sensitive (`hello.greet`, `audio.play`). |
| `medium` | Modifies local state without confirmation (`files.stat`, `media.control`). |
| `high` | Destructive or irreversible (`files.write`, `files.delete`, `shell.exec`). |

If omitted, `risk` is inferred:

- `requireConfirmation: true` + `precondition` → `high`
- `requireConfirmation: true` alone → `medium`
- otherwise → `low`

Override by setting `risk` explicitly.

---

## 8. Recipes

### Server-side Python with dependencies

```json
{
  "id": "browser",
  "type": "python",
  "runtime": { "kind": "server", "nativeModule": "pai.browser.server" },
  "deviceTypes": ["server"],
  "dependencies": ["playwright>=1.48,<2.0", "httpx>=0.27,<1.0"],
  "postInstall": [
    ["python", "-m", "playwright", "install", "chromium", "--with-deps"]
  ],
  "capabilities": [ … ]
}
```

### Device-side native plugin

```json
{
  "id": "camera",
  "type": "native",
  "runtime": { "kind": "device", "nativeModule": "pai.camera", "minPaiVersion": "0.4.0" },
  "platforms": {
    "linux": { "artifact": "linux/libpai_camera.so", "entrypoint": "pai_plugin_module_create" }
  },
  "architectures": ["x86_64"],
  "deviceTypes": ["desktop"]
}
```

### Android (kotlin) variant of the same plugin

```json
{
  "id": "camera",
  "type": "kotlin",
  "runtime": { "kind": "device", "nativeModule": "pai.camera" },
  "platforms": {
    "android": {
      "artifact": "android/plugin.dex",
      "entrypoint": "com.pai.plugins.camera.CameraPlugin"
    }
  },
  "architectures": ["x86_64", "arm64-v8a", "arm64"],
  "deviceTypes": ["mobile"]
}
```

Both live under `plugin_registry/plugins/camera/1.0.0/{linux,android}/manifest.json`.

### Shell with a command-prefix precondition

```json
"metadata": {
  "allowedPrefixes": ["ls", "pwd", "git status", "docker ps"]
},
"capabilities": [{
  "id": "shell.exec",
  "requireConfirmation": true,
  "confirmationReason": "Executing shell commands can be dangerous.",
  "precondition": {
    "type": "command_prefix",
    "field": "command",
    "allowedPrefixesRef": "metadata.allowedPrefixes"
  },
  "parameters": {
    "type": "object",
    "properties": {
      "command":    { "type": "string" },
      "timeoutSec": { "type": "integer", "default": 30 }
    },
    "required": ["command"]
  }
}]
```

---

## 9. Validation & linting

### Validate

```bash
./plugin_registry/tests/install_plugin_manifest.sh plugin_registry/plugins
```

Runs two layers:

1. **JSON Schema** (`manifest.schema.json`) — structural validation with
   exact JSON-Pointer locations on error.
2. **Pydantic** (`PluginManifest`) — semantic validation: unique
   capability ids, `required ⊆ properties`, `kind ↔ deviceTypes`, etc.

You can also validate a single file:

```bash
python -m pai.plugins.manifest_install plugin_registry/plugins/files/1.0.0/manifest.json
```

### Lint (advisory)

The same command also emits non-fatal warnings:

```
OK  plugin_registry/plugins/browser/1.0.1/manifest.json: browser@1.0.1 (python, server)
    WARN capability 'browser.fillForm': object parameter has no 'properties'
    WARN capability 'browser.book': array parameter has no 'items' schema
```

Warnings don't block install, but every one of them hurts LLM reliability
at runtime — the model can't construct valid arguments for a parameter
whose shape it can't see.

### CI

Add to `.github/workflows/manifests.yml`:

```yaml
- run: ./plugin_registry/tests/install_plugin_manifest.sh plugin_registry/plugins
```

Every PR that touches a `manifest.json` will fail fast on invalid keys,
wrong enums, missing `author`/`license`, etc.

---

## 10. Publishing checklist

Before merging a new plugin:

- [ ] `id` is unique, lowercase, and matches the directory name.
- [ ] `version` is semver; bump it for any manifest change.
- [ ] `author` and `license` are present.
- [ ] `type` and `runtime.kind` are correctly paired (see §3).
- [ ] `deviceTypes` matches `runtime.kind` (`server` ⇔ `deviceTypes` contains `"server"`).
- [ ] Every capability `id` has the `<plugin-id>.<verb>` prefix and is unique.
- [ ] Every `required` field in `parameters` exists in `properties`.
- [ ] Every array parameter has `items`; every object parameter has `properties`.
- [ ] Every `*Ref` in a precondition points at a real `metadata` key with the *exact* spelling.
- [ ] `requireConfirmation: true` is set for any destructive / privacy-sensitive capability, with a `confirmationReason`.
- [ ] `description` on each capability says **when to use it**, not just what it does.
- [ ] `./plugin_registry/tests/install_plugin_manifest.sh` prints `OK` with no `WARN` lines.
- [ ] `PluginPackager` produces a tarball; its `sha256` and `size` are recorded (this happens automatically once the plugin is published to the registry — the manifest never carries these).

Once these pass, the manifest is complete. The `PluginPackager` fills
in `packageUrl`, `checksum`, and `size` automatically at publish time —
you never hard-code them in `manifest.json`.

---

## Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `runtime.kind: 'native' is not one of ['server', 'device']` | Using the language as the kind. | Set `runtime.kind: "device"` and `type: "native"`. |
| `'server' not in deviceTypes` | `runtime.kind: "server"` but `deviceTypes: ["desktop"]`. | Add `"server"` to `deviceTypes`. |
| `duplicate capability ids: ['camera.capture']` | Two platform manifests each declare the same capability and were merged. | Use the per-platform layout (each platform has its own directory) — the merger dedupes by `id`, so this only fires if you wrote the same id twice in one file. |
| `'required' references unknown properties: ['foo']` | Typo in `required`. | Every name in `required` must exist in `properties`. |
| `array parameter must declare 'items'` | (If you see this after upgrading, you have a stale validation layer.) | Arrays without `items` are **allowed** — provide `items` anyway for LLM clarity. |
| Linter warns `object parameter has no 'properties'` | `{"type": "object"}` with no schema. | Add `properties` (even `additionalProperties: { "type": "string" }` helps). |

---

## Reference manifests

Look at these in `plugin_registry/plugins/` for working examples:

| Plugin | Demonstrates |
|---|---|
| `files/1.0.0` | `path_in_roots` precondition, `metadata.allowedRoots`, destructive `requireConfirmation`. |
| `shell/1.0.0` | `command_prefix` precondition, `metadata.allowedPrefixes`, `confirmationReason`. |
| `browser/1.0.1` | Server plugin, `dependencies` + `postInstall`, enum parameters. |
| `camera/1.0.0/linux` | Device native plugin, `.so` artifact, `minPaiVersion`. |
| `camera/1.0.0/android` | Same plugin, Kotlin variant, multi-architecture, per-platform layout. |
| `desktop/1.0.0` | Multiple capabilities with mixed confirmation rules. |
| `screen/1.0.0` | `metadata.requires_consent_env`, single-capability plugin. |

---

## Versioning & updates

Three independent version axes are tracked:

| Axis | Where it comes from | Checked against |
|------|--------------------|-----------------|
| **Plugin version** | `version` (semver, must be a valid version) | what a device has installed |
| **Manifest schema** | `schemaVersion` (server accepts 1-2, see `src/pai/version.py`) | the server's supported range |
| **Runtime / app** | `runtime.minPaiVersion` (backend), `runtime.minAppVersion` (Flutter app) | `RUNTIME_VERSION` / the device's reported `app_version` |

Publish a new version by adding a sibling directory (`plugins/camera/2.0.0/<platform>/manifest.json`);
older versions stay on disk so devices that still run them keep working.

### Where the versions are configured, and what CI enforces

`pyproject.toml` is the single source of truth: `[tool.pai.versions]` (runtime, manifest schema range, app, minimum
app) and `[tool.pai.plugins]` (registry path, tag format, release rules). After editing it run
`pai-sync-versions --write` to update the copies (`src/pai/version.py`, `[project].version`, the `schemaVersion`
range in `src/pai/plugins/manifest.schema.json`, `frontend/pubspec.yaml`, `frontend/lib/core/app_version.dart`).

| Command | Checks |
|---------|--------|
| `pai-validate-manifests plugin_registry/plugins` | every manifest passes the JSON Schema **and** the Pydantic models |
| `pai-sync-versions` | no copy of a version drifted from pyproject.toml |
| `pai-check-plugin-versions` | folder = manifest `version` (X.Y.Z), manifest `id` = folder (or `<folder>_<platform>`), `schemaVersion` in range, `minPaiVersion`/`minAppVersion` not newer than this repo, **a version that exists on `origin/main` is never changed**, new versions are higher than all existing ones, an update has `changelog.summary` |
| `pai-package-plugin plugin/<id>/v<version>` | builds the release files (`<id>-<version>-<platform>.tar.gz`, `SHA256SUMS`, notes) |

GitHub Actions: `plugin-manifests.yml` (schema + models + packaging), `versions.yml` (drift + the plugin rules above,
against the base branch), `release-plugin.yml` (push the tag `plugin/<id>/v<version>` from `main` to publish a version),
`tests.yml` (backend + admin JS tests). `src/pai/plugins/manifest.schema.json` must mirror the models in
`manifest_models.py`; `tests/plugins/test_manifest_schema.py` fails when they drift.

```json
"version": "2.0.0",
"runtime": { "kind": "device", "nativeModule": "pai.camera",
             "minPaiVersion": "1.0.0", "minAppVersion": "1.4.0" },
"changelog": {
  "summary": "Live streaming",
  "features": ["camera.stream: live preview"],
  "fixes": [],
  "breaking": []
}
```

Behaviour:

* **Update button** - the Plugins screen shows `v1.0.0 -> v2.0.0` and an *Update* button when a newer version
  that the device can run exists (`GET /plugins/updates`). The sheet lists the release notes of every skipped
  version plus the capabilities added / removed / changed (computed from the manifests, not hand-written).
* **Gating** - a version whose `minPaiVersion` > server, `minAppVersion` > the device's app, or
  `schemaVersion` outside the supported range is never offered for install/update (install returns 409
  `incompatible_version`). A newer-but-blocked version is shown as "needs app X".
* **Planning** - the planner's capability list is built per device from the manifest of the version that
  device has **installed**: a device on camera 1.0.0 is never offered `camera.stream` added in 2.0.0.
  Execution authorisation (confirmation, risk) also reads that same version's manifest.
* Remember to bump `kAppVersion` (`frontend/lib/core/app_version.dart`) together with `pubspec.yaml`.

---

## Build output: the `artifact/` folder

Each platform folder keeps its build output in one place and **only that folder is packaged**:

```
plugins/<plugin_id>/<version>/<platform>/
    manifest.json
    artifact/            <- build output: .so, .dex, .py, assets ... EVERYTHING here ships
        libpai_desktop.so
    src/  tests/  CMakeLists.txt   <- never packaged
```

* The packager (`src/pai/plugins/packager.py`) archives every file under `artifact/` (no
  symlinks, no dotfiles). Nothing else in the plugin folder can leak into a package, and a
  missing `artifact/` folder is an error, not a silent fallback.
* Inside the package each file sits at `<platform>/<file>` (`linux/artifact/libx.so` becomes
  `linux/libx.so`). The manifest's `platforms.<os>.artifact` is that packaged path of the main
  library, e.g. `"artifact": "linux/libpai_desktop.so"`; it must be one of the files in `artifact/`.
* A plugin can ship several libraries or extra files: just put them in `artifact/`.
* A released version never changes: devices that already have it would keep the old binary and
  never see an update. Change anything in `artifact/` -> bump the version.

Build native Linux plugins with `shared/build_linux.sh` (latest version of every plugin, or
`desktop`, `desktop@1.2.0`; `--list` to preview). It builds out of tree, writes to `artifact/`,
removes stale `*.so` there, and refuses to overwrite a git-tracked artifact with different bytes.
Restart the server afterwards: it packages plugins at startup.


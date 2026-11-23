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

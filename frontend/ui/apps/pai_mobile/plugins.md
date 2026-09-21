# PAI Plugin System — Technical Documentation

**Version:** 1.0
**Audience:** PAI core developers, plugin authors, security reviewers
**Status:** Design + implementation reference

---

## Table of Contents

1. [Overview](#1-overview)
2. [Design principles](#2-design-principles)
3. [Glossary](#3-glossary)
4. [Architecture](#4-architecture)
5. [Plugin package format](#5-plugin-package-format)
6. [The plugin registry](#6-the-plugin-registry)
7. [On-disk layout](#7-on-disk-layout)
8. [Dart host layer](#8-dart-host-layer)
9. [Native host layer](#9-native-host-layer)
10. [The C ABI](#10-the-c-abi)
11. [Plugin lifecycle](#11-plugin-lifecycle)
12. [Command dispatch](#12-command-dispatch)
13. [Security model](#13-security-model)
14. [Writing a plugin](#14-writing-a-plugin)
15. [Platform specifics](#15-platform-specifics)
16. [Failure modes and recovery](#16-failure-modes-and-recovery)
17. [Versioning and compatibility](#17-versioning-and-compatibility)
18. [Testing](#18-testing)
19. [Appendix A — File reference](#appendix-a--file-reference)
20. [Appendix B — Method channel protocol](#appendix-b--method-channel-protocol)
21. [Appendix C — Registry API](#appendix-c--registry-api)

---

## 1. Overview

The PAI Plugin System allows end users to **discover, install, enable, disable, and uninstall plugins at runtime** without updating or rebuilding the PAI application. Plugins provide additional **capabilities** — discrete operations like `camera.capture`, `whatsapp.send_message`, or `browser.navigate` — that the PAI server can invoke on the user's device.

The PAI app itself contains **no plugins**. It ships only:

- a **Dart host runtime** that manages plugin packages, downloads, and integrity
- a **native host runtime** (per platform) that loads plugin code at runtime
- a **stable C ABI** that every plugin exports

Plugins are **signed, versioned archives** fetched from a **plugin registry** on demand.

The single sentence that describes the whole system:

> **The app is a loader. Plugins are signed data the user installs. Native code is `dlopen`'d at enable time.**

Everything in this document is a consequence of that sentence.

---

## 2. Design principles

| # | Principle | Consequence |
|---|---|---|
| 1 | The app never ships a plugin | Adds zero build-time dependencies for new plugins |
| 2 | Plugins are data, not code paths in the app | Dart never branches on plugin id |
| 3 | Native code is loaded at runtime, never linked | Adding a plugin never requires recompiling the app |
| 4 | Every plugin declares what it can do | Capabilities are enumerable, matchable, and routable |
| 5 | Every plugin declares what it needs | Permissions are surfaced to the user before enable |
| 6 | Nothing loads before it is verified | SHA-256 + signature are checked before `dlopen` |
| 7 | The Dart layer cannot load code | All code loading is native |
| 8 | The native layer cannot download | All networking is Dart |
| 9 | The ABI is stable and language-neutral | Plugins can be written in C, C++, Rust, Zig, Kotlin/JNI, Swift |
| 10 | Users control the runtime | Enable / disable is a first-class user action, not a build flag |

---

## 3. Glossary

| Term | Meaning |
|---|---|
| **Plugin** | A signed package (`id` + `version`) that provides one or more capabilities |
| **Capability** | A named operation, e.g. `camera.capture`, `whatsapp.send_message` |
| **Permission** | A logical access requirement, e.g. `camera`, `microphone`, `filesystem` |
| **Native module** | The shared library that implements a plugin's capabilities (e.g. `libpai_camera.so`) |
| **Plugin store** | On-disk directory where installed plugins live |
| **Registry** | HTTP service that serves the plugin catalog and artifacts |
| **Manifest** | JSON declaration of a plugin's identity, capabilities, permissions, artifacts |
| **Artifact** | The per-platform binary file referenced by the manifest |
| **Entrypoint** | Exported symbol the host uses to obtain a `pai_plugin_module_t*` |
| **Host** | The PAI app runtime (Dart + native) that loads plugins |
| **Capability resolver** | The component that maps capability id → plugin → native module |
| **Trust store** | The set of public keys the host accepts as plugin signers |
| **ABI** | The binary interface between the host and the plugin's native code |

---

## 4. Architecture

### 4.1 Layered view

```
┌──────────────────────────────────────────────────────────────────────┐
│                            PAI SERVER                                │
│   Planner · Device Registry · Capability Resolver · Plugin Index     │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │  task:  camera.capture { lens: "back" }
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    DART HOST  (lib/plugins/*)                        │
│                                                                      │
│   PluginRepository  ──►  PluginInstaller  ──►  PluginRegistry        │
│        (HTTP)              (disk)                 (in-memory)        │
│                                                                      │
│   PluginManager · PluginCommandRouter · PlatformPluginRegistration   │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │  MethodChannel("pai/plugin_runtime")
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  NATIVE HOST  (linux/, android/, …)                  │
│                                                                      │
│   PluginChannel ──► PluginManager ──► NativeModuleRegistry           │
│                                            │                         │
│                                            ▼                         │
│                                      DynamicLoader                   │
│                                      (dlopen / JNI)                  │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │  C ABI (pai_plugin_module_t)
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        PLUGIN .so / .dll                             │
│                implements pai_plugin_module_create()                 │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.2 Data flow summary

| Direction | Path | Purpose |
|---|---|---|
| Registry → Dart | HTTP | Fetch catalog, manifests, artifacts |
| Dart → Disk | Filesystem | Persist manifests and downloaded binaries |
| Dart → Native | MethodChannel | Request module load / unload / invoke |
| Native → Plugin | C ABI | `dlopen` + `dlsym` + function calls |
| Server → Dart | WebSocket | Deliver capability invocations |
| Dart → Server | WebSocket | Return capability results |

### 4.3 Trust boundaries

| Boundary | Trusted side | Untrusted side |
|---|---|---|
| Registry ↔ Dart | Host | Registry responses (must be signature-verified) |
| Dart ↔ Native | Both | — (in-process) |
| Native ↔ Plugin | Host | Plugin binary (must be signature-verified) |
| Plugin ↔ OS | OS | Plugin (constrained by OS sandbox) |

---

## 5. Plugin package format

### 5.1 Structure

A plugin is a **signed, versioned archive**. Conceptually:

```
pai-plugin-camera-1.0.0.zip
├── manifest.json
├── icon.png
├── README.md
├── LICENSE
└── native/
    ├── android/
    │   ├── arm64-v8a/libpai_camera.so
    │   ├── armeabi-v7a/libpai_camera.so
    │   └── x86_64/libpai_camera.so
    ├── linux/
    │   └── libpai_camera.so
    ├── windows/
    │   └── pai_camera.dll
    └── macos/
        └── PaiCamera.framework
```

The host downloads **only the artifact for the current platform**. The registry serves per-platform URLs so the client can pick the correct one.

### 5.2 `manifest.json`

```json
{
  "schemaVersion": 1,
  "id": "camera",
  "name": "Camera",
  "version": "1.0.0",
  "description": "Capture photos and record video using the device camera.",
  "author": "PAI Labs",
  "homepage": "https://pai.example/plugins/camera",
  "license": "MIT",
  "icon": "icon.png",

  "runtime": {
    "kind": "native",
    "nativeModule": "pai.camera",
    "minPaiVersion": "0.4.0"
  },

  "platforms": {
    "android": {
      "artifact": "native/android/arm64-v8a/libpai_camera.so",
      "entrypoint": "pai_plugin_module_create"
    },
    "linux": {
      "artifact": "native/linux/libpai_camera.so",
      "entrypoint": "pai_plugin_module_create"
    },
    "windows": {
      "artifact": "native/windows/pai_camera.dll",
      "entrypoint": "pai_plugin_module_create"
    },
    "macos": {
      "artifact": "native/macos/PaiCamera.framework",
      "entrypoint": "pai_plugin_module_create"
    }
  },

  "permissions": ["camera", "microphone", "filesystem"],

  "capabilities": [
    {
      "id": "camera.capture",
      "description": "Take a still photo.",
      "permissions": ["camera", "filesystem"],
      "parameters": {
        "type": "object",
        "properties": {
          "lens":       { "type": "string", "enum": ["front", "back"], "default": "back" },
          "resolution": { "type": "string", "default": "high" },
          "outputPath": { "type": "string" },
          "format":     { "type": "string", "enum": ["jpeg", "png"], "default": "jpeg" }
        }
      }
    },
    {
      "id": "camera.record",
      "description": "Record a video.",
      "permissions": ["camera", "microphone", "filesystem"],
      "parameters": {
        "type": "object",
        "properties": {
          "lens":       { "type": "string", "enum": ["front", "back"], "default": "back" },
          "durationMs": { "type": "integer", "default": 5000 },
          "outputPath": { "type": "string" },
          "audio":      { "type": "boolean", "default": true }
        }
      }
    }
  ],

  "integrity": {
    "sha256": "...",
    "signature": "...",
    "signerKeyId": "pai-labs-2025"
  }
}
```

### 5.3 Field reference

| Field | Required | Description |
|---|---|---|
| `schemaVersion` | yes | Manifest schema version (currently `1`) |
| `id` | yes | Globally unique plugin identifier, `[a-z0-9-]+` |
| `name` | yes | Human-readable name |
| `version` | yes | Semantic version |
| `description` | yes | One-line summary |
| `author` | no | Maintainer |
| `homepage` | no | Project URL |
| `license` | no | SPDX identifier |
| `icon` | no | Path to a PNG inside the package |
| `runtime.kind` | yes | `native` \| `dart` \| `external` |
| `runtime.nativeModule` | yes if native | Logical module name passed to native runtime |
| `runtime.minPaiVersion` | yes | Minimum host version required |
| `platforms.<os>.artifact` | yes | Relative path to the binary |
| `platforms.<os>.entrypoint` | yes | Exported symbol name |
| `permissions` | yes | Union of all capabilities' permissions |
| `capabilities` | yes | Non-empty list of capability descriptors |
| `capabilities[].id` | yes | Dotted operation name |
| `capabilities[].parameters` | no | JSON Schema for the parameter map |
| `integrity.sha256` | yes | Hash of the artifact for the current platform |
| `integrity.signature` | yes | Ed25519 signature over the canonical manifest |
| `integrity.signerKeyId` | yes | Key id resolvable in the host trust store |

### 5.4 Canonical manifest encoding

Signature verification is performed over the **canonical JSON** of the manifest with the `integrity.signature` field removed:

1. Remove `integrity.signature`.
2. Serialize the object with **sorted keys**, no whitespace, UTF-8.
3. Verify the Ed25519 signature against the bytes.

This guarantees deterministic verification across languages.

---

## 6. The plugin registry

The registry is an HTTP service serving three resources.

### 6.1 `GET /plugins/index.json`

The catalog. Fetched on demand, cached locally.

```json
{
  "schemaVersion": 1,
  "updatedAt": "2025-01-15T10:00:00Z",
  "plugins": [
    {
      "id": "camera",
      "name": "Camera",
      "latest": "1.0.0",
      "versions": {
        "1.0.0": {
          "manifestUrl": "https://cdn.pai.example/plugins/camera/1.0.0/manifest.json",
          "artifacts": {
            "android": "https://cdn.pai.example/plugins/camera/1.0.0/android-arm64.so",
            "linux":   "https://cdn.pai.example/plugins/camera/1.0.0/linux.so"
          },
          "sha256": {
            "android": "…",
            "linux":   "…"
          },
          "sizeBytes": {
            "android": 812345,
            "linux":   234567
          },
          "minPaiVersion": "0.4.0",
          "publishedAt": "2025-01-10T00:00:00Z"
        }
      }
    }
  ]
}
```

### 6.2 `GET <manifestUrl>`

Returns the manifest for one plugin version.

### 6.3 `GET <artifactUrl>`

Returns the binary artifact. Must set `Content-Type: application/octet-stream` and support range requests for resumable download (optional).

### 6.4 Registry requirements

| Requirement | Notes |
|---|---|
| HTTPS | Mandatory; HTTP is rejected |
| Immutable artifacts | Once published, `(id, version)` content never changes |
| SHA-256 in the index | Required for client-side verification |
| Signature in the manifest | Required for trust |
| CDN-friendly | Artifacts should be cacheable |

### 6.5 Cache policy

| Resource | Cache duration | Storage |
|---|---|---|
| `index.json` | 1 hour | In-memory |
| `manifest.json` | Forever (immutable per version) | Plugin store |
| Artifact | Forever (immutable per version) | Plugin store |

---

## 7. On-disk layout

### 7.1 Plugin store path by platform

| Platform | Root |
|---|---|
| Android | `<getApplicationSupportDirectory()>/plugins/` |
| Linux | `~/.local/share/pai/plugins/` |
| Windows | `%APPDATA%\PAI\plugins\` |
| macOS | `~/Library/Application Support/PAI/plugins/` |
| iOS | `<Application Support>/plugins/` |

### 7.2 Structure

```
<plugins-root>/
├── installed.json
├── camera/
│   └── 1.0.0/
│       ├── manifest.json
│       ├── icon.png
│       └── native/
│           └── libpai_camera.so
└── whatsapp/
    └── 2.1.0/
        ├── manifest.json
        └── native/
            └── libpai_whatsapp.so
```

### 7.3 `installed.json`

The single source of truth for what is installed and enabled.

```json
{
  "camera":   { "version": "1.0.0", "enabled": true  },
  "whatsapp": { "version": "2.1.0", "enabled": false }
}
```

The file is written atomically (write to `.tmp`, then rename).

### 7.4 Invariants

1. Every `<id>/<version>/manifest.json` is byte-identical to the manifest returned by the registry.
2. Every `<id>/<version>/native/<artifact>` has a matching SHA-256 recorded in `installed.json`.
3. `installed.json` never references a directory that doesn't exist.
4. Directories without an `installed.json` entry are garbage and can be reaped.

---

## 8. Dart host layer

### 8.1 Module map

```
lib/plugins/
├── models/
│   ├── plugin_capability.dart          Capability descriptor
│   ├── plugin_info.dart                Full manifest
│   └── plugin_info_short.dart          Lightweight UI descriptor
├── runtime/
│   ├── plugin_runtime.dart             MethodChannel façade
│   └── dart_plugin_loader.dart         (Optional) pure-Dart plugins
├── plugin_repository.dart              HTTP client for the registry
├── plugin_installer.dart               Download + verify + persist
├── plugin_registry.dart                In-memory index of DevicePlugin
├── plugin_manager.dart                 install/enable/disable/uninstall
├── plugin_loader.dart                  Startup bootstrap
├── plugin_command_router.dart          Capability → native module dispatch
├── plugin_service.dart                 Public façade for the UI/server
├── plugin_factory.dart                 Build DevicePlugin from manifest JSON
├── platform/
│   └── platform_plugin_registration.dart  Dart ↔ native load bridge
├── device_plugin.dart                  In-memory plugin instance
└── plugin_command_handler.dart         (Optional) for Dart-only plugins
```

### 8.2 Responsibilities

| Module | Owns | Does NOT own |
|---|---|---|
| `PluginRepository` | HTTP, retries, timeouts | Disk writes |
| `PluginInstaller` | Disk layout, integrity, `installed.json` | HTTP |
| `PluginRegistry` | In-memory map of `DevicePlugin` | Persistence |
| `PluginManager` | State transitions | Binary loading |
| `PluginLoader` | Bootstrapping the above | UI |
| `PluginCommandRouter` | Capability lookup + dispatch | Execution |
| `PluginRuntime` | MethodChannel calls | Business logic |
| `PluginService` | Public API for UI + server | Implementation details |

### 8.3 Public API

```dart
class PluginService {
  Future<List<Map<String, dynamic>>> browse();
  Future<PluginInfo> install(String id, String version);
  Future<bool> enable(String id);
  Future<void> disable(String id);
  Future<void> uninstall(String id);
  Future<Map<String, dynamic>> invoke(String capability, Map<String, dynamic> params);
  Future<void> restoreInstalled();
}
```

Any code outside `lib/plugins/` — UI, WebSocket provider, chat service — interacts with the plugin system **only through this class**.

---

## 9. Native host layer

The native host differs per platform only in *how* it loads code. Everything above the loader is identical.

### 9.1 Common structure

```
agent/
├── abi/
│   └── pai_plugin_abi.h            The stable C ABI
├── bridge/
│   └── plugin_channel.{h,cc,kt}    MethodChannel handler
├── plugins/
│   ├── plugin_module.{h}           Abstract module interface (per language)
│   ├── native_module_registry.{h,cc,kt}
│   ├── plugin_manager.{h,cc,kt}
│   └── dynamic_loader.{h,cc,kt}    dlopen / JNI
└── permissions/
    └── permission_manager.{h,cc,kt}
```

### 9.2 Responsibilities

| Component | Role |
|---|---|
| `PluginChannel` | Receives MethodChannel calls; returns results |
| `NativeModuleRegistry` | Holds loaded `PluginModule` objects keyed by `nativeModule` name |
| `PluginManager` | Dispatches `invoke` / `requestPermissions` to the right module |
| `DynamicLoader` | `dlopen` + `dlsym` (Linux/Android), `LoadLibrary` (Windows) |
| `PermissionManager` | Maps logical permissions to OS permissions and prompts |

### 9.3 Platform loader mechanics

| Platform | API | Notes |
|---|---|---|
| Linux | `dlopen(path, RTLD_NOW|RTLD_LOCAL)` | Path must be writable + executable by the user |
| Android | `dlopen(path)` via JNI | Path must be inside the app's data dir; APK must have `extractNativeLibs=true` |
| Windows | `LoadLibraryW(path)` | Path must be a valid PE DLL |
| macOS | `dlopen` | Plugin must be signed and pass Gatekeeper; entitlement `disable-library-validation` if needed |
| iOS | **Not supported** | iOS forbids loading unsigned native code; only Dart plugins |

### 9.4 Threading

- MethodChannel callbacks arrive on the platform thread.
- `invoke` is dispatched to a background executor / coroutine.
- Results are posted back to the platform thread before `result.success(...)`.
- Only one `invoke` per module runs at a time; `PluginManager` serializes with a per-module mutex.

---

## 10. The C ABI

Every plugin exports a **single vtable of function pointers**. This is what makes the system language-neutral.

### 10.1 Header

```c
#ifndef PAI_PLUGIN_ABI_H
#define PAI_PLUGIN_ABI_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define PAI_PLUGIN_ABI_VERSION 1

typedef struct {
    const char* key;
    const char* value;
} pai_kv_t;

typedef struct {
    const pai_kv_t* items;
    size_t count;
} pai_kv_list_t;

typedef struct pai_plugin_module {
    int abi_version;

    const char* (*name)(void);
    const char** (*capabilities)(size_t* out_count);
    const char** (*permissions)(size_t* out_count);

    int (*invoke)(
        const char* capability,
        const pai_kv_list_t* params,
        pai_kv_list_t* out_result,
        char** out_error);

    int (*request_permissions)(
        const char** permissions,
        size_t count,
        pai_kv_list_t* out_result,
        char** out_error);

    void (*free_kv_list)(pai_kv_list_t* list);
    void (*free_string)(char* s);
} pai_plugin_module_t;

typedef pai_plugin_module_t* (*pai_plugin_create_fn)(void);
typedef void                (*pai_plugin_destroy_fn)(pai_plugin_module_t*);

pai_plugin_module_t* pai_plugin_module_create(void);
void                 pai_plugin_module_destroy(pai_plugin_module_t*);

#ifdef __cplusplus
}
#endif
#endif
```

### 10.2 Contract

| Field | Contract |
|---|---|
| `abi_version` | Must equal `PAI_PLUGIN_ABI_VERSION`; host rejects otherwise |
| `name` | Returns the module name, must equal `runtime.nativeModule` in the manifest |
| `capabilities` | Returns a `const char**` array; host copies the strings |
| `permissions` | Returns a `const char**` array; host copies the strings |
| `invoke` | Returns `0` on success, non-zero on failure; fills `out_result` and `out_error` |
| `request_permissions` | Returns `0` on success; fills `out_result` |
| `free_kv_list` | Frees the `items` array and all keys/values inside |
| `free_string` | Frees a string previously allocated by the plugin |

### 10.3 Memory ownership

- Strings in the arrays returned by `capabilities` / `permissions` are **owned by the plugin** and must live as long as the plugin module does.
- Strings inside `pai_kv_list_t` returned by `invoke` are **allocated by the plugin** and **freed by the host** via `free_kv_list`.
- Strings in `out_error` are **allocated by the plugin** and **freed by the host** via `free_string`.
- All strings crossing the ABI are UTF-8, NUL-terminated.

### 10.4 Thread safety

- `name`, `capabilities`, `permissions` may be called from any thread.
- `invoke` is guaranteed to be called from at most one thread at a time by the host.
- If the plugin spawns its own threads, it is responsible for its own synchronization.

### 10.5 ABI compatibility policy

| Change | Allowed in v1? |
|---|---|
| Add a new function pointer at the end of the struct | ✅ (host checks `abi_version`) |
| Reorder existing fields | ❌ |
| Remove a field | ❌ |
| Change a field's signature | ❌ |
| Change the meaning of a returned value | ❌ |

Breaking changes require `PAI_PLUGIN_ABI_VERSION` to be incremented. The host refuses to load plugins whose `abi_version` does not match.

---

## 11. Plugin lifecycle

### 11.1 States

```
     not-installed
          │
          │ install()    ← download + verify + write to disk
          ▼
       installed
          │
          │ enable()     ← native load + capability registration
          ▼
       enabled
          │
          │ disable()    ← native unload + capability deregistration
          ▼
       disabled
          │
          │ enable()     ← reload
          │
          │ uninstall()  ← native unload + disk removal
          ▼
     not-installed
```

Additional transient states:

| State | Meaning |
|---|---|
| `downloading` | Artifact transfer in progress |
| `verifying` | SHA-256 / signature check |
| `unavailable` | Platform doesn't match, or native load failed |
| `error` | Recoverable failure with a message |

### 11.2 Operations

| Operation | Dart | Native | Disk |
|---|---|---|---|
| `browse()` | HTTP fetch | — | — |
| `install(id, version)` | HTTP fetch + write | — | create `<id>/<version>/` |
| `enable(id)` | call native `loadNativeModule` | `dlopen` + register | update `installed.json` |
| `disable(id)` | call native `unloadNativeModule` | `dlclose` + deregister | update `installed.json` |
| `uninstall(id)` | disable + delete | — | remove `<id>/` |
| `restoreInstalled()` | scan disk | — | read `installed.json` |

### 11.3 Startup sequence

```
App starts
   │
   ▼
PluginService.restoreInstalled()
   │
   ├── PluginInstaller.discover()  ──► scans <plugins-root>/*
   │
   ├── for each manifest found:
   │      PluginManager.install(manifest)
   │      PluginRegistry.add(DevicePlugin)
   │
   └── reads installed.json
          └── for each entry where enabled == true:
                 PluginManager.enable(id)
                 └── native loadNativeModule(...)
```

### 11.4 Shutdown sequence

```
App exits
   │
   ▼
for each enabled plugin:
    PluginManager.disable(id)
    └── native unloadNativeModule(...)
          └── dlclose
```

On Android and Linux this happens implicitly when the process exits, but explicit unloading is safer for hot-reload scenarios.

---

## 12. Command dispatch

### 12.1 Input shape

A capability invocation arrives from the server (or local UI) as:

```
capability: "camera.capture"
parameters: { "lens": "back", "format": "jpeg" }
```

### 12.2 Resolution

```
PluginCommandRouter.route(capability, parameters)
   │
   ▼
PluginRegistry.resolveCapability(capability)
   │  scans enabled plugins for a matching capability id
   ▼
{ plugin, capability }  or  null
   │
   ▼  if null:
   return { ok: false, error: "capability_not_available" }
   │
   ▼  otherwise:
   module = plugin.info.runtime.nativeModule
   return PluginRuntime.invoke(
            nativeModule: module,
            capability: capability,
            parameters: parameters)
```

### 12.3 Native dispatch

```
PluginChannel receives "invoke"
   │
   ▼
PluginManager.Invoke(nativeModule, capability, params)
   │
   ▼
NativeModuleRegistry.Get(nativeModule)
   │  missing → { ok: false, error: "module_not_available" }
   ▼
module->Capabilities() contains capability?
   │  no → { ok: false, error: "capability_not_supported" }
   ▼
plugin_module_t::invoke(capability, params, &result, &err)
   │
   ▼  returns map
Dart receives { ... }
```

### 12.4 Error taxonomy

| Error string | Meaning |
|---|---|
| `capability_not_available` | No enabled plugin provides this capability |
| `module_not_available` | Native module not registered (load failed or unloaded) |
| `capability_not_supported` | Module is loaded but does not expose this capability |
| `permission_denied` | OS or user denied a required permission |
| `invalid_parameters` | Parameter schema validation failed |
| `plugin_error` | Plugin returned a non-zero status; details in `error` |
| `timeout` | Invocation exceeded the configured budget |

### 12.5 Timeouts and cancellation

Every invocation has a budget (default 30 seconds, configurable per capability). If exceeded:

1. The host returns `{ ok: false, error: "timeout" }`.
2. The plugin is *not* killed; it is expected to eventually return.
3. Repeated timeouts mark the plugin as degraded and disable it automatically.

---

## 13. Security model

### 13.1 Threat model

| Threat | Mitigation |
|---|---|
| Malicious registry serves a tampered binary | Signature check against trust store |
| Man-in-the-middle modifies artifact | TLS + SHA-256 check |
| Plugin from a compromised publisher | Signature verification + revocable key id |
| Plugin exploits the host | Runs in-process; limited by OS sandbox; no host API beyond the C ABI |
| Plugin reads user data without consent | Permission prompt before enable; OS-level enforcement |
| Malicious user adds a rogue registry | Registries must be explicitly added by the user and are pinned |
| Replay of an old plugin version | `minPaiVersion` + monotonic version policy |

### 13.2 Trust store

```dart
class TrustStore {
  static const Map<String, String> _trusted = {
    'pai-labs-2025': 'MCowBQYDK2VwAyEA...',   // base64 Ed25519 public key
  };

  static bool isTrusted(String keyId) => _trusted.containsKey(keyId);
  static Uint8List? publicKey(String keyId) => ...;
}
```

The trust store ships with the app. New signers require an app update, or an explicit user opt-in for third-party registries.

### 13.3 Verification pipeline

```
1. Fetch manifest
2. Parse JSON
3. Extract integrity.signerKeyId
4. Look up public key in TrustStore
   └── not found → reject
5. Canonicalize manifest (remove signature, sort keys, UTF-8)
6. Verify Ed25519 signature
   └── invalid → reject
7. Fetch artifact URL from index
8. Stream to disk while computing SHA-256
9. Compare SHA-256 with index value
   └── mismatch → delete partial, reject
10. Only then mark installed
```

### 13.4 Enable-time checks

Before `dlopen`:

- Verify the artifact file exists and matches the recorded SHA-256.
- Verify the manifest's `minPaiVersion` is satisfied.
- Verify the plugin supports the current platform.
- Verify the required permissions are declared in the manifest.
- Prompt the user for permission grant if any are `dangerous` on this OS.

### 13.5 Sandboxing per platform

| Platform | Sandbox |
|---|---|
| Android | App UID; SELinux; scoped storage; runtime permissions |
| Linux | User UID; optional `bubblewrap`/`landlock` wrapper (future) |
| Windows | AppContainer (future); UAC for privileged operations |
| macOS | Hardened runtime + entitlements + code signing |
| iOS | No native plugin loading; Dart-only plugins |

### 13.6 Plugin revocation

The registry index may include:

```json
"revoked": ["camera@0.9.0", "whatsapp@1.0.0"]
```

When the host sees a revoked `(id, version)`:

- If installed but not enabled: mark disabled, show a warning.
- If enabled: disable immediately, show a warning.
- If currently downloading: abort.

---

## 14. Writing a plugin

### 14.1 Minimum viable plugin

Create a directory `pai-plugin-hello/`:

```
pai-plugin-hello/
├── manifest.json
├── CMakeLists.txt
├── include/pai_plugin_abi.h
└── src/hello.cpp
```

**manifest.json**

```json
{
  "schemaVersion": 1,
  "id": "hello",
  "name": "Hello",
  "version": "1.0.0",
  "description": "Says hello.",
  "runtime": { "kind": "native", "nativeModule": "pai.hello", "minPaiVersion": "0.4.0" },
  "platforms": {
    "linux": { "artifact": "build/libpai_hello.so", "entrypoint": "pai_plugin_module_create" },
    "android": { "artifact": "build/arm64-v8a/libpai_hello.so", "entrypoint": "pai_plugin_module_create" }
  },
  "permissions": [],
  "capabilities": [
    {
      "id": "hello.greet",
      "description": "Greet a person by name.",
      "parameters": { "type": "object", "properties": { "name": { "type": "string" } } }
    }
  ],
  "integrity": { "sha256": "", "signature": "", "signerKeyId": "local-dev" }
}
```

**src/hello.cpp**

```cpp
#include "pai_plugin_abi.h"
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace {
std::vector<const char*> g_caps = { "hello.greet" };
std::vector<const char*> g_perms = {};

const char* Name() { return "pai.hello"; }
const char** Caps(size_t* n) { *n = g_caps.size(); return g_caps.data(); }
const char** Perms(size_t* n) { *n = g_perms.size(); return g_perms.data(); }

char* Dup(const std::string& s) {
  char* p = (char*)std::malloc(s.size() + 1);
  std::memcpy(p, s.c_str(), s.size() + 1);
  return p;
}

void FreeKvList(pai_kv_list_t* l) {
  if (!l || !l->items) return;
  for (size_t i = 0; i < l->count; ++i) {
    std::free((void*)l->items[i].key);
    std::free((void*)l->items[i].value);
  }
  std::free((void*)l->items);
  l->items = nullptr; l->count = 0;
}

void FreeStr(char* s) { std::free(s); }

int Invoke(const char* cap, const pai_kv_list_t* params,
           pai_kv_list_t* out, char** err) {
  if (std::strcmp(cap, "hello.greet") != 0) {
    if (err) *err = Dup("unknown_capability");
    return 1;
  }

  std::string name = "world";
  for (size_t i = 0; i < params->count; ++i) {
    if (std::strcmp(params->items[i].key, "name") == 0) {
      name = params->items[i].value;
    }
  }

  std::string greeting = "hello, " + name;
  auto* items = (pai_kv_t*)std::malloc(sizeof(pai_kv_t) * 2);
  items[0] = { Dup("ok"), Dup("true") };
  items[1] = { Dup("greeting"), Dup(greeting) };
  out->items = items;
  out->count = 2;
  return 0;
}

int ReqPerms(const char**, size_t, pai_kv_list_t* out, char**) {
  out->items = nullptr; out->count = 0; return 0;
}

pai_plugin_module_t g_module = {
  PAI_PLUGIN_ABI_VERSION,
  &Name, &Caps, &Perms, &Invoke, &ReqPerms, &FreeKvList, &FreeStr,
};
}

extern "C" pai_plugin_module_t* pai_plugin_module_create() { return &g_module; }
extern "C" void pai_plugin_module_destroy(pai_plugin_module_t*) {}
```

**CMakeLists.txt**

```cmake
cmake_minimum_required(VERSION 3.18)
project(pai_hello CXX)
add_library(pai_hello SHARED src/hello.cpp)
target_include_directories(pai_hello PRIVATE include)
target_compile_features(pai_hello PRIVATE cxx_std_17)
set_target_properties(pai_hello PROPERTIES
  CXX_VISIBILITY_PRESET hidden
  VISIBILITY_INLINES_HIDDEN ON
  PREFIX "libpai_"
  OUTPUT_NAME "pai_hello")
```

### 14.2 Build & publish

```bash
# 1. Build for each platform
cmake -S . -B build/linux && cmake --build build/linux
# Android: use the NDK toolchain for each ABI

# 2. Compute SHA-256 for each artifact
sha256sum build/linux/libpai_hello.so

# 3. Sign the canonical manifest (Ed25519) with your private key
pai-cli sign manifest.json > manifest.signed.json

# 4. Upload to the registry under /plugins/hello/1.0.0/
```

### 14.3 Signing

The signing key is an Ed25519 key pair. The public half must be in the host's trust store.

```bash
pai-cli keygen --id my-labs-2025 --out my-labs-2025.key
pai-cli sign --key my-labs-2025.key manifest.json
```

For local development, the host can be configured to trust a `local-dev` key id.

### 14.4 Testing locally

Point the app at a local registry:

```dart
// lib/config/app_config.dart
static final pluginRegistry = Uri.parse(
  Platform.isAndroid ? 'http://10.0.2.2:8080/' : 'http://127.0.0.1:8080/',
);
```

Serve the files:

```bash
mkdir -p /tmp/pai-registry/plugins/hello/1.0.0
cp manifest.signed.json /tmp/pai-registry/plugins/hello/1.0.0/manifest.json
cp build/linux/libpai_hello.so /tmp/pai-registry/plugins/hello/1.0.0/
# generate index.json
python3 -m http.server 8080 --directory /tmp/pai-registry
```

---

## 15. Platform specifics

### 15.1 Linux

| Concern | Detail |
|---|---|
| Loader | `dlopen(path, RTLD_NOW | RTLD_LOCAL)` |
| Artifact ext | `.so` |
| Store path | `~/.local/share/pai/plugins/` |
| Permissions | Device node access (e.g. `/dev/video0`), polkit for privileged ops |
| Multi-arch | One `.so` per architecture (`x86_64`, `arm64`) |
| Sandbox | User UID only (future: Landlock + seccomp) |

### 15.2 Android

| Concern | Detail |
|---|---|
| Loader | JNI + `dlopen` inside host JNI lib `libpai_agent_host.so` |
| Artifact ext | `.so` per ABI |
| ABIs | `arm64-v8a`, `armeabi-v7a`, `x86_64` |
| Store path | `context.filesDir/plugins/` |
| Permissions | Runtime permissions (`CAMERA`, `RECORD_AUDIO`, etc.) |
| AndroidManifest | All potential permissions must be declared (merged manifest) |
| `extractNativeLibs` | Must be `true` so `dlopen` can read the `.so` |
| JNI symbol naming | `Java_<pkg>_<Class>_<method>` — must match package exactly |
| Verified boot | Artifacts must be readable by app UID only |

### 15.3 Windows

| Concern | Detail |
|---|---|
| Loader | `LoadLibraryW` + `GetProcAddress` |
| Artifact ext | `.dll` |
| Store path | `%APPDATA%\PAI\plugins\` |
| Multi-arch | One `.dll` per architecture |
| Signing | Authenticode recommended; not enforced at load |

### 15.4 macOS

| Concern | Detail |
|---|---|
| Loader | `dlopen` + `dlsym` |
| Artifact ext | `.dylib` or `.framework` |
| Store path | `~/Library/Application Support/PAI/plugins/` |
| Signing | Hardened runtime requires `com.apple.security.cs.disable-library-validation` |
| Gatekeeper | Plugins must be notarized for distribution outside the App Store |

### 15.5 iOS

**Native plugins are not supported on iOS.** Apple's sandbox forbids loading unsigned executable code at runtime. iOS builds may use:

- **Dart plugins** (`runtime.kind == "dart"`) loaded via `Isolate.spawnUri`
- **Server-side execution**: capabilities are routed to another device

---

## 16. Failure modes and recovery

| Failure | Detection | Recovery |
|---|---|---|
| Network error during `install()` | HTTP status / exception | Retry with backoff; partial file deleted |
| SHA-256 mismatch | Hash comparison | Delete artifact; report tampering |
| Signature invalid | Ed25519 verify | Refuse install; report |
| Manifest schema mismatch | `schemaVersion` check | Refuse; prompt app update |
| ABI mismatch on enable | `abi_version != 1` | Refuse load; mark `unavailable` |
| Native load fails | `dlopen` returns null | Mark `unavailable`; show diagnostics |
| Plugin crashes during `invoke` | Process crash | Module auto-disabled after N crashes |
| Plugin hangs during `invoke` | Timeout | Return `timeout`; disable after N timeouts |
| Disk full during install | `IOException` | Partial install cleaned up |
| `installed.json` corrupt | JSON parse error | Rebuild from disk scan |
| Capability missing at invoke | Registry lookup returns null | Return `capability_not_available` |

**Automatic disable policy**

A plugin is automatically disabled if:

- It fails to load N consecutive times (default N = 3)
- It crashes the process N times in M minutes
- It exceeds the timeout on N consecutive invocations
- It is listed as revoked by the registry

Auto-disable is recorded in `installed.json` with a reason field.

---

## 17. Versioning and compatibility

### 17.1 Version axes

| Component | Scheme | Notes |
|---|---|---|
| Host app | SemVer | e.g. `0.4.0` |
| Plugin | SemVer | e.g. `1.0.0` |
| Manifest schema | Integer | Currently `1` |
| C ABI | Integer | Currently `1` |
| MethodChannel protocol | Implicit with host | Additive changes only |

### 17.2 Compatibility rules

| Change | Requires |
|---|---|
| New plugin (no host change) | Nothing |
| New plugin with a new capability | Nothing |
| New plugin with a new permission | Host trust store entry + OS permission |
| Manifest schema bump | Host update |
| C ABI bump | Host update; plugins recompiled |
| MethodChannel method added | Host update; old hosts ignore unknown methods |
| MethodChannel method changed | Host + Dart update |

### 17.3 `minPaiVersion`

Enforced at install and at enable:

```
if (hostVersion < manifest.runtime.minPaiVersion) → reject
```

---

## 18. Testing

### 18.1 Unit tests

| Test | Coverage |
|---|---|
| `PluginRepository` | HTTP client behavior, error paths |
| `PluginInstaller` | Disk layout, integrity, atomic writes |
| `PluginRegistry` | Capability resolution |
| `PluginManager` | State transitions |
| Native `PluginManager` | Dispatch, missing module, unsupported capability |
| Native `DynamicLoader` | Symbol resolution, ABI mismatch |

### 18.2 Integration tests

| Scenario | Setup |
|---|---|
| Install from local registry | `python3 -m http.server` serving a fixture |
| Enable + invoke | Test plugin `hello.greet` |
| Disable + re-enable | Verify capability disappears and reappears |
| Uninstall | Verify files removed and capability gone |
| Tampered artifact | Modify artifact bytes; verify install fails |
| Bad signature | Sign with a key not in trust store; verify rejection |
| ABI mismatch | Plugin compiled with `abi_version = 99`; verify rejection |

### 18.3 Fixture registry

```
test/registry/
├── index.json
└── plugins/
    └── hello/
        └── 1.0.0/
            ├── manifest.json
            └── libpai_hello.so
```

### 18.4 CI

- Linux + Android CI runs the fixture registry on `localhost`.
- Android runs on an emulator with `10.0.2.2` pointing at the host.
- Each PR that touches `lib/plugins/`, `linux/runner/agent/`, or `android/app/src/main/kotlin/com/pai/agent/` runs the full integration suite.

---

## Appendix A — File reference

### Dart (`lib/plugins/`)

| File | Purpose |
|---|---|
| `models/plugin_capability.dart` | Capability descriptor |
| `models/plugin_info.dart` | Manifest model |
| `models/plugin_info_short.dart` | UI descriptor |
| `runtime/plugin_runtime.dart` | MethodChannel façade |
| `runtime/dart_plugin_loader.dart` | Pure-Dart plugin loader |
| `plugin_repository.dart` | HTTP client |
| `plugin_installer.dart` | Disk + integrity |
| `plugin_registry.dart` | In-memory index |
| `plugin_manager.dart` | Lifecycle |
| `plugin_loader.dart` | Startup bootstrap |
| `plugin_command_router.dart` | Capability dispatch |
| `plugin_service.dart` | Public API |
| `plugin_factory.dart` | Manifest → `DevicePlugin` |
| `platform/platform_plugin_registration.dart` | Native load bridge |
| `device_plugin.dart` | In-memory instance |
| `plugin_command_handler.dart` | Interface for Dart plugins |

### Native (`linux/runner/agent/`)

| File | Purpose |
|---|---|
| `abi/pai_plugin_abi.h` | C ABI header |
| `bridge/plugin_channel.{h,cc}` | MethodChannel handler |
| `plugins/plugin_module.h` | Abstract module interface |
| `plugins/native_module_registry.{h,cc}` | Module registry |
| `plugins/plugin_manager.{h,cc}` | Dispatch |
| `plugins/dynamic_loader.{h,cc}` | `dlopen` wrapper |
| `permissions/permission_manager.{h,cc}` | Permission prompts |

### Native (`android/app/src/main/`)

| File | Purpose |
|---|---|
| `cpp/pai_plugin_abi.h` | C ABI header |
| `cpp/native_loader.cpp` | JNI + `dlopen` |
| `cpp/CMakeLists.txt` | Build `libpai_agent_host.so` |
| `kotlin/com/pai/agent/MainActivity.kt` | Registers `PluginChannel` |
| `kotlin/com/pai/agent/bridge/PluginChannel.kt` | MethodChannel handler |
| `kotlin/com/pai/agent/plugins/PluginModule.kt` | Interface |
| `kotlin/com/pai/agent/plugins/NativeModuleRegistry.kt` | Registry |
| `kotlin/com/pai/agent/plugins/PluginManager.kt` | Dispatch |
| `kotlin/com/pai/agent/plugins/DynamicLoader.kt` | JNI façade |
| `kotlin/com/pai/agent/plugins/CAbiPluginModule.kt` | Kotlin wrapper |
| `kotlin/com/pai/agent/permissions/PermissionManager.kt` | Permission prompts |

---

## Appendix B — Method channel protocol

Channel name: `pai/plugin_runtime`
Codec: `StandardMethodCodec`

### B.1 `listModules`

**Args:** none
**Returns:** `List<String>` — registered native module names

### B.2 `isModuleAvailable`

**Args:** `{ "nativeModule": String }`
**Returns:** `bool`

### B.3 `loadNativeModule`

**Args:**
```json
{
  "nativeModule": "pai.camera",
  "artifactPath": "/abs/path/libpai_camera.so",
  "entrypoint":   "pai_plugin_module_create",
  "capabilities": ["camera.capture", "camera.record"],
  "permissions":  ["camera", "microphone", "filesystem"]
}
```
**Returns:** `bool`

### B.4 `unloadNativeModule`

**Args:** `{ "nativeModule": String }`
**Returns:** `null`

### B.5 `invoke`

**Args:**
```json
{
  "nativeModule": "pai.camera",
  "capability":   "camera.capture",
  "parameters":   { "lens": "back" }
}
```
**Returns:** `Map<String, dynamic>`

### B.6 `requestPermissions`

**Args:**
```json
{
  "nativeModule": "pai.camera",
  "permissions":  ["camera", "microphone"]
}
```
**Returns:** `Map<String, dynamic>` — `{ "ok": true, "granted": ["camera"] }`

---

## Appendix C — Registry API

### C.1 `GET /plugins/index.json`

Returns the plugin catalog. See §6.1.

### C.2 `GET /plugins/<id>/<version>/manifest.json`

Returns the signed manifest. See §5.2.

### C.3 `GET /plugins/<id>/<version>/<artifact>`

Returns the binary. Content-Type: `application/octet-stream`. Must be byte-identical to what was signed.

### C.4 Optional endpoints

| Endpoint | Purpose |
|---|---|
| `GET /plugins/revoked.json` | List of revoked `(id, version)` pairs |
| `GET /plugins/<id>/versions.json` | Version history |
| `POST /plugins/publish` | Publish (registry-operator only) |

### C.5 Response headers

| Header | Value |
|---|---|
| `Content-Type` | `application/json` or `application/octet-stream` |
| `Cache-Control` | `public, max-age=31536000, immutable` for versioned content |
| `ETag` | SHA-256 of the body |
| `X-Pai-Signature` | Ed25519 signature of the response body (optional, defense in depth) |

---

**End of document.**
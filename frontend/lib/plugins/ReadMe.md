## **Architecture**

**PluginManager**

Catalog + UI state

- Loads catalog from backend
- Tracks UI installation state
- Handles remote device operations
- Manages capability configuration

**PluginService**

Local lifecycle

- Installs and uninstalls plugins
- Enables and disables plugins
- Loads manifests and capabilities
- Updates the local registry

**PluginRegistry**

Installed runtime state

- Holds `DevicePlugin` instances
- Tracks enabled plugins
- Resolves capability IDs

**CapabilityDispatcher**

Execution routing

- Resolves capability to plugin
- Resolves native artifact
- Loads native module
- Invokes capability

**PluginRuntime**

Platform execution

- Native module loading
- Native capability invocation
- Platform-specific communication


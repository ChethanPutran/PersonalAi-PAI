
enum PluginState {
  available,
  installing,
  installed,
  enabled,
  disabled,
  unavailable,
  error,
}

PluginState pluginStateFromString(String? value) {
  return PluginState.values.firstWhere(
    (state) => state.name == value,
    orElse: () => PluginState.available,
  );
}

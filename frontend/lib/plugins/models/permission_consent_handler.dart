typedef PermissionConsentHandler = Future<bool> Function({
  required String pluginName,
  required String nativeModule,
  required List<String> permissions,
});

import '../../device_plugin.dart';
import '../../models/plugin_info.dart';

class BrowserPlugin
    implements DevicePlugin {
  @override
  final PluginInfo info;

  BrowserPlugin(this.info);

  bool _installed = false;
  bool _enabled = false;

  @override
  Future<void> install() async {
    /*
     * Actual Linux installation logic goes here.
     *
     * For example:
     *
     * - download package_url
     * - verify checksum
     * - extract package
     * - initialize runtime
     */
    _installed = true;
  }

  @override
  Future<void> enable() async {
    if (!_installed) {
      throw StateError(
        'Browser plugin is not installed',
      );
    }

    _enabled = true;
  }

  @override
  Future<void> disable() async {
    _enabled = false;
  }

  @override
  Future<dynamic> execute(
    String action,
    Map<String, dynamic> params,
  ) async {
    if (!_installed) {
      throw StateError(
        'Browser plugin is not installed',
      );
    }

    if (!_enabled) {
      throw StateError(
        'Browser plugin is not enabled',
      );
    }

    switch (action) {
      case 'browser.open':
        return await _open(
          params,
        );

      case 'browser.navigate':
        return await _navigate(
          params,
        );

      case 'browser.click':
        return await _click(
          params,
        );

      case 'browser.type':
        return await _type(
          params,
        );

      default:
        throw UnsupportedError(
          'Unsupported browser action: $action',
        );
    }
  }

  Future<Map<String, dynamic>> _open(
    Map<String, dynamic> params,
  ) async {
    return {
      'success': true,
      'action': 'browser.open',
    };
  }

  Future<Map<String, dynamic>> _navigate(
    Map<String, dynamic> params,
  ) async {
    final url =
        params['url']?.toString();

    if (url == null) {
      throw ArgumentError(
        'browser.navigate requires url',
      );
    }

    return {
      'success': true,
      'url': url,
    };
  }

  Future<Map<String, dynamic>> _click(
    Map<String, dynamic> params,
  ) async {
    return {
      'success': true,
      'action': 'browser.click',
    };
  }

  Future<Map<String, dynamic>> _type(
    Map<String, dynamic> params,
  ) async {
    return {
      'success': true,
      'action': 'browser.type',
    };
  }
}
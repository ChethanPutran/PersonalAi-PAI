/// Human wording for the permissions a plugin asks for, shown in the consent dialog.
///
/// The raw permission id ("input") means nothing to most people, and some permissions are far more
/// powerful than others; the dialog should say what is being handed over.
class PermissionLabel {
  final String title;
  final String detail;

  /// True for permissions that let a plugin act on the user's behalf or see private screens: the
  /// dialog marks these so they are not skimmed like "network".
  final bool sensitive;

  const PermissionLabel(this.title, this.detail, {this.sensitive = false});
}

const Map<String, PermissionLabel> _labels = {
  'input': PermissionLabel(
    'Control your keyboard and mouse',
    'Type and click for you, as if it were you. Your desktop will also ask you to approve, and you '
        'can stop it at any time from the desktop\'s remote-control indicator.',
    sensitive: true,
  ),
  'screen': PermissionLabel(
    'See your screen',
    'Take screenshots of your desktop, which can show anything that is open.',
    sensitive: true,
  ),
  'shell': PermissionLabel(
    'Run commands',
    'Run commands on this computer.',
    sensitive: true,
  ),
  'process': PermissionLabel('Start programs', 'Start and manage programs on this computer.', sensitive: true),
  'camera': PermissionLabel('Use the camera', 'Take photos and record video.', sensitive: true),
  'microphone': PermissionLabel('Use the microphone', 'Record audio.', sensitive: true),
  'clipboard': PermissionLabel('Use the clipboard', 'Read and change what you copied.', sensitive: true),
  'filesystem': PermissionLabel('Access your files', 'Read and write files on this computer.', sensitive: true),
  'network': PermissionLabel('Use the network', 'Make network requests.'),
  'notifications': PermissionLabel('Show notifications', 'Show and manage desktop notifications.'),
};

/// The label for [permission]; an unknown id falls back to the id itself (never hides a request).
PermissionLabel describePermission(String permission) =>
    _labels[permission.trim().toLowerCase()] ?? PermissionLabel(permission, '');

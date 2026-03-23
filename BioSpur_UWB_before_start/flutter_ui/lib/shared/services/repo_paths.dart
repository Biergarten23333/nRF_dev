import 'dart:io';

class RepoPaths {
  static Directory? _cachedRoot;

  static Directory get root => _cachedRoot ??= _discoverRoot();

  static Directory _discoverRoot() {
    var dir = Directory.current;
    for (var i = 0; i < 12; i++) {
      if (Directory('${dir.path}/docs').existsSync() &&
          Directory('${dir.path}/logs').existsSync()) {
        return dir;
      }
      final parent = dir.parent;
      if (parent.path == dir.path) {
        break;
      }
      dir = parent;
    }
    return Directory.current;
  }

  static Directory get docsDir => Directory('${root.path}/docs');
  static Directory get logsDir => Directory('${root.path}/logs');
  static Directory get tagSessionsDir => Directory('${logsDir.path}/tag_sessions');
  static Directory get masterBleSessionsDir =>
      Directory('${logsDir.path}/master_ble_sessions');
  static Directory get dataDir => Directory('${root.path}/data');
  static Directory get flutterUiDir => Directory('${root.path}/flutter_ui');
}

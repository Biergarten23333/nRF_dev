import 'dart:io';

enum DevicePortRole {
  masterAnchor,
  masterTag,
  listener,
  unknown,
}

extension DevicePortRoleLabel on DevicePortRole {
  String get label {
    switch (this) {
      case DevicePortRole.masterAnchor:
        return 'Master_Anchor';
      case DevicePortRole.masterTag:
        return 'Master_Tag';
      case DevicePortRole.listener:
        return 'Listener';
      case DevicePortRole.unknown:
        return 'Unknown';
    }
  }
}

class DevicePortInfo {
  final String path;
  final String targetPath;
  final String label;
  final DevicePortRole role;
  final int confidence;

  const DevicePortInfo({
    required this.path,
    required this.targetPath,
    required this.label,
    required this.role,
    required this.confidence,
  });

  bool get isVerifiedCandidate => confidence >= 90;
}

class DevicePortSnapshot {
  final List<DevicePortInfo> ports;
  final DevicePortInfo? masterAnchor;
  final DevicePortInfo? masterTag;
  final DevicePortInfo? listener;

  const DevicePortSnapshot({
    required this.ports,
    required this.masterAnchor,
    required this.masterTag,
    required this.listener,
  });
}

class DevicePortDetector {
  DevicePortDetector._();

  static final DevicePortDetector instance = DevicePortDetector._();

  Future<DevicePortSnapshot> scan() async {
    final ports = <DevicePortInfo>[];
    final seenTargets = <String>{};
    final byIdDir = Directory('/dev/serial/by-id');

    if (byIdDir.existsSync()) {
      final entities = byIdDir.listSync().whereType<Link>().toList()
        ..sort((a, b) => a.path.compareTo(b.path));
      for (final entity in entities) {
        final idName = entity.path.split('/').last;
        final target = _resolveDevPath(entity.targetSync());
        seenTargets.add(target);
        ports.add(
          DevicePortInfo(
            path: entity.path,
            targetPath: target,
            label: idName,
            role: _classify(idName),
            confidence: _confidence(idName),
          ),
        );
      }
    }

    final devDir = Directory('/dev');
    if (devDir.existsSync()) {
      final entities = devDir.listSync().toList()
        ..sort((a, b) => a.path.compareTo(b.path));
      for (final entity in entities) {
        final p = entity.path;
        if (!p.contains(RegExp(r'tty(ACM|USB)[0-9]+$'))) continue;
        if (seenTargets.contains(p)) continue;
        ports.add(
          DevicePortInfo(
            path: p,
            targetPath: p,
            label: p.split('/').last,
            role: DevicePortRole.unknown,
            confidence: 20,
          ),
        );
      }
    }

    return DevicePortSnapshot(
      ports: ports,
      masterAnchor: _bestFor(ports, DevicePortRole.masterAnchor),
      masterTag: _bestFor(ports, DevicePortRole.masterTag),
      listener: _bestFor(ports, DevicePortRole.listener),
    );
  }

  String _resolveDevPath(String target) {
    if (target.startsWith('/dev/')) return target;
    if (target.startsWith('../')) return '/dev/${target.substring(3)}';
    return '/dev/$target';
  }

  DevicePortInfo? _bestFor(List<DevicePortInfo> ports, DevicePortRole role) {
    final matches = ports.where((p) => p.role == role).toList()
      ..sort((a, b) => b.confidence.compareTo(a.confidence));
    return matches.isEmpty ? null : matches.first;
  }

  DevicePortRole _classify(String label) {
    final lower = label.toLowerCase();
    if (lower.contains('master_anchor')) return DevicePortRole.masterAnchor;
    if (lower.contains('master-tag') || lower.contains('master_tag')) {
      return DevicePortRole.masterTag;
    }
    if (lower.contains('j-link') ||
        lower.contains('jlink') ||
        lower.contains('listener')) {
      return DevicePortRole.listener;
    }
    return DevicePortRole.unknown;
  }

  int _confidence(String label) {
    final lower = label.toLowerCase();
    if (lower.contains('master_anchor') || lower.contains('master_tag')) {
      return 100;
    }
    if (lower.contains('j-link') ||
        lower.contains('jlink') ||
        lower.contains('listener')) {
      return 70;
    }
    return 35;
  }
}

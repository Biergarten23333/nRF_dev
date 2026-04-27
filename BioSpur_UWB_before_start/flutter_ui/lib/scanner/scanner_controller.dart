import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

import '../shared/services/repo_paths.dart';

enum ScannedKind { anchor, tag, unknown }

class ScannedDevice {
  final String key;
  final ScannedKind kind;
  final String displayName;
  final String address;
  final String uuid;
  final String extra;
  final int rssi;
  final bool online;
  final DateTime lastSeenAt;

  const ScannedDevice({
    required this.key,
    required this.kind,
    required this.displayName,
    required this.address,
    required this.uuid,
    required this.extra,
    required this.rssi,
    required this.online,
    required this.lastSeenAt,
  });

  ScannedDevice copyWith({
    ScannedKind? kind,
    String? displayName,
    String? address,
    String? uuid,
    String? extra,
    int? rssi,
    bool? online,
    DateTime? lastSeenAt,
  }) {
    return ScannedDevice(
      key: key,
      kind: kind ?? this.kind,
      displayName: displayName ?? this.displayName,
      address: address ?? this.address,
      uuid: uuid ?? this.uuid,
      extra: extra ?? this.extra,
      rssi: rssi ?? this.rssi,
      online: online ?? this.online,
      lastSeenAt: lastSeenAt ?? this.lastSeenAt,
    );
  }
}

class SerialPortInfo {
  final String path;
  final String label;

  const SerialPortInfo({required this.path, required this.label});
}

class ScannerController extends ChangeNotifier {
  static const Duration _staleWindow = Duration(seconds: 4);

  final Map<String, ScannedDevice> _devices = {};
  final List<String> _statusTrail = <String>[];

  List<SerialPortInfo> _ports = const [];
  String? _selectedPort;
  Process? _process;
  StreamSubscription<String>? _stdoutSub;
  StreamSubscription<String>? _stderrSub;
  Timer? _tick;

  bool _refreshing = false;
  String _status = 'Idle';

  List<SerialPortInfo> get ports => _ports;
  String? get selectedPort => _selectedPort;
  bool get connected => _process != null;
  String get status => _status;
  List<String> get statusTrail => List.unmodifiable(_statusTrail);

  List<ScannedDevice> get anchors {
    final list = _devices.values.where((d) => d.kind == ScannedKind.anchor).toList();
    list.sort(_deviceSort);
    return list;
  }

  List<ScannedDevice> get tags {
    final list = _devices.values.where((d) => d.kind == ScannedKind.tag).toList();
    list.sort(_deviceSort);
    return list;
  }

  ScannerController() {
    scanPorts();
    _tick = Timer.periodic(const Duration(seconds: 1), (_) {
      _refreshOnlineFlags();
    });
  }

  int _deviceSort(ScannedDevice a, ScannedDevice b) {
    final onlineCmp = (a.online == b.online) ? 0 : (a.online ? -1 : 1);
    if (onlineCmp != 0) return onlineCmp;
    final rssiCmp = b.rssi.compareTo(a.rssi);
    if (rssiCmp != 0) return rssiCmp;
    return a.displayName.compareTo(b.displayName);
  }

  void _pushStatus(String status) {
    _status = status;
    _statusTrail.add('[${DateTime.now().toIso8601String()}] $status');
    if (_statusTrail.length > 20) {
      _statusTrail.removeRange(0, _statusTrail.length - 20);
    }
    notifyListeners();
  }

  Future<void> scanPorts() async {
    if (_refreshing) return;
    _refreshing = true;
    try {
      final byIdDir = Directory('/dev/serial/by-id');
      final prettyNames = <String, String>{};
      if (byIdDir.existsSync()) {
        for (final entity in byIdDir.listSync()) {
          if (entity is! Link) continue;
          final idName = entity.path.split('/').last;
          final target = entity.targetSync();
          late final String devPath;
          if (target.startsWith('../')) {
            devPath = '/dev/${target.substring(3)}';
          } else if (target.startsWith('/dev/')) {
            devPath = target;
          } else {
            devPath = '/dev/$target';
          }
          prettyNames[devPath] = idName;
        }
      }

      final discovered = <SerialPortInfo>[];
      final devDir = Directory('/dev');
      if (devDir.existsSync()) {
        for (final entity in devDir.listSync()) {
          final path = entity.path;
          if (path.contains('ttyUSB') || path.contains('ttyACM') || path.contains('ttyS')) {
            final symlink = prettyNames[path];
            final label = symlink == null ? path : '$path  [$symlink]';
            discovered.add(SerialPortInfo(path: path, label: label));
          }
        }
      }

      discovered.sort((a, b) => a.label.compareTo(b.label));
      _ports = discovered;
      _selectedPort ??= discovered.isNotEmpty ? discovered.first.path : null;
      if (_selectedPort != null && !discovered.any((e) => e.path == _selectedPort)) {
        _selectedPort = discovered.isNotEmpty ? discovered.first.path : null;
      }
      _pushStatus(discovered.isEmpty ? 'No serial ports found' : 'Found ${discovered.length} serial ports');
    } finally {
      _refreshing = false;
      notifyListeners();
    }
  }

  void selectPort(String? port) {
    _selectedPort = port;
    notifyListeners();
  }

  Future<void> connect() async {
    if (connected) return;
    final port = _selectedPort;
    if (port == null || port.isEmpty) {
      _pushStatus('No port selected');
      return;
    }

    final bridge = '${RepoPaths.root.path}/52840_dongle_scanner/backend/dongle_scan_bridge.py';
    _pushStatus('Connecting to $port');

    try {
      final process = await Process.start(
        'python3',
        [bridge, '--port', port],
        workingDirectory: RepoPaths.root.path,
        runInShell: false,
      );
      _process = process;

      _stdoutSub = process.stdout
          .transform(utf8.decoder)
          .transform(const LineSplitter())
          .listen(_handleLine);
      _stderrSub = process.stderr
          .transform(utf8.decoder)
          .transform(const LineSplitter())
          .listen((line) {
        final text = line.trim();
        if (text.isNotEmpty) {
          _pushStatus('bridge: $text');
        }
      });

      unawaited(process.exitCode.then((code) {
        _pushStatus('Scanner exited with code $code');
        _process = null;
        _stdoutSub = null;
        _stderrSub = null;
        notifyListeners();
      }));
    } catch (err) {
      _process = null;
      _stdoutSub = null;
      _stderrSub = null;
      _pushStatus('Failed to start scanner: $err');
    }
    notifyListeners();
  }

  Future<void> disconnect() async {
    final process = _process;
    if (process == null) return;
    _pushStatus('Disconnecting scanner');
    try {
      await _stdoutSub?.cancel();
      await _stderrSub?.cancel();
      process.kill(ProcessSignal.sigterm);
      await Future<void>.delayed(const Duration(milliseconds: 200));
      if (_process != null) {
        process.kill(ProcessSignal.sigkill);
      }
    } finally {
      _stdoutSub = null;
      _stderrSub = null;
      _process = null;
      notifyListeners();
    }
  }

  void _handleLine(String line) {
    if (!line.startsWith('{')) {
      return;
    }
    Map<String, dynamic> decoded;
    try {
      decoded = jsonDecode(line) as Map<String, dynamic>;
    } catch (_) {
      return;
    }

    final type = decoded['type']?.toString();
    switch (type) {
      case 'ready':
        _pushStatus('Scanner ready (${decoded['product'] ?? 'unknown'})');
        break;
      case 'status':
        final state = decoded['state']?.toString() ?? 'unknown';
        final mode = decoded['mode']?.toString();
        _pushStatus(mode == null ? 'Scanner status: $state' : 'Scanner status: $state / $mode');
        break;
      case 'peer':
        _upsertPeer(decoded);
        break;
      case 'error':
        _pushStatus('Scanner error: ${decoded['message'] ?? 'unknown'}');
        break;
      default:
        break;
    }
  }

  void _upsertPeer(Map<String, dynamic> decoded) {
    final kind = decoded['kind']?.toString() == 'anchor'
        ? ScannedKind.anchor
        : decoded['kind']?.toString() == 'tag'
            ? ScannedKind.tag
            : ScannedKind.unknown;
    if (kind == ScannedKind.unknown) return;

    final key = decoded['uuid']?.toString().isNotEmpty == true
        ? decoded['uuid'].toString()
        : decoded['address']?.toString() ?? decoded['display_name']?.toString() ?? 'unknown';
    final now = DateTime.now();
    final device = ScannedDevice(
      key: key,
      kind: kind,
      displayName: decoded['display_name']?.toString() ?? key,
      address: decoded['address']?.toString() ?? '-',
      uuid: decoded['uuid']?.toString() ?? '-',
      extra: kind == ScannedKind.anchor
          ? 'ANCHOR ${decoded['anchor_id_cfg'] ?? '?'} role=${decoded['role_code'] ?? '?'}'
          : 'TAG ${decoded['tag_id'] ?? '?'} id=${decoded['identity_code'] ?? '?'}',
      rssi: (decoded['rssi'] is num) ? (decoded['rssi'] as num).toInt() : int.tryParse('${decoded['rssi']}') ?? -127,
      online: decoded['online'] == true,
      lastSeenAt: now,
    );

    _devices[key] = device;
    _refreshOnlineFlags();
    notifyListeners();
  }

  void _refreshOnlineFlags() {
    final now = DateTime.now();
    var changed = false;
    _devices.updateAll((key, device) {
      final online = now.difference(device.lastSeenAt) <= _staleWindow;
      if (online != device.online) changed = true;
      return device.copyWith(online: online);
    });
    if (changed) {
      notifyListeners();
    }
  }

  @override
  void dispose() {
    _tick?.cancel();
    _stdoutSub?.cancel();
    _stderrSub?.cancel();
    _process?.kill(ProcessSignal.sigkill);
    super.dispose();
  }
}

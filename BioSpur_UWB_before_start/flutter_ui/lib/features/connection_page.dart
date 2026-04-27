import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';

import 'connection/backend_client.dart';
import 'connection/backend_message.dart';
import 'connection/ble_peer.dart';
import 'connection/flutter_connection_pannel.dart';

class ConnectionPage extends StatefulWidget {
  const ConnectionPage({super.key});

  @override
  State<ConnectionPage> createState() => _ConnectionPageState();
}

class _SerialPortInfo {
  final String path;
  final String label;

  _SerialPortInfo(this.path, this.label);
}

class _ConnectionPageState extends State<ConnectionPage> {
  final BackendClient _client = BackendClient();
  final TextEditingController _exeController = TextEditingController(
    text: 'BioSpur_Fusion/C_core/build/biospur_core',
  );

  List<_SerialPortInfo> _serialPorts = [];
  String? _selectedPort1;
  String? _selectedPort2;
  final List<String> _log = [];
  final Map<String, BlePeerInfo> _blePeers = {};
  String _connectionHint = 'Awaiting SCAN / CONN events...';
  StreamSubscription<BackendMessage>? _msgSub;
  Timer? _pollTimer;

  @override
  void initState() {
    super.initState();
    _scanSerialPorts();
    _pollTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _msgSub?.cancel();
    _pollTimer?.cancel();
    _client.dispose();
    _exeController.dispose();
    super.dispose();
  }

  String _extractDeviceName(String idName) {
    var name = idName;
    if (name.startsWith('usb-')) {
      name = name.substring(4);
    }
    final ifIndex = name.indexOf('-if');
    if (ifIndex != -1) {
      name = name.substring(0, ifIndex);
    }
    if (name.length > 40) {
      name = name.substring(0, 40);
    }
    return name;
  }

  Future<void> _scanSerialPorts() async {
    final ports = <_SerialPortInfo>[];
    final Map<String, String> prettyNames = {};
    final byIdDir = Directory('/dev/serial/by-id');

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
        prettyNames[devPath] = _extractDeviceName(idName);
      }
    }

    final devDir = Directory('/dev');
    if (devDir.existsSync()) {
      for (final entity in devDir.listSync()) {
        final p = entity.path;
        if (p.contains('ttyUSB') || p.contains('ttyACM') || p.contains('ttyS')) {
          final friendly = prettyNames[p];
          final label = friendly != null ? '$p - $friendly' : p;
          ports.add(_SerialPortInfo(p, label));
        }
      }
    }

    setState(() {
      _serialPorts = ports;
      _selectedPort1 ??= ports.isNotEmpty ? ports.first.path : null;
      _selectedPort2 ??= ports.length > 1 ? ports[1].path : null;
    });
  }

  void _upsertBlePeer({
    required String key,
    required String address,
    required String displayName,
    required BlePeerState state,
    required String lastLine,
  }) {
    _blePeers[key] =
        (_blePeers[key] ??
                BlePeerInfo(
                  key: key,
                  address: address,
                  displayName: displayName,
                  state: state,
                  lastLine: lastLine,
                  updatedAt: DateTime.now(),
                ))
            .copyWith(
              address: address,
              displayName: displayName,
              state: state,
              lastLine: lastLine,
              updatedAt: DateTime.now(),
            );
  }

  void _handleBackendLog(String line) {
    final scanHit = RegExp(r'SCAN hit:\s+([0-9A-F:]+).*?(?:bs=([A-Z0-9_]+))?').firstMatch(line);
    if (scanHit != null) {
      final address = scanHit.group(1) ?? 'unknown';
      final name = scanHit.group(2) ?? address;
      _upsertBlePeer(
        key: name,
        address: address,
        displayName: name,
        state: BlePeerState.discovered,
        lastLine: line,
      );
      _connectionHint = 'Discovered $name';
      return;
    }

    final connected =
        RegExp(r'Connected(?:\[\d+\])?:\s+([0-9A-F:]+).*?(?:bs=([A-Z0-9_]+))?').firstMatch(line);
    if (connected != null) {
      final address = connected.group(1) ?? 'unknown';
      final name = connected.group(2) ?? address;
      _upsertBlePeer(
        key: name,
        address: address,
        displayName: name,
        state: BlePeerState.connected,
        lastLine: line,
      );
      _connectionHint = 'Connected to $name';
      return;
    }

    final disconnected = RegExp(
      r'Disconnected(?:\[\d+\])?:\s+([0-9A-F:]+).*?(?:bs=([A-Z0-9_]+))?',
    ).firstMatch(line);
    if (disconnected != null) {
      final address = disconnected.group(1) ?? 'unknown';
      final name = disconnected.group(2) ?? address;
      _upsertBlePeer(
        key: name,
        address: address,
        displayName: name,
        state: BlePeerState.disconnected,
        lastLine: line,
      );
      _connectionHint = 'Disconnected $name';
      return;
    }

    if (line.contains('Scanning for BS*')) {
      _connectionHint = 'Scanning for BLE peers...';
      return;
    }

    if (line.contains('link ready')) {
      _connectionHint = 'BLE link ready';
    }
  }

  void _handleBackendMessage(BackendMessage msg) {
    switch (msg.kind) {
      case BackendMsgKind.raw:
      case BackendMsgKind.fuse:
      case BackendMsgKind.other:
        _log.add(msg.line);
        break;
      case BackendMsgKind.log:
        _handleBackendLog(msg.line);
        _log.add(msg.line);
        break;
    }
    if (_log.length > 1000) {
      _log.removeRange(0, 500);
    }
  }

  Future<void> _startBackend() async {
    if (_selectedPort1 == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('No serial port selected for Rx1')),
      );
      return;
    }

    await _msgSub?.cancel();
    _msgSub = _client.messages.listen((msg) {
      if (!mounted) return;
      setState(() {
        _handleBackendMessage(msg);
      });
    });

    await _client.start(
      exePath: _exeController.text,
      serial1: _selectedPort1!,
      serial2: _selectedPort2,
    );
  }

  Future<void> _stopBackend() async {
    await _client.stop();
    await _msgSub?.cancel();
    _msgSub = null;
    if (mounted) setState(() {});
  }

  Future<void> _send(String cmd) async {
    await _client.sendCommand(cmd);
  }

  @override
  Widget build(BuildContext context) {
    final dropdownItems = _serialPorts
        .map((e) => DropdownMenuItem<String>(value: e.path, child: Text(e.label)))
        .toList();

    final peers = _blePeers.values.toList();
    final sortedLog = _log.length <= 200 ? _log : _log.sublist(_log.length - 200);

    return Scaffold(
      appBar: AppBar(title: const Text('BioSpur - Connection')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          children: [
            FlutterConnectionPannel(
              backendController: _exeController,
              serialPortItems: dropdownItems,
              selectedPort1: _selectedPort1,
              selectedPort2: _selectedPort2,
              onPort1Changed: (v) => setState(() => _selectedPort1 = v),
              onPort2Changed: (v) => setState(() => _selectedPort2 = v),
              backendRunning: _client.isRunning,
              onStartBackend: _startBackend,
              onStopBackend: _stopBackend,
              onRescanPorts: _scanSerialPorts,
              onScan: () => _send('scan'),
              onConn: () => _send('conn'),
              onStartStream: () => _send('stream_on'),
              onStopStream: () => _send('stream_off'),
              onTsync: () => _send('TSYNC ${DateTime.now().millisecondsSinceEpoch}'),
              peers: peers,
              statusHint: _connectionHint,
            ),
            const SizedBox(height: 12),
            Expanded(
              child: Card(
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Backend Log',
                        style: Theme.of(context).textTheme.titleMedium?.copyWith(
                              fontWeight: FontWeight.bold,
                            ),
                      ),
                      const SizedBox(height: 8),
                      Expanded(
                        child: Container(
                          width: double.infinity,
                          decoration: BoxDecoration(
                            border: Border.all(color: Colors.grey.shade300),
                            borderRadius: BorderRadius.circular(8),
                          ),
                          child: sortedLog.isEmpty
                              ? const Center(child: Text('No backend logs yet'))
                              : ListView.builder(
                                  padding: const EdgeInsets.all(8),
                                  itemCount: sortedLog.length,
                                  itemBuilder: (context, index) {
                                    return Padding(
                                      padding: const EdgeInsets.only(bottom: 4),
                                      child: SelectableText(sortedLog[index]),
                                    );
                                  },
                                ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

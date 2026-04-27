import 'package:flutter/material.dart';

import 'ble_peer.dart';

class FlutterConnectionPannel extends StatelessWidget {
  final TextEditingController backendController;
  final List<DropdownMenuItem<String>> serialPortItems;
  final String? selectedPort1;
  final String? selectedPort2;
  final ValueChanged<String?> onPort1Changed;
  final ValueChanged<String?> onPort2Changed;
  final bool backendRunning;
  final VoidCallback onStartBackend;
  final VoidCallback onStopBackend;
  final VoidCallback onRescanPorts;
  final VoidCallback onScan;
  final VoidCallback onConn;
  final VoidCallback onStartStream;
  final VoidCallback onStopStream;
  final VoidCallback onTsync;
  final List<BlePeerInfo> peers;
  final String statusHint;

  const FlutterConnectionPannel({
    super.key,
    required this.backendController,
    required this.serialPortItems,
    required this.selectedPort1,
    required this.selectedPort2,
    required this.onPort1Changed,
    required this.onPort2Changed,
    required this.backendRunning,
    required this.onStartBackend,
    required this.onStopBackend,
    required this.onRescanPorts,
    required this.onScan,
    required this.onConn,
    required this.onStartStream,
    required this.onStopStream,
    required this.onTsync,
    required this.peers,
    required this.statusHint,
  });

  Color _stateColor(BlePeerState state, BuildContext context) {
    switch (state) {
      case BlePeerState.connected:
        return Colors.greenAccent;
      case BlePeerState.disconnected:
        return Colors.redAccent;
      case BlePeerState.discovered:
        return Theme.of(context).colorScheme.secondary;
    }
  }

  @override
  Widget build(BuildContext context) {
    final connected = peers.where((p) => p.isConnected).length;
    final discovered = peers.length;
    final sortedPeers = [...peers]..sort((a, b) => b.updatedAt.compareTo(a.updatedAt));

    return Card(
      elevation: 2,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.bluetooth_connected),
                const SizedBox(width: 8),
                Text(
                  'Flutter_Connection_Pannel',
                  style: Theme.of(context).textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.bold,
                      ),
                ),
                const SizedBox(width: 12),
                Chip(
                  label: Text(backendRunning ? 'backend running' : 'backend stopped'),
                  visualDensity: VisualDensity.compact,
                ),
                const SizedBox(width: 8),
                Chip(
                  label: Text('discovered: $discovered'),
                  visualDensity: VisualDensity.compact,
                ),
                const SizedBox(width: 8),
                Chip(
                  label: Text('connected: $connected'),
                  visualDensity: VisualDensity.compact,
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(statusHint, style: TextStyle(color: Colors.grey.shade700)),
            const SizedBox(height: 12),
            Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Expanded(
                  flex: 3,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('Backend Executable Path', style: TextStyle(fontWeight: FontWeight.bold)),
                      const SizedBox(height: 4),
                      TextField(
                        controller: backendController,
                        decoration: const InputDecoration(
                          border: OutlineInputBorder(),
                          isDense: true,
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  flex: 3,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('Serial Port 1', style: TextStyle(fontWeight: FontWeight.bold)),
                      const SizedBox(height: 4),
                      DropdownButtonFormField<String>(
                        isExpanded: true,
                        initialValue: selectedPort1,
                        items: serialPortItems,
                        decoration: const InputDecoration(
                          border: OutlineInputBorder(),
                          isDense: true,
                        ),
                        onChanged: onPort1Changed,
                      ),
                      const SizedBox(height: 8),
                      const Text('Serial Port 2', style: TextStyle(fontWeight: FontWeight.bold)),
                      const SizedBox(height: 4),
                      DropdownButtonFormField<String>(
                        isExpanded: true,
                        initialValue: selectedPort2,
                        items: serialPortItems,
                        decoration: const InputDecoration(
                          border: OutlineInputBorder(),
                          isDense: true,
                        ),
                        onChanged: onPort2Changed,
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 12),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    ElevatedButton(
                      onPressed: backendRunning ? null : onStartBackend,
                      child: const Text('Start'),
                    ),
                    ElevatedButton(
                      onPressed: backendRunning ? onStopBackend : null,
                      child: const Text('Stop'),
                    ),
                    OutlinedButton(
                      onPressed: onRescanPorts,
                      child: const Text('Rescan'),
                    ),
                    ElevatedButton(
                      onPressed: onScan,
                      child: const Text('SCAN'),
                    ),
                    ElevatedButton(
                      onPressed: onConn,
                      child: const Text('CONN'),
                    ),
                    ElevatedButton(
                      onPressed: onStartStream,
                      child: const Text('START'),
                    ),
                    OutlinedButton(
                      onPressed: onStopStream,
                      child: const Text('STOP'),
                    ),
                    OutlinedButton(
                      onPressed: onTsync,
                      child: const Text('TSYNC'),
                    ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 12),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('BLE peers', style: TextStyle(fontWeight: FontWeight.bold)),
                      const SizedBox(height: 6),
                      Container(
                        constraints: const BoxConstraints(minHeight: 120, maxHeight: 210),
                        decoration: BoxDecoration(
                          border: Border.all(color: Colors.grey.shade400),
                          borderRadius: BorderRadius.circular(8),
                        ),
                        child: sortedPeers.isEmpty
                            ? const Center(child: Text('No BLE peers discovered yet'))
                            : ListView.separated(
                                padding: const EdgeInsets.all(8),
                                itemCount: sortedPeers.length,
                                separatorBuilder: (_, __) => const Divider(height: 12),
                                itemBuilder: (context, index) {
                                  final peer = sortedPeers[index];
                                  return ListTile(
                                    dense: true,
                                    contentPadding: EdgeInsets.zero,
                                    title: Text(
                                      peer.displayName,
                                      style: const TextStyle(fontWeight: FontWeight.bold),
                                    ),
                                    subtitle: Text('${peer.address}\n${peer.lastLine}'),
                                    isThreeLine: true,
                                    leading: Icon(Icons.bluetooth, color: _stateColor(peer.state, context)),
                                    trailing: Text(peer.stateText),
                                  );
                                },
                              ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

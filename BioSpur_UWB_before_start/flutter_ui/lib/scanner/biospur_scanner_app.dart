import 'package:flutter/material.dart';

import 'scanner_controller.dart';

class BioSpurScannerApp extends StatelessWidget {
  const BioSpurScannerApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'BS BLE Scanner',
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF0B6E99),
          brightness: Brightness.light,
        ),
      ),
      home: const _ScannerHome(),
    );
  }
}

class _ScannerHome extends StatefulWidget {
  const _ScannerHome();

  @override
  State<_ScannerHome> createState() => _ScannerHomeState();
}

class _ScannerHomeState extends State<_ScannerHome> {
  late final ScannerController _controller;

  @override
  void initState() {
    super.initState();
    _controller = ScannerController();
    _controller.addListener(_onControllerChanged);
  }

  void _onControllerChanged() {
    if (mounted) {
      setState(() {});
    }
  }

  @override
  void dispose() {
    _controller.removeListener(_onControllerChanged);
    _controller.dispose();
    super.dispose();
  }

  Color _stateColor(bool online, BuildContext context) {
    return online ? Colors.green.shade700 : Colors.grey.shade500;
  }

  Widget _peerCard({
    required BuildContext context,
    required ScannedDevice device,
  }) {
    return Container(
      width: 320,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.black12),
        boxShadow: const [
          BoxShadow(
            color: Color(0x11000000),
            blurRadius: 12,
            offset: Offset(0, 4),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  device.displayName,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.w700,
                      ),
                ),
              ),
              const SizedBox(width: 8),
              _StatusChip(
                label: device.online ? 'online' : 'offline',
                color: _stateColor(device.online, context),
              ),
            ],
          ),
          const SizedBox(height: 10),
          _FieldLine(label: 'RSSI', value: '${device.rssi} dBm'),
          const SizedBox(height: 4),
          _FieldLine(label: 'UUID', value: device.uuid),
          const SizedBox(height: 4),
          _FieldLine(label: 'ADDR', value: device.address),
          const SizedBox(height: 4),
          _FieldLine(label: 'INFO', value: device.extra),
          const SizedBox(height: 8),
          Text(
            'last seen ${DateTime.now().difference(device.lastSeenAt).inSeconds}s ago',
            style: Theme.of(context).textTheme.bodySmall?.copyWith(
                  color: Colors.black54,
                ),
          ),
        ],
      ),
    );
  }

  Widget _deviceSection({
    required BuildContext context,
    required String title,
    required List<ScannedDevice> devices,
    required IconData icon,
    required Color accent,
  }) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: [
            accent.withValues(alpha: 0.09),
            Theme.of(context).colorScheme.surface,
          ],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: accent.withValues(alpha: 0.18)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, color: accent),
              const SizedBox(width: 10),
              Text(
                title,
                style: Theme.of(context).textTheme.titleLarge?.copyWith(
                      fontWeight: FontWeight.w800,
                    ),
              ),
              const Spacer(),
              _StatusChip(label: '${devices.length}', color: accent),
            ],
          ),
          const SizedBox(height: 16),
          if (devices.isEmpty)
            Container(
              padding: const EdgeInsets.all(18),
              width: double.infinity,
              decoration: BoxDecoration(
                color: Colors.white.withValues(alpha: 0.55),
                borderRadius: BorderRadius.circular(14),
                border: Border.all(color: Colors.black12),
              ),
              child: Text(
                'No $title seen yet',
                style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                      color: Colors.black54,
                    ),
              ),
            )
          else
            Wrap(
              spacing: 12,
              runSpacing: 12,
              children: devices.map((d) => _peerCard(context: context, device: d)).toList(),
            ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final ports = _controller.ports;
    final selectedPort = _controller.selectedPort;
    final anchors = _controller.anchors;
    final tags = _controller.tags;
    final connected = _controller.connected;
    final onlineAnchors = anchors.where((d) => d.online).length;
    final onlineTags = tags.where((d) => d.online).length;
    final status = _controller.status;

    return Scaffold(
      backgroundColor: const Color(0xFFF4F7FB),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(18),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _HeaderBar(
                connected: connected,
                status: status,
                ports: ports,
                selectedPort: selectedPort,
                onPortChanged: _controller.selectPort,
                onRefreshPorts: _controller.scanPorts,
                onConnect: _controller.connect,
                onDisconnect: _controller.disconnect,
              ),
              const SizedBox(height: 16),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              _StatusChip(label: 'Anchors online: $onlineAnchors/${anchors.length}', color: const Color(0xFF0B6E99)),
              _StatusChip(label: 'Tags online: $onlineTags/${tags.length}', color: const Color(0xFF8B5CF6)),
              _StatusChip(label: connected ? 'scanner active' : 'scanner idle', color: connected ? Colors.green : Colors.grey),
            ],
          ),
              const SizedBox(height: 16),
              Expanded(
                child: LayoutBuilder(
                  builder: (context, constraints) {
                    final narrow = constraints.maxWidth < 1100;
                    final anchorSection = _deviceSection(
                      context: context,
                      title: 'Anchor',
                      devices: anchors,
                      icon: Icons.hub_outlined,
                      accent: const Color(0xFF0B6E99),
                    );
                    final tagSection = _deviceSection(
                      context: context,
                      title: 'Tag',
                      devices: tags,
                      icon: Icons.radar_outlined,
                      accent: const Color(0xFF8B5CF6),
                    );

                    if (narrow) {
                      return ListView(
                        children: [
                          anchorSection,
                          const SizedBox(height: 16),
                          tagSection,
                        ],
                      );
                    }

                    return Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Expanded(child: anchorSection),
                        const SizedBox(width: 16),
                        Expanded(child: tagSection),
                      ],
                    );
                  },
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _HeaderBar extends StatelessWidget {
  final bool connected;
  final String status;
  final List<SerialPortInfo> ports;
  final String? selectedPort;
  final ValueChanged<String?> onPortChanged;
  final VoidCallback onRefreshPorts;
  final VoidCallback onConnect;
  final VoidCallback onDisconnect;

  const _HeaderBar({
    required this.connected,
    required this.status,
    required this.ports,
    required this.selectedPort,
    required this.onPortChanged,
    required this.onRefreshPorts,
    required this.onConnect,
    required this.onDisconnect,
  });

  @override
  Widget build(BuildContext context) {
    final items = ports
        .map(
          (p) => DropdownMenuItem<String>(
            value: p.path,
            child: Text(
              p.label,
              overflow: TextOverflow.ellipsis,
            ),
          ),
        )
        .toList();

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Colors.black12),
        boxShadow: const [
          BoxShadow(
            color: Color(0x0F000000),
            blurRadius: 16,
            offset: Offset(0, 6),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(
                'BioSpur BLE Scanner',
                style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                      fontWeight: FontWeight.w800,
                    ),
              ),
              const Spacer(),
              _StatusChip(
                label: connected ? 'connected' : 'disconnected',
                color: connected ? Colors.green : Colors.grey,
              ),
            ],
          ),
          const SizedBox(height: 10),
          Text(
            status,
            style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                  color: Colors.black54,
                ),
          ),
          const SizedBox(height: 16),
          Wrap(
            spacing: 12,
            runSpacing: 12,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              SizedBox(
                width: 420,
                child: DropdownButtonFormField<String>(
                  initialValue: selectedPort,
                  items: items,
                  isExpanded: true,
                  decoration: const InputDecoration(
                    labelText: 'Serial port',
                    border: OutlineInputBorder(),
                  ),
                  onChanged: onPortChanged,
                ),
              ),
              OutlinedButton(
                onPressed: onRefreshPorts,
                child: const Text('Rescan'),
              ),
              FilledButton(
                onPressed: connected ? null : onConnect,
                child: const Text('Connect'),
              ),
              OutlinedButton(
                onPressed: connected ? onDisconnect : null,
                child: const Text('Disconnect'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _StatusChip extends StatelessWidget {
  final String label;
  final Color color;

  const _StatusChip({required this.label, required this.color});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: color.withValues(alpha: 0.35)),
      ),
      child: Text(
        label,
        style: Theme.of(context).textTheme.labelMedium?.copyWith(
              color: color,
              fontWeight: FontWeight.w700,
            ),
      ),
    );
  }
}

class _FieldLine extends StatelessWidget {
  final String label;
  final String value;

  const _FieldLine({required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: 54,
          child: Text(
            label,
            style: Theme.of(context).textTheme.labelMedium?.copyWith(
                  fontWeight: FontWeight.w800,
                  color: Colors.black54,
                ),
          ),
        ),
        Expanded(
          child: Text(
            value,
            style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                  fontFeatures: const [],
                ),
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }
}

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

void main() {
  runApp(const BioSpurBleApp());
}

const repoRoot = '/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start';
const defaultBlePort =
    '/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Listener_760AE3DFC3CD8F38-if00';
const appVersion = '1.0.1';
const appBuildStamp = '2026-06-26 00:20 CEST';
const appBuildNote = 'BLE listener status monitor with strict BADV parsing';

const biospurGreen = Color(0xFFA3E635);
const biospurGlow = Color(0xFFD9FC05);
const controlGreen = Color(0xFFB9D98F);
const biospurBlack = Color(0xFF050806);
const panelLine = Color(0x33638A01);
const tableLine = Color(0xAA638A01);
const mutedText = Color(0xFFB6C7B3);

String resolveBleTailScript() {
  final exeDir = File(Platform.resolvedExecutable).parent.path;
  final candidates = [
    Platform.environment['BIOSPUR_BLE_TAIL_SCRIPT'] ?? '',
    '$exeDir/scripts/ble_listener_tail.py',
    '$repoRoot/flutter_ui_ble/scripts/ble_listener_tail.py',
  ];
  for (final candidate in candidates) {
    if (candidate.isNotEmpty && File(candidate).existsSync()) {
      return candidate;
    }
  }
  return candidates.last;
}

String resolveBleTailWorkingDirectory(String scriptPath) {
  final script = File(scriptPath);
  final scriptDir = script.parent;
  if (scriptDir.path.endsWith('/scripts')) {
    return scriptDir.parent.path;
  }
  return '$repoRoot/flutter_ui_ble';
}

class BioSpurBleApp extends StatelessWidget {
  const BioSpurBleApp({super.key, this.autoStart = true});

  final bool autoStart;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'BioSpur BLE Monitor',
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: controlGreen,
          brightness: Brightness.dark,
          primary: controlGreen,
          secondary: controlGreen,
          tertiary: biospurGlow,
          surface: Colors.transparent,
        ),
        filledButtonTheme: FilledButtonThemeData(
          style: FilledButton.styleFrom(
            backgroundColor: controlGreen,
            foregroundColor: biospurBlack,
            disabledBackgroundColor: controlGreen.withValues(alpha: 0.18),
            disabledForegroundColor: controlGreen.withValues(alpha: 0.35),
          ),
        ),
        outlinedButtonTheme: OutlinedButtonThemeData(
          style: OutlinedButton.styleFrom(
            backgroundColor: Colors.transparent,
            foregroundColor: controlGreen,
            disabledForegroundColor: controlGreen.withValues(alpha: 0.35),
            side: const BorderSide(color: controlGreen),
          ),
        ),
        textButtonTheme: TextButtonThemeData(
          style: TextButton.styleFrom(
            foregroundColor: controlGreen,
            disabledForegroundColor: controlGreen.withValues(alpha: 0.35),
          ),
        ),
        progressIndicatorTheme: const ProgressIndicatorThemeData(
          color: controlGreen,
          linearTrackColor: Color(0x55384515),
        ),
        cardTheme: const CardThemeData(
          elevation: 0,
          color: Colors.transparent,
          surfaceTintColor: Colors.transparent,
          shadowColor: Colors.transparent,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.all(Radius.circular(8)),
            side: BorderSide(color: panelLine),
          ),
        ),
        scaffoldBackgroundColor: Colors.transparent,
        tabBarTheme: const TabBarThemeData(
          labelColor: biospurGlow,
          unselectedLabelColor: Color(0xFFB6C7B3),
          indicatorColor: biospurGlow,
        ),
      ),
      home: BleMonitorPage(autoStart: autoStart),
    );
  }
}

class BleMonitorPage extends StatefulWidget {
  const BleMonitorPage({super.key, required this.autoStart});

  final bool autoStart;

  @override
  State<BleMonitorPage> createState() => _BleMonitorPageState();
}

class _BleMonitorPageState extends State<BleMonitorPage>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs;
  late final TextEditingController _portController;
  late final Timer _tick;
  final ScrollController _rawScroll = ScrollController();
  final List<String> _rawLines = [];
  final Map<String, BlePeer> _peers = {};
  BleSummary _summary = BleSummary.empty();
  Process? _process;
  List<String> _ports = [];
  String _stateText = 'idle';
  String? _firmwareVersion;
  String? _activePort;
  DateTime? _lastLineAt;
  bool _autoScroll = true;

  bool get _running => _process != null;

  @override
  void initState() {
    super.initState();
    _tabs = TabController(length: 2, vsync: this);
    _portController = TextEditingController(text: defaultBlePort);
    _refreshPorts();
    _tick = Timer.periodic(const Duration(milliseconds: 500), (_) {
      if (mounted) setState(() {});
    });
    if (widget.autoStart) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _startTail());
    }
  }

  @override
  void dispose() {
    _tick.cancel();
    _tabs.dispose();
    _portController.dispose();
    _rawScroll.dispose();
    _stopTail(updateState: false);
    super.dispose();
  }

  Future<void> _refreshPorts() async {
    final dir = Directory('/dev/serial/by-id');
    final ports = <String>[];
    if (dir.existsSync()) {
      final entries =
          dir.listSync().whereType<Link>().map((entry) => entry.path).where((
            path,
          ) {
            final lower = path.toLowerCase();
            return lower.contains('biospur') ||
                lower.contains('nordic') ||
                lower.contains('segger');
          }).toList()..sort();
      ports.addAll(entries);
    }
    if (!mounted) return;
    setState(() => _ports = ports);
    final listener = ports.where((p) => p.contains('BioSpur_BLE_Listener'));
    if (_portController.text.trim().isEmpty && listener.isNotEmpty) {
      _portController.text = listener.first;
    }
  }

  Future<void> _startTail() async {
    if (_running) return;
    await _refreshPorts();
    final port = _portController.text.trim().isEmpty
        ? 'auto'
        : _portController.text.trim();
    setState(() {
      _stateText = 'connecting';
      _activePort = port;
    });

    try {
      final tailScript = resolveBleTailScript();
      final process = await Process.start('python3', [
        tailScript,
        '--port',
        port,
        '--baud',
        '115200',
      ], workingDirectory: resolveBleTailWorkingDirectory(tailScript));
      _process = process;
      setState(() => _stateText = 'running');
      process.stdout
          .transform(utf8.decoder)
          .transform(const LineSplitter())
          .listen(
            _handleLine,
            onError: (Object err) => _appendRaw('[stdout] $err'),
          );
      process.stderr
          .transform(utf8.decoder)
          .transform(const LineSplitter())
          .listen((line) => _appendRaw('[tail] $line'));
      unawaited(
        process.exitCode.then((code) {
          if (!mounted) return;
          setState(() {
            if (identical(_process, process)) {
              _process = null;
              _stateText = code == 0 ? 'stopped' : 'exit $code';
            }
          });
        }),
      );
    } catch (err) {
      if (!mounted) return;
      setState(() => _stateText = 'start failed');
      _appendRaw('[ui] tail start failed: $err');
    }
  }

  void _stopTail({bool updateState = true}) {
    final process = _process;
    _process = null;
    process?.kill(ProcessSignal.sigterm);
    if (updateState && mounted) setState(() => _stateText = 'stopped');
  }

  Future<void> _restartTail() async {
    _stopTail();
    await Future<void>.delayed(const Duration(milliseconds: 250));
    await _startTail();
  }

  void _clear() {
    setState(() {
      _rawLines.clear();
      _peers.clear();
      _summary = BleSummary.empty();
      _lastLineAt = null;
    });
  }

  void _handleLine(String line) {
    _appendRaw(line);
    final parsed = ParsedBleLine.parse(line);
    if (parsed == null) return;

    setState(() {
      _lastLineAt = DateTime.now();
      if (parsed.bootVersion != null) {
        _firmwareVersion = parsed.bootVersion;
      }
      if (parsed.summary != null) {
        _summary = parsed.summary!;
      }
      if (parsed.peer != null) {
        _peers[parsed.peer!.addr] = parsed.peer!.copyWith(
          hostSeenAt: DateTime.now(),
        );
      }
    });
  }

  void _appendRaw(String line) {
    if (!mounted) return;
    setState(() {
      _rawLines.add(line);
      if (_rawLines.length > 2000) {
        _rawLines.removeRange(0, _rawLines.length - 2000);
      }
    });
    if (_autoScroll) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (!_rawScroll.hasClients) return;
        _rawScroll.jumpTo(_rawScroll.position.maxScrollExtent);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final peers = _peers.values.toList()
      ..sort((a, b) {
        final kind = a.kind.compareTo(b.kind);
        if (kind != 0) return kind;
        return a.id.compareTo(b.id);
      });

    return Scaffold(
      backgroundColor: biospurBlack,
      body: Stack(
        fit: StackFit.expand,
        children: [
          const BiospurBackground(),
          Column(
            children: [
              BleHeader(
                stateText: _stateText,
                running: _running,
                firmwareVersion: _firmwareVersion,
                activePort: _activePort,
                ports: _ports,
                portController: _portController,
                onPortSelected: (value) {
                  if (value == null) return;
                  setState(() => _portController.text = value);
                },
                onRefreshPorts: _refreshPorts,
                onStart: _startTail,
                onStop: _stopTail,
                onRestart: _restartTail,
                onClear: _clear,
              ),
              BleStatusStrip(
                summary: _summary,
                peers: peers,
                running: _running,
                lastLineAt: _lastLineAt,
              ),
              Material(
                color: Colors.transparent,
                child: TabBar(
                  controller: _tabs,
                  tabs: const [
                    Tab(
                      icon: Icon(Icons.monitor_heart_outlined),
                      text: 'Status Monitor',
                    ),
                    Tab(icon: Icon(Icons.article_outlined), text: 'Raw Log'),
                  ],
                ),
              ),
              Expanded(
                child: TabBarView(
                  controller: _tabs,
                  children: [
                    StatusMonitorTab(summary: _summary, peers: peers),
                    RawLogTab(
                      lines: _rawLines,
                      controller: _rawScroll,
                      autoScroll: _autoScroll,
                      onAutoScrollChanged: (value) =>
                          setState(() => _autoScroll = value),
                      onCopy: () {
                        Clipboard.setData(
                          ClipboardData(text: _rawLines.join('\n')),
                        );
                      },
                      onClear: _clear,
                    ),
                  ],
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class BleHeader extends StatelessWidget {
  const BleHeader({
    super.key,
    required this.stateText,
    required this.running,
    required this.firmwareVersion,
    required this.activePort,
    required this.ports,
    required this.portController,
    required this.onPortSelected,
    required this.onRefreshPorts,
    required this.onStart,
    required this.onStop,
    required this.onRestart,
    required this.onClear,
  });

  final String stateText;
  final bool running;
  final String? firmwareVersion;
  final String? activePort;
  final List<String> ports;
  final TextEditingController portController;
  final ValueChanged<String?> onPortSelected;
  final VoidCallback onRefreshPorts;
  final VoidCallback onStart;
  final VoidCallback onStop;
  final VoidCallback onRestart;
  final VoidCallback onClear;

  @override
  Widget build(BuildContext context) {
    final selected = ports.contains(portController.text)
        ? portController.text
        : null;
    final titleBlock = Row(
      children: [
        Image.asset(
          'assets/images/biospur_logo.png',
          width: 44,
          height: 44,
          filterQuality: FilterQuality.high,
        ),
        const SizedBox(width: 14),
        const Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'BioSpur BLE Monitor',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: 22,
                  fontWeight: FontWeight.w800,
                  color: Colors.white,
                ),
              ),
              SizedBox(height: 3),
              Text(
                'v$appVersion  |  $appBuildStamp  |  $appBuildNote',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  color: mutedText,
                  fontFamily: 'monospace',
                  fontSize: 12,
                ),
              ),
            ],
          ),
        ),
      ],
    );
    final portPicker = Row(
      children: [
        Expanded(
          child: PanelSurface(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
            child: Row(
              children: [
                const Icon(Icons.usb_outlined, size: 18, color: controlGreen),
                const SizedBox(width: 8),
                Expanded(
                  child: DropdownButtonHideUnderline(
                    child: DropdownButton<String>(
                      isDense: true,
                      isExpanded: true,
                      value: selected,
                      hint: Text(
                        compactPath(portController.text),
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(color: mutedText),
                      ),
                      dropdownColor: const Color(0xFF0A0F08),
                      items: ports
                          .map(
                            (p) => DropdownMenuItem(
                              value: p,
                              child: Text(
                                compactPath(p),
                                overflow: TextOverflow.ellipsis,
                              ),
                            ),
                          )
                          .toList(),
                      onChanged: onPortSelected,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(width: 8),
        IconButton.filledTonal(
          tooltip: 'Refresh ports',
          onPressed: onRefreshPorts,
          icon: const Icon(Icons.refresh_outlined),
        ),
      ],
    );
    final statusPills = Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        StatusPill(
          label: 'Tail',
          value: stateText,
          tone: running ? PillTone.good : PillTone.neutral,
        ),
        StatusPill(
          label: 'FW',
          value: firmwareVersion ?? '-',
          tone: firmwareVersion == null ? PillTone.neutral : PillTone.good,
        ),
      ],
    );
    final actions = Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        FilledButton.icon(
          onPressed: running ? null : onStart,
          icon: const Icon(Icons.play_arrow_outlined),
          label: const Text('Start'),
        ),
        OutlinedButton.icon(
          onPressed: running ? onStop : null,
          icon: const Icon(Icons.stop_outlined),
          label: const Text('Stop'),
        ),
        OutlinedButton.icon(
          onPressed: onRestart,
          icon: const Icon(Icons.restart_alt_outlined),
          label: const Text('Restart'),
        ),
        TextButton.icon(
          onPressed: onClear,
          icon: const Icon(Icons.delete_sweep_outlined),
          label: const Text('Clear'),
        ),
      ],
    );
    return Container(
      padding: const EdgeInsets.fromLTRB(18, 14, 18, 12),
      decoration: BoxDecoration(
        color: const Color(0xAA050806),
        border: Border(
          bottom: BorderSide(color: panelLine.withValues(alpha: 0.8)),
        ),
      ),
      child: LayoutBuilder(
        builder: (context, constraints) {
          if (constraints.maxWidth < 1060) {
            return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(child: titleBlock),
                    const SizedBox(width: 10),
                    statusPills,
                  ],
                ),
                const SizedBox(height: 10),
                portPicker,
                const SizedBox(height: 10),
                actions,
              ],
            );
          }
          return Row(
            children: [
              Expanded(flex: 2, child: titleBlock),
              const SizedBox(width: 14),
              Expanded(flex: 3, child: portPicker),
              const SizedBox(width: 12),
              statusPills,
              const SizedBox(width: 12),
              Flexible(
                fit: FlexFit.loose,
                child: SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  child: actions,
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}

class BleStatusStrip extends StatelessWidget {
  const BleStatusStrip({
    super.key,
    required this.summary,
    required this.peers,
    required this.running,
    required this.lastLineAt,
  });

  final BleSummary summary;
  final List<BlePeer> peers;
  final bool running;
  final DateTime? lastLineAt;

  @override
  Widget build(BuildContext context) {
    final freshPeers = peers.where((p) => p.isFresh).length;
    final lineAge = lastLineAt == null
        ? '-'
        : '${DateTime.now().difference(lastLineAt!).inSeconds}s';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 9),
      decoration: const BoxDecoration(color: Color(0x77050806)),
      child: Wrap(
        spacing: 8,
        runSpacing: 8,
        children: [
          StatusPill(
            label: 'Dongle',
            value: running ? 'online' : 'idle',
            tone: running ? PillTone.good : PillTone.neutral,
          ),
          StatusPill(
            label: 'Scan',
            value: summary.scan ? 'active' : '-',
            tone: summary.scan ? PillTone.good : PillTone.warn,
          ),
          StatusPill(
            label: 'Tags',
            value: '${summary.tags}',
            tone: summary.tags > 0 ? PillTone.good : PillTone.neutral,
          ),
          StatusPill(
            label: 'Anchors',
            value: '${summary.anchors}',
            tone: summary.anchors > 0 ? PillTone.good : PillTone.neutral,
          ),
          StatusPill(
            label: 'DFU',
            value: '${summary.dfu}',
            tone: summary.dfu > 0 ? PillTone.warn : PillTone.neutral,
          ),
          StatusPill(
            label: 'Fresh',
            value: '$freshPeers',
            tone: freshPeers > 0 ? PillTone.good : PillTone.neutral,
          ),
          StatusPill(
            label: 'Adv',
            value: '${summary.adv}',
            tone: summary.adv > 0 ? PillTone.active : PillTone.neutral,
          ),
          StatusPill(
            label: 'Line age',
            value: lineAge,
            tone: lastLineAt == null
                ? PillTone.neutral
                : DateTime.now().difference(lastLineAt!).inSeconds < 4
                ? PillTone.good
                : PillTone.warn,
          ),
        ],
      ),
    );
  }
}

class StatusMonitorTab extends StatelessWidget {
  const StatusMonitorTab({
    super.key,
    required this.summary,
    required this.peers,
  });

  final BleSummary summary;
  final List<BlePeer> peers;

  @override
  Widget build(BuildContext context) {
    final tags = peers.where((p) => p.kind == 'TAG').toList();
    final anchors = peers.where((p) => p.kind == 'ANCHOR').toList();
    final dfu = peers.where((p) => p.dfu).toList();

    return LayoutBuilder(
      builder: (context, constraints) {
        final narrow = constraints.maxWidth < 980;
        final contentMinHeight = math.max(0.0, constraints.maxHeight - 32);
        final tableHeight = narrow
            ? math.max(300.0, constraints.maxHeight - 430.0)
            : math.max(300.0, constraints.maxHeight - 230.0);

        final summaryPanel = SummaryPanel(
          summary: summary,
          tags: tags.length,
          anchors: anchors.length,
          dfu: dfu.length,
        );
        final ledPanel = LedLegendPanel(summary: summary);

        final top = narrow
            ? Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [summaryPanel, const SizedBox(height: 14), ledPanel],
              )
            : Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(child: summaryPanel),
                  const SizedBox(width: 14),
                  SizedBox(width: 360, child: ledPanel),
                ],
              );

        return SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: ConstrainedBox(
            constraints: BoxConstraints(minHeight: contentMinHeight),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                top,
                const SizedBox(height: 14),
                SizedBox(
                  height: tableHeight,
                  child: PeerTable(peers: peers),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class SummaryPanel extends StatelessWidget {
  const SummaryPanel({
    super.key,
    required this.summary,
    required this.tags,
    required this.anchors,
    required this.dfu,
  });

  final BleSummary summary;
  final int tags;
  final int anchors;
  final int dfu;

  @override
  Widget build(BuildContext context) {
    return PanelSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const PanelTitle(icon: Icons.dashboard_outlined, title: 'Status'),
          const SizedBox(height: 14),
          LayoutBuilder(
            builder: (context, constraints) {
              final width = constraints.maxWidth;
              final cols = width > 920
                  ? 4
                  : width > 640
                  ? 3
                  : 2;
              return GridView.count(
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                crossAxisCount: cols,
                crossAxisSpacing: 10,
                mainAxisSpacing: 10,
                childAspectRatio: cols >= 3 ? 2.95 : 2.65,
                children: [
                  MetricTile(
                    label: 'Tags',
                    value: '$tags / ${summary.tags}',
                    icon: Icons.sell_outlined,
                    tone: summary.tags > 0 ? PillTone.good : PillTone.neutral,
                  ),
                  MetricTile(
                    label: 'Anchors',
                    value: '$anchors / ${summary.anchors}',
                    icon: Icons.sensors_outlined,
                    tone: summary.anchors > 0
                        ? PillTone.good
                        : PillTone.neutral,
                  ),
                  MetricTile(
                    label: 'DFU',
                    value: '$dfu / ${summary.dfu}',
                    icon: Icons.system_update_alt_outlined,
                    tone: summary.dfu > 0 ? PillTone.warn : PillTone.neutral,
                  ),
                  MetricTile(
                    label: 'Unknown',
                    value: '${summary.unknown}',
                    icon: Icons.help_outline,
                    tone: summary.unknown > 0
                        ? PillTone.warn
                        : PillTone.neutral,
                  ),
                  MetricTile(
                    label: 'Total',
                    value: '${summary.total}',
                    icon: Icons.blur_on_outlined,
                    tone: summary.total > 0
                        ? PillTone.active
                        : PillTone.neutral,
                  ),
                  MetricTile(
                    label: 'Stale',
                    value: '${summary.stale}',
                    icon: Icons.history_toggle_off_outlined,
                    tone: summary.stale > 0 ? PillTone.warn : PillTone.neutral,
                  ),
                  MetricTile(
                    label: 'Adv',
                    value: '${summary.adv}',
                    icon: Icons.podcasts_outlined,
                    tone: summary.adv > 0 ? PillTone.good : PillTone.neutral,
                  ),
                  MetricTile(
                    label: 'Printed',
                    value: '${summary.printed}',
                    icon: Icons.short_text_outlined,
                    tone: summary.printed > 0
                        ? PillTone.good
                        : PillTone.neutral,
                  ),
                ],
              );
            },
          ),
        ],
      ),
    );
  }
}

class LedLegendPanel extends StatelessWidget {
  const LedLegendPanel({super.key, required this.summary});

  final BleSummary summary;

  @override
  Widget build(BuildContext context) {
    return PanelSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const PanelTitle(icon: Icons.lightbulb_outline, title: 'Dongle LED'),
          const SizedBox(height: 12),
          LedRow(
            color: const Color(0xFF60A5FA),
            label: 'Blue',
            value: summary.total == 0 && summary.scan ? 'scan idle' : '-',
          ),
          const SizedBox(height: 8),
          LedRow(
            color: controlGreen,
            label: 'Green',
            value: summary.tags + summary.anchors > 0 ? 'BioSpur seen' : '-',
          ),
          const SizedBox(height: 8),
          LedRow(
            color: const Color(0xFFE7C55A),
            label: 'Yellow',
            value: summary.dfu > 0 ? 'DFU visible' : '-',
          ),
          const SizedBox(height: 8),
          LedRow(
            color: toneColor(PillTone.bad),
            label: 'Red',
            value: summary.scan ? '-' : 'scan stopped',
          ),
        ],
      ),
    );
  }
}

class PeerTable extends StatelessWidget {
  const PeerTable({super.key, required this.peers});

  final List<BlePeer> peers;

  @override
  Widget build(BuildContext context) {
    return PanelSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(
                child: PanelTitle(icon: Icons.hub_outlined, title: 'BLE Peers'),
              ),
              StatusPill(
                label: 'Rows',
                value: '${peers.length}',
                tone: peers.isNotEmpty ? PillTone.good : PillTone.neutral,
              ),
            ],
          ),
          const SizedBox(height: 12),
          Expanded(
            child: peers.isEmpty
                ? const Center(
                    child: Text(
                      'No BioSpur advertisements',
                      style: TextStyle(color: mutedText),
                    ),
                  )
                : Scrollbar(
                    child: SingleChildScrollView(
                      scrollDirection: Axis.horizontal,
                      child: SingleChildScrollView(
                        child: DataTable(
                          headingRowColor: WidgetStateProperty.all(
                            const Color(0x66242B12),
                          ),
                          dataRowMinHeight: 38,
                          dataRowMaxHeight: 46,
                          headingTextStyle: const TextStyle(
                            color: controlGreen,
                            fontWeight: FontWeight.w800,
                          ),
                          columns: const [
                            DataColumn(label: Text('Kind')),
                            DataColumn(label: Text('ID')),
                            DataColumn(label: Text('Name')),
                            DataColumn(label: Text('Role')),
                            DataColumn(label: Text('RSSI')),
                            DataColumn(label: Text('DFU')),
                            DataColumn(label: Text('Age')),
                            DataColumn(label: Text('Address')),
                            DataColumn(label: Text('UUID')),
                          ],
                          rows: peers.map(_peerRow).toList(),
                        ),
                      ),
                    ),
                  ),
          ),
        ],
      ),
    );
  }

  DataRow _peerRow(BlePeer peer) {
    final age = DateTime.now().difference(peer.hostSeenAt).inSeconds;
    final tone = peer.dfu
        ? PillTone.warn
        : peer.isFresh
        ? PillTone.good
        : PillTone.neutral;
    return DataRow(
      cells: [
        DataCell(StatusPill(label: '', value: peer.kind, tone: tone)),
        DataCell(_mono(peer.id)),
        DataCell(Text(peer.name)),
        DataCell(Text(peer.role)),
        DataCell(_mono('${peer.rssi} dBm')),
        DataCell(
          Icon(
            peer.dfu ? Icons.system_update_alt_outlined : Icons.remove,
            color: peer.dfu ? toneColor(PillTone.warn) : mutedText,
            size: 18,
          ),
        ),
        DataCell(_mono('${age}s')),
        DataCell(_mono(peer.addr)),
        DataCell(_mono(peer.uuid)),
      ],
    );
  }

  Widget _mono(String value) => Text(
    value,
    overflow: TextOverflow.ellipsis,
    style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
  );
}

class RawLogTab extends StatelessWidget {
  const RawLogTab({
    super.key,
    required this.lines,
    required this.controller,
    required this.autoScroll,
    required this.onAutoScrollChanged,
    required this.onCopy,
    required this.onClear,
  });

  final List<String> lines;
  final ScrollController controller;
  final bool autoScroll;
  final ValueChanged<bool> onAutoScrollChanged;
  final VoidCallback onCopy;
  final VoidCallback onClear;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(16),
      child: PanelSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Expanded(
                  child: PanelTitle(
                    icon: Icons.article_outlined,
                    title: 'Raw Log',
                  ),
                ),
                StatusPill(
                  label: 'Lines',
                  value: '${lines.length}',
                  tone: lines.isEmpty ? PillTone.neutral : PillTone.good,
                ),
                const SizedBox(width: 8),
                FilterChip(
                  selected: autoScroll,
                  onSelected: onAutoScrollChanged,
                  label: const Text('Autoscroll'),
                  avatar: const Icon(Icons.vertical_align_bottom_outlined),
                ),
                const SizedBox(width: 8),
                OutlinedButton.icon(
                  onPressed: lines.isEmpty ? null : onCopy,
                  icon: const Icon(Icons.copy_outlined),
                  label: const Text('Copy'),
                ),
                const SizedBox(width: 8),
                TextButton.icon(
                  onPressed: lines.isEmpty ? null : onClear,
                  icon: const Icon(Icons.delete_sweep_outlined),
                  label: const Text('Clear'),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Expanded(
              child: Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: const Color(0xDD020602),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: const Color(0x55384515)),
                ),
                child: Scrollbar(
                  controller: controller,
                  child: ListView.builder(
                    controller: controller,
                    itemCount: lines.length,
                    itemBuilder: (context, index) {
                      final line = lines[index];
                      return SelectableText(
                        line,
                        style: TextStyle(
                          color:
                              line.startsWith('[tail]') ||
                                  line.startsWith('[ui]')
                              ? toneColor(PillTone.warn)
                              : const Color(0xFFD7EEC1),
                          fontFamily: 'monospace',
                          fontSize: 12,
                          height: 1.25,
                        ),
                      );
                    },
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

class PanelSurface extends StatelessWidget {
  const PanelSurface({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(14),
  });

  final Widget child;
  final EdgeInsetsGeometry padding;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: padding,
      decoration: BoxDecoration(
        color: const Color(0xEE050806),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: panelLine),
      ),
      child: child,
    );
  }
}

class PanelTitle extends StatelessWidget {
  const PanelTitle({super.key, required this.icon, required this.title});

  final IconData icon;
  final String title;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Icon(icon, color: controlGreen, size: 20),
        const SizedBox(width: 8),
        Text(
          title,
          style: const TextStyle(
            color: Colors.white,
            fontSize: 16,
            fontWeight: FontWeight.w800,
          ),
        ),
      ],
    );
  }
}

class MetricTile extends StatelessWidget {
  const MetricTile({
    super.key,
    required this.label,
    required this.value,
    required this.icon,
    required this.tone,
  });

  final String label;
  final String value;
  final IconData icon;
  final PillTone tone;

  @override
  Widget build(BuildContext context) {
    final color = toneColor(tone);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: color.withValues(alpha: 0.28)),
      ),
      child: Row(
        children: [
          Icon(icon, color: color, size: 22),
          const SizedBox(width: 9),
          Expanded(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              mainAxisAlignment: MainAxisAlignment.center,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: mutedText, fontSize: 12),
                ),
                const SizedBox(height: 2),
                Text(
                  value,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: color,
                    fontSize: 18,
                    fontWeight: FontWeight.w800,
                    fontFamily: 'monospace',
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class LedRow extends StatelessWidget {
  const LedRow({
    super.key,
    required this.color,
    required this.label,
    required this.value,
  });

  final Color color;
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Container(
          width: 14,
          height: 14,
          decoration: BoxDecoration(
            color: color,
            shape: BoxShape.circle,
            boxShadow: [
              BoxShadow(color: color.withValues(alpha: 0.4), blurRadius: 8),
            ],
          ),
        ),
        const SizedBox(width: 10),
        SizedBox(
          width: 58,
          child: Text(
            label,
            style: const TextStyle(fontWeight: FontWeight.w700),
          ),
        ),
        Expanded(
          child: Text(
            value,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(color: mutedText),
          ),
        ),
      ],
    );
  }
}

class StatusPill extends StatelessWidget {
  const StatusPill({
    super.key,
    required this.label,
    required this.value,
    required this.tone,
  });

  final String label;
  final String value;
  final PillTone tone;

  @override
  Widget build(BuildContext context) {
    final color = toneColor(tone);
    return Container(
      constraints: const BoxConstraints(minHeight: 34),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: color.withValues(alpha: 0.28)),
      ),
      child: Text(
        label.isEmpty ? value : '$label: $value',
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(
          color: color,
          fontWeight: FontWeight.w800,
          fontSize: 12,
        ),
      ),
    );
  }
}

class BiospurBackground extends StatelessWidget {
  const BiospurBackground({super.key});

  @override
  Widget build(BuildContext context) {
    return Stack(
      fit: StackFit.expand,
      children: [
        Image.asset(
          'assets/images/biospur_lime_background.png',
          fit: BoxFit.cover,
          alignment: Alignment.center,
        ),
        Container(
          decoration: const BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              colors: [Color(0x66050806), Color(0x88050806), Color(0xAA050806)],
            ),
          ),
        ),
      ],
    );
  }
}

class ParsedBleLine {
  const ParsedBleLine({this.bootVersion, this.summary, this.peer});

  final String? bootVersion;
  final BleSummary? summary;
  final BlePeer? peer;

  static ParsedBleLine? parse(String line) {
    if (line.startsWith('BL;')) {
      final match = RegExp(r'version=([^;]+)').firstMatch(line);
      if (match == null) return const ParsedBleLine();
      return ParsedBleLine(bootVersion: match.group(1));
    }
    if (line.startsWith('BSTAT;')) {
      return ParsedBleLine(summary: BleSummary.parse(line));
    }
    if (line.startsWith('BADV;')) {
      final peer = BlePeer.tryParse(line);
      if (peer == null) return null;
      return ParsedBleLine(peer: peer);
    }
    return null;
  }
}

class BleSummary {
  const BleSummary({
    required this.uptimeMs,
    required this.tags,
    required this.anchors,
    required this.dfu,
    required this.unknown,
    required this.total,
    required this.stale,
    required this.adv,
    required this.printed,
    required this.scan,
  });

  final int uptimeMs;
  final int tags;
  final int anchors;
  final int dfu;
  final int unknown;
  final int total;
  final int stale;
  final int adv;
  final int printed;
  final bool scan;

  factory BleSummary.empty() => const BleSummary(
    uptimeMs: 0,
    tags: 0,
    anchors: 0,
    dfu: 0,
    unknown: 0,
    total: 0,
    stale: 0,
    adv: 0,
    printed: 0,
    scan: false,
  );

  factory BleSummary.parse(String line) {
    final parts = line.split(';');
    final values = <String, String>{};
    for (final part in parts.skip(3)) {
      final idx = part.indexOf('=');
      if (idx <= 0) continue;
      values[part.substring(0, idx)] = part.substring(idx + 1);
    }
    int read(String key) => int.tryParse(values[key] ?? '') ?? 0;
    return BleSummary(
      uptimeMs: parts.length > 2 ? int.tryParse(parts[2]) ?? 0 : 0,
      tags: read('tags'),
      anchors: read('anchors'),
      dfu: read('dfu'),
      unknown: read('unknown'),
      total: read('total'),
      stale: read('stale'),
      adv: read('adv'),
      printed: read('printed'),
      scan: read('scan') == 1,
    );
  }
}

class BlePeer {
  const BlePeer({
    required this.uptimeMs,
    required this.addr,
    required this.rssi,
    required this.kind,
    required this.name,
    required this.id,
    required this.role,
    required this.uuid,
    required this.dfu,
    required this.hostSeenAt,
  });

  final int uptimeMs;
  final String addr;
  final int rssi;
  final String kind;
  final String name;
  final String id;
  final String role;
  final String uuid;
  final bool dfu;
  final DateTime hostSeenAt;

  bool get isFresh => DateTime.now().difference(hostSeenAt).inSeconds < 6;

  BlePeer copyWith({DateTime? hostSeenAt}) => BlePeer(
    uptimeMs: uptimeMs,
    addr: addr,
    rssi: rssi,
    kind: kind,
    name: name,
    id: id,
    role: role,
    uuid: uuid,
    dfu: dfu,
    hostSeenAt: hostSeenAt ?? this.hostSeenAt,
  );

  factory BlePeer.parse(String line) {
    final peer = BlePeer.tryParse(line);
    if (peer == null) {
      throw const FormatException('invalid BADV line');
    }
    return peer;
  }

  static BlePeer? tryParse(String line) {
    final parts = line.split(';');
    if (parts.length != 11 || parts[0] != 'BADV' || parts[1] != '1') {
      return null;
    }
    String field(int idx, [String fallback = '-']) =>
        parts.length > idx && parts[idx].isNotEmpty ? parts[idx] : fallback;
    final uptimeMs = int.tryParse(field(2, '0'));
    final addr = field(3);
    final rssi = int.tryParse(field(4));
    final kind = field(5);
    final dfuRaw = field(10, '0');

    if (uptimeMs == null ||
        rssi == null ||
        rssi < -127 ||
        rssi > 20 ||
        !isValidBleAddress(addr) ||
        !isValidBleKind(kind) ||
        (dfuRaw != '0' && dfuRaw != '1')) {
      return null;
    }

    return BlePeer(
      uptimeMs: uptimeMs,
      addr: addr,
      rssi: rssi,
      kind: kind,
      name: field(6),
      id: field(7),
      role: field(8),
      uuid: field(9),
      dfu: dfuRaw == '1',
      hostSeenAt: DateTime.now(),
    );
  }
}

bool isValidBleKind(String value) {
  return value == 'TAG' ||
      value == 'ANCHOR' ||
      value == 'DFU' ||
      value == 'UNKNOWN';
}

bool isValidBleAddress(String value) {
  return RegExp(
    r'^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}( \([^)]+\))?$',
  ).hasMatch(value);
}

enum PillTone { neutral, good, warn, bad, active }

Color toneColor(PillTone tone) {
  switch (tone) {
    case PillTone.good:
      return controlGreen;
    case PillTone.warn:
      return const Color(0xFFE7C55A);
    case PillTone.bad:
      return const Color(0xFFDC2626);
    case PillTone.active:
      return biospurGlow;
    case PillTone.neutral:
      return const Color(0xFF7A8F65);
  }
}

String compactPath(String path) {
  if (path.isEmpty) return 'auto';
  final parts = path.split('/');
  return parts.isEmpty ? path : parts.last;
}

void unawaited(Future<void> future) {}

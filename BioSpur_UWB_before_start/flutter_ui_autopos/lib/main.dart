import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

void main() {
  runApp(const AutoPosFieldApp());
}

const repoRoot = '/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start';
const fieldRoot = '$repoRoot/autopos_pipeline/erlangen_20260528_mocap';
const defaultWorkspaceRoot = fieldRoot;
const captureSession = 'erlangen_20260528_optitrack';
const aliases = '$fieldRoot/tools/erlangen_aliases.sh';
const experimentPlanShort = '$fieldRoot/docs/experiment_plan_short.md';
const anchors = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];
const freeCaptureTags = [
  'BSF66F',
  'BS2DCE',
  'BSDC91',
  'BS9336',
  'BS955A',
  'BSCCF4',
];
const captureTargetsByKind = {
  'static': ['BSF66F'],
  'roto': ['BS2DCE', 'BSDC91'],
  'wand': ['BS9336', 'BS955A', 'BSCCF4'],
};
String activeWorkspaceRoot = defaultWorkspaceRoot;
String get capturesRoot => '$activeWorkspaceRoot/captures/$captureSession';
String get activeCaptureRoot => '$activeWorkspaceRoot/captures';
String get activeSolverRoot => '$activeWorkspaceRoot/solver';
String get activeStagedDataset =>
    '$activeWorkspaceRoot/solver/work/field_dataset_staged';
String get activeSolverOutputs => '$activeWorkspaceRoot/solver/outputs';
String get workspaceExport =>
    'export BIOSPUR_CAPTURE_ROOT=${shellQuote(activeCaptureRoot)}';
String get workspaceSetup =>
    'cd $repoRoot && source $aliases && $workspaceExport && bio_setup $captureSession';
String get settingsPath {
  final home = Platform.environment['HOME'] ?? '/tmp';
  return '$home/.config/biospur-autopos/settings.json';
}

const biospurGreen = Color(0xFFA3E635);
const biospurGlow = Color(0xFFD9FC05);
const controlGreen = Color(0xFFB9D98F);
const biospurBlack = Color(0xFF050806);
const panelLine = Color(0x33638A01);
const tableLine = Color(0xAA638A01);
const mutedText = Color(0xFFB6C7B3);

class AutoPosFieldApp extends StatelessWidget {
  const AutoPosFieldApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'BioSpur AutoPos Field',
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
      home: const FieldConsolePage(),
    );
  }
}

class FieldConsolePage extends StatefulWidget {
  const FieldConsolePage({super.key});

  @override
  State<FieldConsolePage> createState() => _FieldConsolePageState();
}

class _FieldConsolePageState extends State<FieldConsolePage>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs;
  late final Timer _timer;
  final ScriptRunner _runner = ScriptRunner();
  late final TextEditingController _workspaceController;
  SweepSnapshot _sweep = SweepSnapshot.empty();
  PortSnapshot _ports = PortSnapshot.empty();
  Timer? _splashTimer;
  bool _showSplash = true;
  bool _sweepLoadingEnabled = false;
  DateTime? _sweepReadSince;
  bool _refreshInFlight = false;
  String? _selectedSolverLayoutPath;
  SolverSweepInfo? _selectedSolverSweep;
  String _selectedSolverMode = 'v1-v4';

  @override
  void initState() {
    super.initState();
    _workspaceController = TextEditingController(text: activeWorkspaceRoot);
    _tabs = TabController(length: 3, vsync: this);
    _loadWorkspaceSetting();
    _refreshAll();
    _runner.addListener(_refreshAll);
    _timer = Timer.periodic(const Duration(seconds: 1), (_) => _refreshAll());
    _splashTimer = Timer(const Duration(seconds: 3), () {
      if (!mounted) return;
      setState(() => _showSplash = false);
    });
  }

  Future<void> _loadWorkspaceSetting() async {
    final file = File(settingsPath);
    if (!file.existsSync()) return;
    try {
      final decoded = jsonDecode(await file.readAsString());
      if (decoded is! Map<String, dynamic>) return;
      final path = decoded['workspace_root']?.toString();
      if (path == null || path.isEmpty) return;
      if (!mounted) return;
      setState(() {
        activeWorkspaceRoot = path;
        _workspaceController.text = path;
      });
      await _refreshAll();
    } catch (_) {
      // Ignore malformed local UI settings and keep the repo default workspace.
    }
  }

  @override
  void dispose() {
    _timer.cancel();
    _splashTimer?.cancel();
    _workspaceController.dispose();
    _runner.removeListener(_refreshAll);
    _runner.dispose();
    _tabs.dispose();
    super.dispose();
  }

  Future<void> _applyWorkspace() async {
    final raw = _workspaceController.text.trim();
    if (raw.isEmpty) {
      await showBioSpurNotice(
        context,
        title: 'Workspace path is empty',
        message: 'Set a data workspace directory before applying.',
      );
      return;
    }
    final expanded = raw.startsWith('~/')
        ? '${Platform.environment['HOME'] ?? ''}/${raw.substring(2)}'
        : raw;
    final dir = Directory(expanded);
    await dir.create(recursive: true);
    await Directory(
      '$expanded/captures/$captureSession',
    ).create(recursive: true);
    await Directory('$expanded/solver/work').create(recursive: true);
    await Directory('$expanded/solver/outputs').create(recursive: true);
    await Directory('$expanded/exports').create(recursive: true);
    await Directory('$expanded/logs').create(recursive: true);
    await File('$expanded/workspace.json').writeAsString(
      const JsonEncoder.withIndent('  ').convert({
        'workspace_name': expanded.split(Platform.pathSeparator).last,
        'repo_root': repoRoot,
        'field_root': fieldRoot,
        'capture_session': captureSession,
        'schema': 1,
        'updated_at': DateTime.now().toIso8601String(),
      }),
      encoding: utf8,
    );
    final settings = File(settingsPath);
    await settings.parent.create(recursive: true);
    await settings.writeAsString(
      const JsonEncoder.withIndent('  ').convert({
        'workspace_root': expanded,
        'updated_at': DateTime.now().toIso8601String(),
      }),
      encoding: utf8,
    );
    if (!mounted) return;
    setState(() {
      activeWorkspaceRoot = expanded;
      _sweep = SweepSnapshot.empty();
      _sweepLoadingEnabled = false;
      _sweepReadSince = null;
      _selectedSolverLayoutPath = null;
      _selectedSolverSweep = null;
      _selectedSolverMode = 'v1-v4';
    });
    await showBioSpurNotice(
      context,
      title: 'Workspace activated',
      message:
          'Active data workspace is now:\n\n$expanded\n\nNew captures, US measurements, staged solver data, solver outputs, logs, and exports will be written under this folder.',
    );
  }

  Future<void> _browseWorkspace() async {
    final home = Platform.environment['HOME'] ?? '/tmp';
    final initialPath = Directory(_workspaceController.text).existsSync()
        ? _workspaceController.text
        : '$home/Desktop';
    final result = await Process.run('zenity', [
      '--file-selection',
      '--directory',
      '--title=Select BioSpur data workspace',
      '--filename=$initialPath/',
    ]);
    if (result.exitCode == 0) {
      final selected = result.stdout.toString().trim();
      if (selected.isNotEmpty) {
        _workspaceController.text = selected;
      }
      return;
    }
    if (result.exitCode != 1 && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            'Folder picker failed. Type the workspace path manually. ${result.stderr}',
          ),
        ),
      );
    }
  }

  Future<void> _refreshAll() async {
    if (_refreshInFlight) return;
    _refreshInFlight = true;
    try {
      final ports = await PortReader.read();
      final sweep = _sweepLoadingEnabled
          ? await SweepReader.readLatest(minModified: _sweepReadSince)
          : SweepSnapshot.empty();
      if (!mounted) return;
      setState(() {
        _sweep = sweep;
        _ports = ports;
      });
    } finally {
      _refreshInFlight = false;
    }
  }

  void _enableSweepLoading({bool clear = false, DateTime? minModified}) {
    setState(() {
      _sweepLoadingEnabled = true;
      _sweepReadSince = minModified;
      if (clear) {
        _sweep = SweepSnapshot.empty();
      }
    });
  }

  Future<void> _clearExperimentData() async {
    if (_runner.isRunning) {
      await showBioSpurNotice(
        context,
        title: 'Runner is busy',
        message: 'Stop the current command before clearing experiment data.',
      );
      return;
    }
    final confirmed = await showBioSpurConfirm(
      context,
      title: 'Clear experiment data?',
      message:
          'This will delete current capture folders, session_notes.csv, solver outputs, staged solver work, and logs in the active data workspace.\n\nIt will not delete scripts, docs, tools, README, or archived folders.',
      confirmLabel: 'Clear Data',
    );
    if (!mounted || !confirmed) return;
    setState(() {
      _sweep = SweepSnapshot.empty();
      _sweepLoadingEnabled = false;
      _sweepReadSince = null;
      _selectedSolverLayoutPath = null;
      _selectedSolverSweep = null;
      _selectedSolverMode = 'v1-v4';
    });
    await _runner.start('Clear experiment data', '''
set -euo pipefail
WORK=${shellQuote(activeWorkspaceRoot)}
CAP=${shellQuote(capturesRoot)}
echo "[clear] captures: \$CAP"
mkdir -p "\$CAP"
find "\$CAP" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
echo "[clear] solver outputs"
mkdir -p "\$WORK/solver/outputs"
find "\$WORK/solver/outputs" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
echo "[clear] staged solver work"
rm -rf "\$WORK/solver/work/field_dataset_staged"
mkdir -p "\$WORK/solver/work"
echo "[clear] logs"
mkdir -p "\$WORK/logs"
find "\$WORK/logs" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
echo "[clear] done"
''');
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: biospurBlack,
      body: Stack(
        children: [
          const Positioned.fill(child: BiospurBackground()),
          Column(
            children: [
              FieldHeader(
                ports: _ports,
                sweep: _sweep,
                runner: _runner,
                workspaceController: _workspaceController,
                onApplyWorkspace: _applyWorkspace,
                onBrowseWorkspace: _browseWorkspace,
              ),
              AnchorStatusBar(ports: _ports, sweep: _sweep, runner: _runner),
              Material(
                color: Colors.transparent,
                child: TabBar(
                  controller: _tabs,
                  tabs: [
                    Tab(
                      icon: Image.asset(
                        'assets/images/biospur_logo.png',
                        width: 28,
                        height: 28,
                        filterQuality: FilterQuality.high,
                      ),
                      text: 'AutoPos Sweep',
                    ),
                    const Tab(
                      icon: Icon(Icons.analytics_outlined),
                      text: 'Anchor Layout Analysis',
                    ),
                    const Tab(
                      icon: Icon(Icons.sensors_outlined),
                      text: 'Static / Roto / Wand Capture',
                    ),
                  ],
                ),
              ),
              Expanded(
                child: TabBarView(
                  controller: _tabs,
                  children: [
                    AutoPosSweepTab(
                      sweep: _sweep,
                      ports: _ports,
                      runner: _runner,
                      onRefresh: _refreshAll,
                      onEnableSweepLoading: _enableSweepLoading,
                      onClearExperimentData: _clearExperimentData,
                    ),
                    AnchorLayoutTab(
                      runner: _runner,
                      sweep: _sweep,
                      ports: _ports,
                      selectedLayoutPath: _selectedSolverLayoutPath,
                      selectedSweep: _selectedSolverSweep,
                      selectedSolverMode: _selectedSolverMode,
                      onSelectedLayoutPathChanged: (path) {
                        setState(() {
                          _selectedSolverLayoutPath = path;
                        });
                      },
                      onSelectedSweepChanged: (sweep) {
                        setState(() {
                          _selectedSolverSweep = sweep;
                          _selectedSolverLayoutPath = null;
                        });
                      },
                      onSelectedSolverModeChanged: (mode) {
                        setState(() {
                          _selectedSolverMode = mode;
                          _selectedSolverLayoutPath = null;
                        });
                      },
                    ),
                    CaptureTab(
                      runner: _runner,
                      ports: _ports,
                      selectedLayoutPath: _selectedSolverLayoutPath,
                      selectedSweep: _selectedSolverSweep,
                      selectedSolverMode: _selectedSolverMode,
                      onSelectedLayoutPathChanged: (path) {
                        setState(() {
                          _selectedSolverLayoutPath = path;
                        });
                      },
                      onSelectedSweepChanged: (sweep) {
                        setState(() {
                          _selectedSolverSweep = sweep;
                          _selectedSolverLayoutPath = null;
                        });
                      },
                    ),
                  ],
                ),
              ),
              RunnerLogCard(runner: _runner),
            ],
          ),
          if (_showSplash) const Positioned.fill(child: SplashScreen()),
        ],
      ),
    );
  }
}

class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Material(
      color: biospurBlack,
      child: Stack(
        fit: StackFit.expand,
        children: [
          Image.asset(
            'assets/images/biospur_splash.png',
            fit: BoxFit.cover,
            alignment: Alignment.center,
            filterQuality: FilterQuality.high,
          ),
          Container(
            decoration: const BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [
                  Color(0x11050806),
                  Color(0x00050806),
                  Color(0x66050806),
                ],
              ),
            ),
          ),
          Align(
            alignment: Alignment.bottomCenter,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(56, 0, 56, 54),
              child: TweenAnimationBuilder<double>(
                tween: Tween(begin: 0, end: 1),
                duration: const Duration(seconds: 3),
                builder: (context, value, _) {
                  return ClipRRect(
                    borderRadius: BorderRadius.circular(999),
                    child: LinearProgressIndicator(
                      value: value,
                      minHeight: 8,
                      backgroundColor: const Color(0x55384515),
                      valueColor: const AlwaysStoppedAnimation<Color>(
                        controlGreen,
                      ),
                    ),
                  );
                },
              ),
            ),
          ),
        ],
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
              colors: [Color(0x55050806), Color(0x77050806), Color(0x99050806)],
            ),
          ),
        ),
      ],
    );
  }
}

class FieldHeader extends StatelessWidget {
  const FieldHeader({
    super.key,
    required this.ports,
    required this.sweep,
    required this.runner,
    required this.workspaceController,
    required this.onApplyWorkspace,
    required this.onBrowseWorkspace,
  });

  final PortSnapshot ports;
  final SweepSnapshot sweep;
  final ScriptRunner runner;
  final TextEditingController workspaceController;
  final VoidCallback onApplyWorkspace;
  final VoidCallback onBrowseWorkspace;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: const BoxDecoration(
        border: Border(bottom: BorderSide(color: panelLine)),
      ),
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
      child: SafeArea(
        bottom: false,
        child: Row(
          children: [
            Image.asset(
              'assets/images/biospur_logo.png',
              width: 34,
              height: 34,
              filterQuality: FilterQuality.high,
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'BioSpur AutoPos',
                    style: TextStyle(fontSize: 20, fontWeight: FontWeight.w700),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    'Session $captureSession  |  ${sweep.displayName}',
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: mutedText),
                  ),
                  const SizedBox(height: 8),
                  LayoutBuilder(
                    builder: (context, constraints) {
                      final fieldWidth = math.min(constraints.maxWidth, 520.0);
                      return Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        crossAxisAlignment: WrapCrossAlignment.center,
                        children: [
                          SizedBox(
                            width: fieldWidth,
                            child: TextField(
                              controller: workspaceController,
                              enabled: !runner.isRunning,
                              style: const TextStyle(
                                fontSize: 12,
                                fontFamily: 'monospace',
                              ),
                              decoration: const InputDecoration(
                                labelText: 'Data workspace',
                                isDense: true,
                                contentPadding: EdgeInsets.symmetric(
                                  horizontal: 10,
                                  vertical: 8,
                                ),
                              ),
                            ),
                          ),
                          OutlinedButton.icon(
                            onPressed: runner.isRunning
                                ? null
                                : onBrowseWorkspace,
                            icon: const Icon(Icons.folder_outlined),
                            label: const Text('Browse'),
                          ),
                          const SizedBox(width: 8),
                          FilledButton.icon(
                            onPressed: runner.isRunning
                                ? null
                                : onApplyWorkspace,
                            icon: const Icon(Icons.folder_open_outlined),
                            label: const Text('Use'),
                          ),
                        ],
                      );
                    },
                  ),
                ],
              ),
            ),
            StatusPill(
              label: 'Anchor CDC',
              value: ports.masterAnchor ? 'OK' : 'missing',
              tone: ports.masterAnchor ? PillTone.good : PillTone.bad,
            ),
            const SizedBox(width: 8),
            StatusPill(
              label: 'Tag CDC',
              value: ports.masterTag ? 'OK' : 'missing',
              tone: ports.masterTag ? PillTone.good : PillTone.bad,
            ),
            const SizedBox(width: 8),
            StatusPill(
              label: 'Runner',
              value: runner.isRunning ? 'running' : 'idle',
              tone: runner.isRunning ? PillTone.active : PillTone.neutral,
            ),
          ],
        ),
      ),
    );
  }
}

class AnchorStatusBar extends StatelessWidget {
  const AnchorStatusBar({
    super.key,
    required this.ports,
    required this.sweep,
    required this.runner,
  });

  final PortSnapshot ports;
  final SweepSnapshot sweep;
  final ScriptRunner runner;

  @override
  Widget build(BuildContext context) {
    final anchorOnline = ports.masterAnchor;
    final showLiveSweepRoles =
        anchorOnline &&
        runner.isRunning &&
        runner.activeName?.startsWith('Sweep ') == true;
    final currentAnchor = showLiveSweepRoles ? sweep.currentAnchor : null;
    return Container(
      decoration: const BoxDecoration(
        border: Border(bottom: BorderSide(color: panelLine)),
      ),
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 10),
      child: Column(
        children: [
          LayoutBuilder(
            builder: (context, constraints) {
              final compact = constraints.maxWidth < 980;
              return Wrap(
                spacing: compact ? 6 : 10,
                runSpacing: 8,
                children: anchors.map((anchor) {
                  return AnchorChip(
                    label: anchor,
                    online: anchorOnline,
                    masterOn: showLiveSweepRoles && currentAnchor == anchor,
                    matrixActive:
                        showLiveSweepRoles &&
                        currentAnchor != null &&
                        currentAnchor != anchor,
                    responderOn: !showLiveSweepRoles && sweep.finalResponderOk,
                  );
                }).toList(),
              );
            },
          ),
        ],
      ),
    );
  }
}

class AnchorChip extends StatelessWidget {
  const AnchorChip({
    super.key,
    required this.label,
    required this.online,
    required this.masterOn,
    required this.matrixActive,
    required this.responderOn,
  });

  final String label;
  final bool online;
  final bool masterOn;
  final bool matrixActive;
  final bool responderOn;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 112,
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 7),
      decoration: BoxDecoration(
        border: Border.all(color: online ? panelLine : const Color(0x33585F57)),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        children: [
          SizedBox(
            width: 18,
            child: Text(
              label,
              style: TextStyle(
                color: online ? Colors.white : const Color(0xFF6B7268),
                fontWeight: FontWeight.w800,
              ),
            ),
          ),
          RoleDot(
            label: 'M',
            color: controlGreen,
            on: online && masterOn,
            muted: !online,
          ),
          const SizedBox(width: 5),
          RoleDot(
            label: 'X',
            color: const Color(0xFF2F80FF),
            on: online && matrixActive,
            flash: online && matrixActive,
            muted: !online,
          ),
          const SizedBox(width: 5),
          RoleDot(
            label: 'R',
            color: const Color(0xFF2F80FF),
            on: online && responderOn,
            muted: !online,
          ),
        ],
      ),
    );
  }
}

class RoleDot extends StatefulWidget {
  const RoleDot({
    super.key,
    required this.label,
    required this.color,
    required this.on,
    this.flash = false,
    this.muted = false,
  });

  final String label;
  final Color color;
  final bool on;
  final bool flash;
  final bool muted;

  @override
  State<RoleDot> createState() => _RoleDotState();
}

class _RoleDotState extends State<RoleDot> with SingleTickerProviderStateMixin {
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 450),
    )..repeat(reverse: true);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) {
        final opacity = !widget.on
            ? widget.muted
                  ? 0.10
                  : 0.16
            : widget.flash
            ? (_controller.value < 0.5 ? 0.18 : 1.0)
            : 1.0;
        final color = widget.muted ? const Color(0xFF6B7268) : widget.color;
        return Tooltip(
          message: widget.label,
          child: Container(
            width: 22,
            height: 22,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: color.withValues(alpha: opacity),
              shape: BoxShape.circle,
            ),
            child: Text(
              widget.label,
              style: const TextStyle(
                color: Colors.white,
                fontSize: 11,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
        );
      },
    );
  }
}

class AutoPosSweepTab extends StatefulWidget {
  const AutoPosSweepTab({
    super.key,
    required this.sweep,
    required this.ports,
    required this.runner,
    required this.onRefresh,
    required this.onEnableSweepLoading,
    required this.onClearExperimentData,
  });

  final SweepSnapshot sweep;
  final PortSnapshot ports;
  final ScriptRunner runner;
  final Future<void> Function() onRefresh;
  final void Function({bool clear, DateTime? minModified}) onEnableSweepLoading;
  final Future<void> Function() onClearExperimentData;

  @override
  State<AutoPosSweepTab> createState() => _AutoPosSweepTabState();
}

class _AutoPosSweepTabState extends State<AutoPosSweepTab> {
  final TextEditingController _idController = TextEditingController(
    text: 'SW01',
  );
  final TextEditingController _setsController = TextEditingController(
    text: '1000',
  );
  final TextEditingController _prewarmController = TextEditingController(
    text: '10',
  );

  @override
  void dispose() {
    _idController.dispose();
    _setsController.dispose();
    _prewarmController.dispose();
    super.dispose();
  }

  Future<void> _runShell(String name, String command) async {
    await widget.runner.start(name, command);
    await widget.onRefresh();
  }

  Future<void> _connect() async {
    await _runShell(
      'Refresh ports',
      'cd $repoRoot && source $aliases && bio_ports',
    );
    final ports = widget.ports.masterTag
        ? widget.ports
        : await PortReader.read();
    if (!mounted) return;
    if (!ports.anyMaster) {
      await showBioSpurNotice(
        context,
        title: 'Device not connected',
        message:
            'No Master_Anchor or Master_Tag CDC port is visible under /dev/serial/by-id.',
      );
    }
  }

  Future<void> _usbPowerOn() async {
    final ports = await PortReader.read();
    if (!mounted) return;
    if (!ports.anyMaster) {
      await showBioSpurNotice(
        context,
        title: 'Device not connected',
        message:
            'USB power setup needs a visible BioSpur CDC or J-Link serial device.',
      );
      return;
    }
    await _runShell(
      'USB power on',
      'cd $repoRoot && source $aliases && bio_usb_on',
    );
  }

  Future<void> _allAnchorResponder() async {
    final ports = await PortReader.read();
    if (!mounted) return;
    if (!ports.masterAnchor) {
      await showBioSpurNotice(
        context,
        title: 'Master Anchor not connected',
        message:
            'All-responder setup requires the Master_Anchor CDC port. Connect the Master Anchor board, then press Connect again.',
      );
      return;
    }
    await _runShell(
      'All anchors responder',
      '$_baseSource && bio_all_anchor_responder',
    );
  }

  Future<void> _systemReset() async {
    await _runShell('System reset', '$_baseSource && bio_reset_masters');
  }

  Future<void> _startSweep() async {
    final ports = await PortReader.read();
    if (!mounted) return;
    if (!ports.masterAnchor) {
      await showBioSpurNotice(
        context,
        title: 'Master Anchor not connected',
        message:
            'AutoPos sweep requires the Master_Anchor CDC port. Connect the Master Anchor board, then press Connect again.',
      );
      return;
    }
    final id = _idController.text.trim().isEmpty
        ? 'SW01'
        : _idController.text.trim();
    final swSets = int.tryParse(_setsController.text.trim()) ?? 1000;
    final prewarm = int.tryParse(_prewarmController.text.trim()) ?? 10;
    if (swSets <= 0 || prewarm < 0) {
      await showBioSpurNotice(
        context,
        title: 'Invalid sweep parameters',
        message: 'SW sets must be > 0 and prewarm must be >= 0.',
      );
      return;
    }
    widget.onEnableSweepLoading(
      clear: true,
      minModified: DateTime.now().subtract(const Duration(seconds: 2)),
    );
    await _runShell(
      'Sweep $id',
      '$_baseSource && sweep -id ${shellQuote(id)} -n $swSets -p $prewarm',
    );
  }

  String get _baseSource => workspaceSetup;

  @override
  Widget build(BuildContext context) {
    final sweep = widget.sweep;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        ControlPanel(
          runner: widget.runner,
          idController: _idController,
          setsController: _setsController,
          prewarmController: _prewarmController,
          onRefreshPorts: _connect,
          onUsbPower: _usbPowerOn,
          onAllAnchorResponder: _allAnchorResponder,
          onSystemReset: _systemReset,
          onStartSweep: _startSweep,
          onReadLatest: () async {
            widget.onEnableSweepLoading();
            await widget.onRefresh();
          },
          onClearExperimentData: widget.onClearExperimentData,
        ),
        const SizedBox(height: 14),
        SweepOverviewCard(sweep: sweep),
        const SizedBox(height: 14),
        LiveSwRowCard(sweep: sweep),
        const SizedBox(height: 14),
        RoundStatusTable(sweep: sweep),
      ],
    );
  }
}

class ControlPanel extends StatelessWidget {
  const ControlPanel({
    super.key,
    required this.runner,
    required this.idController,
    required this.setsController,
    required this.prewarmController,
    required this.onRefreshPorts,
    required this.onUsbPower,
    required this.onAllAnchorResponder,
    required this.onSystemReset,
    required this.onStartSweep,
    required this.onReadLatest,
    required this.onClearExperimentData,
  });

  final ScriptRunner runner;
  final TextEditingController idController;
  final TextEditingController setsController;
  final TextEditingController prewarmController;
  final Future<void> Function() onRefreshPorts;
  final Future<void> Function() onUsbPower;
  final Future<void> Function() onAllAnchorResponder;
  final Future<void> Function() onSystemReset;
  final Future<void> Function() onStartSweep;
  final Future<void> Function() onReadLatest;
  final Future<void> Function() onClearExperimentData;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Wrap(
              spacing: 8,
              runSpacing: 10,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                FilledButton.icon(
                  onPressed: runner.isRunning ? null : onRefreshPorts,
                  icon: const Icon(Icons.cable_outlined),
                  label: const Text('Connect'),
                ),
                OutlinedButton.icon(
                  onPressed: runner.isRunning ? null : onUsbPower,
                  icon: const Icon(Icons.power_settings_new),
                  label: const Text('USB On'),
                ),
                TextButton.icon(
                  onPressed: runner.isRunning ? null : onReadLatest,
                  icon: const Icon(Icons.history),
                  label: const Text('Read Latest'),
                ),
                OutlinedButton.icon(
                  onPressed: runner.isRunning ? null : onAllAnchorResponder,
                  icon: const Icon(Icons.sensors),
                  label: const Text('All Responder'),
                ),
                OutlinedButton.icon(
                  onPressed: runner.isRunning ? null : onSystemReset,
                  icon: const Icon(Icons.restart_alt),
                  label: const Text('System Reset'),
                ),
                OutlinedButton.icon(
                  onPressed: runner.isRunning ? null : onClearExperimentData,
                  style: OutlinedButton.styleFrom(
                    foregroundColor: toneColor(PillTone.bad),
                    side: BorderSide(color: toneColor(PillTone.bad)),
                  ),
                  icon: const Icon(Icons.delete_forever_outlined),
                  label: const Text('Clear Data'),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 10,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                SizedBox(
                  width: 150,
                  child: TextField(
                    controller: idController,
                    decoration: const InputDecoration(
                      labelText: 'Sweep ID',
                      border: OutlineInputBorder(),
                      isDense: true,
                    ),
                  ),
                ),
                SizedBox(
                  width: 120,
                  child: TextField(
                    controller: setsController,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(
                      labelText: 'SW sets',
                      border: OutlineInputBorder(),
                      isDense: true,
                    ),
                  ),
                ),
                SizedBox(
                  width: 120,
                  child: TextField(
                    controller: prewarmController,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(
                      labelText: 'Prewarm',
                      border: OutlineInputBorder(),
                      isDense: true,
                    ),
                  ),
                ),
                FilledButton.icon(
                  onPressed: runner.isRunning ? null : onStartSweep,
                  icon: const Icon(Icons.play_arrow),
                  label: const Text('Start Sweep'),
                ),
                OutlinedButton.icon(
                  onPressed: runner.isRunning ? runner.stop : null,
                  icon: const Icon(Icons.stop),
                  label: const Text('Stop'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class SweepOverviewCard extends StatelessWidget {
  const SweepOverviewCard({super.key, required this.sweep});

  final SweepSnapshot sweep;

  @override
  Widget build(BuildContext context) {
    final usable = sweep.complete;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Text(
                  'Sweep Progress',
                  style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700),
                ),
                const Spacer(),
                StatusPill(
                  label: 'Complete',
                  value: usable ? 'YES' : 'NO',
                  tone: usable ? PillTone.good : PillTone.warn,
                ),
                const SizedBox(width: 8),
                StatusPill(
                  label: 'Solver',
                  value: usable ? 'usable' : 'blocked',
                  tone: usable ? PillTone.good : PillTone.bad,
                ),
              ],
            ),
            const SizedBox(height: 12),
            ClipRRect(
              borderRadius: BorderRadius.circular(999),
              child: LinearProgressIndicator(
                value: sweep.overallProgress,
                minHeight: 10,
                backgroundColor: const Color(0x55384515),
              ),
            ),
            const SizedBox(height: 10),
            Text(
              '${sweep.totalSwCount} / ${sweep.targetTotal} sets  |  ${percent(sweep.overallProgress)}  |  current ${sweep.currentMaster ?? '-'}',
            ),
            const SizedBox(height: 4),
            Text(
              sweep.directory ?? 'No sweep folder detected yet.',
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 12, color: mutedText),
            ),
          ],
        ),
      ),
    );
  }
}

class LiveSwRowCard extends StatelessWidget {
  const LiveSwRowCard({super.key, required this.sweep});

  final SweepSnapshot sweep;

  @override
  Widget build(BuildContext context) {
    final row = sweep.currentRow;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Text(
                  'Live SW Row',
                  style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700),
                ),
                const Spacer(),
                Text(
                  row == null
                      ? 'waiting'
                      : '${row.master}  ${row.setIndex} / ${sweep.targetSets}',
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
              ],
            ),
            const SizedBox(height: 12),
            LiveRowGrid(row: row),
          ],
        ),
      ),
    );
  }
}

class LiveRowGrid extends StatelessWidget {
  const LiveRowGrid({super.key, required this.row});

  final LiveSwRow? row;

  @override
  Widget build(BuildContext context) {
    final master = row?.master.replaceFirst('SW-', '');
    return Container(
      width: double.infinity,
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        color: const Color(0xFF050806),
        border: Border.all(color: tableLine),
        borderRadius: BorderRadius.circular(8),
      ),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Column(
          children: [
            Row(
              children: [
                const MatrixCell(text: '', header: true, width: 84),
                ...anchors.map((a) => MatrixCell(text: a, header: true)),
              ],
            ),
            Row(
              children: [
                MatrixCell(text: row?.master ?? 'SW-', header: true, width: 84),
                ...anchors.map((a) {
                  if (master == a) {
                    return const MatrixCell(text: '-', diagonal: true);
                  }
                  final sample = row?.values[a];
                  return MatrixCell(
                    text: sample == null ? '.' : sample.distanceMm.toString(),
                    subtitle: sample == null ? '' : 'q${sample.quality}',
                    tone: sampleTone(sample),
                    fresh: sample != null,
                  );
                }),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class MatrixCell extends StatelessWidget {
  const MatrixCell({
    super.key,
    required this.text,
    this.subtitle = '',
    this.header = false,
    this.diagonal = false,
    this.width = 104,
    this.tone = PillTone.neutral,
    this.fresh = false,
  });

  final String text;
  final String subtitle;
  final bool header;
  final bool diagonal;
  final double width;
  final PillTone tone;
  final bool fresh;

  @override
  Widget build(BuildContext context) {
    final Color textColor;
    if (fresh) {
      textColor = toneColor(tone);
    } else if (header) {
      textColor = controlGreen;
    } else {
      textColor = mutedText;
    }

    return Container(
      width: width,
      height: 62,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: _cellBackground,
        border: Border.all(color: _cellBorder, width: 0.5),
      ),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Text(
            text,
            style: TextStyle(
              fontFamily: 'monospace',
              fontSize: header ? 15 : 19,
              fontWeight: header || fresh ? FontWeight.w800 : FontWeight.w500,
              color: textColor,
            ),
          ),
          if (subtitle.isNotEmpty)
            Text(
              subtitle,
              style: TextStyle(
                fontFamily: 'monospace',
                fontSize: 11,
                color: toneColor(tone),
              ),
            ),
        ],
      ),
    );
  }

  Color get _cellBackground {
    if (diagonal) return const Color(0xFF101407);
    if (!fresh) return const Color(0xFF050806);
    switch (tone) {
      case PillTone.good:
        return const Color(0xFF050806);
      case PillTone.warn:
        return const Color(0xFF2B2608);
      case PillTone.bad:
        return const Color(0xFF2A0909);
      case PillTone.active:
      case PillTone.neutral:
        return const Color(0xFF050806);
    }
  }

  Color get _cellBorder {
    if (!fresh) return tableLine;
    switch (tone) {
      case PillTone.good:
        return tableLine;
      case PillTone.warn:
        return const Color(0xCCB9952A);
      case PillTone.bad:
        return const Color(0xCCDC2626);
      case PillTone.active:
      case PillTone.neutral:
        return tableLine;
    }
  }
}

class RoundStatusTable extends StatelessWidget {
  const RoundStatusTable({super.key, required this.sweep});

  final SweepSnapshot sweep;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'A-H Round Status',
              style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 10),
            Table(
              columnWidths: const {
                0: FixedColumnWidth(80),
                1: FixedColumnWidth(110),
                2: FixedColumnWidth(130),
                3: FixedColumnWidth(180),
                4: FixedColumnWidth(80),
              },
              defaultVerticalAlignment: TableCellVerticalAlignment.middle,
              children: [
                const TableRow(
                  decoration: BoxDecoration(color: Color(0x66242B12)),
                  children: [
                    TableText('Round', header: true),
                    TableText('State', header: true),
                    TableText('Sets', header: true),
                    TableText('Progress', header: true),
                    TableText('Min Q', header: true),
                  ],
                ),
                ...anchors.map((anchor) {
                  final round =
                      sweep.rounds[anchor] ?? RoundState.empty(anchor);
                  return TableRow(
                    children: [
                      TableText(anchor),
                      Padding(
                        padding: const EdgeInsets.all(7),
                        child: StatusPill(
                          label: '',
                          value: round.state.label,
                          tone: round.state.tone,
                        ),
                      ),
                      TableText('${round.swCount}/${sweep.targetSets}'),
                      Padding(
                        padding: const EdgeInsets.all(10),
                        child: LinearProgressIndicator(
                          value: round.progress(sweep.targetSets),
                        ),
                      ),
                      TableText(
                        round.minQuality == null ? '-' : '${round.minQuality}',
                      ),
                    ],
                  );
                }),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class TableText extends StatelessWidget {
  const TableText(this.text, {super.key, this.header = false});

  final String text;
  final bool header;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(10),
      child: Text(
        text,
        style: TextStyle(
          fontWeight: header ? FontWeight.w800 : FontWeight.w500,
        ),
      ),
    );
  }
}

class AnchorLayoutTab extends StatefulWidget {
  const AnchorLayoutTab({
    super.key,
    required this.runner,
    required this.sweep,
    required this.ports,
    required this.selectedLayoutPath,
    required this.selectedSweep,
    required this.selectedSolverMode,
    required this.onSelectedLayoutPathChanged,
    required this.onSelectedSweepChanged,
    required this.onSelectedSolverModeChanged,
  });

  final ScriptRunner runner;
  final SweepSnapshot sweep;
  final PortSnapshot ports;
  final String? selectedLayoutPath;
  final SolverSweepInfo? selectedSweep;
  final String selectedSolverMode;
  final ValueChanged<String?> onSelectedLayoutPathChanged;
  final ValueChanged<SolverSweepInfo?> onSelectedSweepChanged;
  final ValueChanged<String> onSelectedSolverModeChanged;

  @override
  State<AnchorLayoutTab> createState() => _AnchorLayoutTabState();
}

class _AnchorLayoutTabState extends State<AnchorLayoutTab> {
  final TextEditingController _usId = TextEditingController(text: 'US01');
  bool _usLoadingEnabled = false;
  bool _layoutLoadingEnabled = false;
  bool _solverLoadingEnabled = false;
  bool _runnerWasRunning = false;
  int _layoutRefresh = 0;
  int _solverRefresh = 0;
  late Future<List<SolverSweepInfo>> _sweepsFuture;

  @override
  void initState() {
    super.initState();
    _sweepsFuture = SolverSweepInfo.scan();
    widget.runner.addListener(_onRunnerChanged);
  }

  @override
  void dispose() {
    widget.runner.removeListener(_onRunnerChanged);
    _usId.dispose();
    super.dispose();
  }

  Future<void> _run(String name, String command) =>
      widget.runner.start(name, command);

  void _onRunnerChanged() {
    final running = widget.runner.isRunning;
    if (_runnerWasRunning && !running) {
      final name = widget.runner.activeName ?? '';
      if (name.contains('V1 to V4') || name.contains('V4-io')) {
        setState(() {
          _layoutLoadingEnabled = true;
          _solverLoadingEnabled = true;
          _layoutRefresh++;
          _solverRefresh++;
        });
      } else if (name.contains('Stage dataset')) {
        setState(() {
          _layoutLoadingEnabled = false;
          _solverLoadingEnabled = false;
          _layoutRefresh++;
          _solverRefresh++;
        });
      }
    }
    _runnerWasRunning = running;
  }

  Future<void> _runUs30() async {
    final ports = await PortReader.read();
    if (!mounted) return;
    if (!ports.masterAnchor) {
      await showBioSpurNotice(
        context,
        title: 'Master Anchor not connected',
        message:
            'Ultrasound measurement requires the Master_Anchor CDC port. Connect the Master Anchor board, then press Connect again.',
      );
      return;
    }
    final id = _usId.text.trim().isEmpty ? 'US01' : _usId.text.trim();
    setState(() {
      _usLoadingEnabled = true;
    });
    await _run(
      'Ultrasound $id',
      '$workspaceSetup && us30 -id ${shellQuote(id)}',
    );
  }

  void _loadLatestUs() {
    setState(() {
      _usLoadingEnabled = true;
    });
  }

  void _loadLatestLayoutAndSolver() {
    setState(() {
      _sweepsFuture = SolverSweepInfo.scan();
      _layoutLoadingEnabled = true;
      _solverLoadingEnabled = true;
      _layoutRefresh++;
      _solverRefresh++;
    });
  }

  String _stageCommand() {
    final sweepArg = widget.selectedSweep == null
        ? ''
        : ' --sweep ${shellQuote(widget.selectedSweep!.name)}';
    return 'cd $repoRoot && python3 $fieldRoot/solver/scripts/stage_field_dataset.py '
        '--session ${shellQuote(capturesRoot)} '
        '--out ${shellQuote(activeStagedDataset)}$sweepArg';
  }

  Future<void> _runSolver() async {
    setState(() {
      _layoutLoadingEnabled = false;
      _solverLoadingEnabled = false;
      _layoutRefresh++;
      _solverRefresh++;
    });
    final isV4Only = widget.selectedSolverMode == 'v4-io';
    await _run(
      isV4Only ? 'Run V4-io only' : 'Run V1 to V4-io',
      isV4Only
          ? 'cd $repoRoot && '
                'rm -rf ${shellQuote('$activeSolverOutputs/v4io_field_check')} && '
                '${_stageCommand()} && '
                'python3 $fieldRoot/solver/scripts/run_v4io_field_check.py '
                '--staged ${shellQuote(activeStagedDataset)} '
                '--out ${shellQuote('$activeSolverOutputs/v4io_field_check')}'
          : 'cd $repoRoot && '
                'rm -rf ${shellQuote('$activeSolverOutputs/v1_to_v4_io_field_check')} && '
                '${_stageCommand()} && '
                'python3 $fieldRoot/solver/scripts/run_v1_to_v4_io.py '
                '--staged ${shellQuote(activeStagedDataset)} '
                '--out ${shellQuote('$activeSolverOutputs/v1_to_v4_io_field_check')}',
    );
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        FutureBuilder<UltrasoundSummary>(
          future: _usLoadingEnabled
              ? UltrasoundSummary.readLatest()
              : Future.value(UltrasoundSummary.empty()),
          builder: (context, snapshot) {
            return UltrasoundMeasurementCard(
              idController: _usId,
              runner: widget.runner,
              summary: snapshot.data ?? UltrasoundSummary.empty(),
              onRun: _runUs30,
              onLoadLatest: _loadLatestUs,
            );
          },
        ),
        const SizedBox(height: 14),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Anchor Layout Analysis',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 10),
                FutureBuilder<List<SolverSweepInfo>>(
                  future: _sweepsFuture,
                  builder: (context, snapshot) {
                    final sweeps = snapshot.data ?? const <SolverSweepInfo>[];
                    final selected = sweeps.contains(widget.selectedSweep)
                        ? widget.selectedSweep
                        : null;
                    return Wrap(
                      spacing: 10,
                      runSpacing: 10,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        SolverSweepMenu(
                          sweeps: sweeps,
                          selected: selected,
                          onSelected: (value) {
                            widget.onSelectedSweepChanged(value);
                          },
                        ),
                        SolverModeMenu(
                          selected: widget.selectedSolverMode,
                          onSelected: (value) {
                            if (value == null) return;
                            widget.onSelectedSolverModeChanged(value);
                          },
                        ),
                        TextButton.icon(
                          onPressed: widget.runner.isRunning
                              ? null
                              : () {
                                  setState(() {
                                    _sweepsFuture = SolverSweepInfo.scan();
                                  });
                                },
                          icon: const Icon(Icons.refresh),
                          label: const Text('Refresh Sweeps'),
                        ),
                      ],
                    );
                  },
                ),
                const SizedBox(height: 10),
                FutureBuilder<UltrasoundSummary>(
                  future: UltrasoundSummary.readLatest(),
                  builder: (context, snapshot) {
                    final us = snapshot.data ?? UltrasoundSummary.empty();
                    return Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        StatusPill(
                          label: 'Selected sweep',
                          value: widget.selectedSweep?.id ?? 'latest complete',
                          tone: widget.selectedSweep == null
                              ? PillTone.neutral
                              : PillTone.active,
                        ),
                        StatusPill(
                          label: 'US height source',
                          value: us.path == null
                              ? 'none; raw layout'
                              : us.antennaCenterLabel,
                          tone: us.path == null ? PillTone.warn : PillTone.good,
                        ),
                      ],
                    );
                  },
                ),
                const SizedBox(height: 10),
                Wrap(
                  spacing: 10,
                  runSpacing: 10,
                  children: [
                    FilledButton.icon(
                      onPressed: widget.runner.isRunning ? null : _runSolver,
                      icon: const Icon(Icons.stacked_line_chart),
                      label: const Text('Stage + Run Solver'),
                    ),
                    TextButton.icon(
                      onPressed: widget.runner.isRunning
                          ? null
                          : _loadLatestLayoutAndSolver,
                      icon: const Icon(Icons.history),
                      label: const Text('Read Latest'),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                const Text(
                  'Final physical layout is layout_us_height.json. It keeps layout.json as pure UWB, then rigidly aligns F/G/H to ultrasound heights and reports residual PASS/FAIL.',
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 14),
        FutureBuilder<LayoutSummary>(
          key: ValueKey('layout-$_layoutRefresh-${widget.runner.isRunning}'),
          future: _layoutLoadingEnabled && !widget.runner.isRunning
              ? LayoutSummary.read()
              : Future.value(LayoutSummary.empty()),
          builder: (context, snapshot) {
            final summary = snapshot.data ?? LayoutSummary.empty();
            return LayoutSummaryCard(summary: summary);
          },
        ),
        const SizedBox(height: 14),
        SolverAnalysisCard(
          enabled: _solverLoadingEnabled && !widget.runner.isRunning,
          refreshKey: _solverRefresh,
          selectedLayoutPath: widget.selectedLayoutPath,
          onSelectedLayoutPathChanged: widget.onSelectedLayoutPathChanged,
        ),
      ],
    );
  }
}

class UltrasoundMeasurementCard extends StatelessWidget {
  const UltrasoundMeasurementCard({
    super.key,
    required this.idController,
    required this.runner,
    required this.summary,
    required this.onRun,
    required this.onLoadLatest,
  });

  final TextEditingController idController;
  final ScriptRunner runner;
  final UltrasoundSummary summary;
  final Future<void> Function() onRun;
  final VoidCallback onLoadLatest;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Text(
                  'Ultra Sound Measurement',
                  style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700),
                ),
                const Spacer(),
                SizedBox(
                  width: 120,
                  child: TextField(
                    controller: idController,
                    decoration: const InputDecoration(
                      labelText: 'US ID',
                      border: OutlineInputBorder(),
                      isDense: true,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                FilledButton.icon(
                  onPressed: runner.isRunning ? null : onRun,
                  icon: const Icon(Icons.height),
                  label: const Text('FGH US 30s'),
                ),
                const SizedBox(width: 8),
                OutlinedButton.icon(
                  onPressed: runner.isRunning ? runner.stop : null,
                  icon: const Icon(Icons.stop),
                  label: const Text('Stop'),
                ),
                const SizedBox(width: 8),
                TextButton.icon(
                  onPressed: runner.isRunning ? null : onLoadLatest,
                  icon: const Icon(Icons.history),
                  label: const Text('Read Latest'),
                ),
              ],
            ),
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                StatusPill(
                  label: 'State',
                  value: summary.anchors.isEmpty
                      ? 'missing'
                      : '${summary.doneCount}/${summary.expectedCount} done',
                  tone: summary.doneCount == summary.expectedCount
                      ? PillTone.good
                      : PillTone.warn,
                ),
                StatusPill(
                  label: 'Mean F/G/H z',
                  value: summary.antennaCenterMm == null
                      ? '-'
                      : '${summary.antennaCenterMm!.toStringAsFixed(0)} mm',
                  tone: summary.antennaCenterMm == null
                      ? PillTone.neutral
                      : PillTone.good,
                ),
                StatusPill(
                  label: 'Offsets',
                  value: summary.offsetsLabel,
                  tone: PillTone.active,
                ),
                StatusPill(
                  label: 'Anchors',
                  value: summary.anchors.isEmpty
                      ? '-'
                      : summary.anchors.keys.join('/'),
                  tone: summary.anchors.length == 3
                      ? PillTone.good
                      : PillTone.warn,
                ),
                StatusPill(
                  label: 'Min OK',
                  value: summary.ok == null ? '-' : '${summary.ok}',
                  tone: summary.ok == null ? PillTone.neutral : PillTone.good,
                ),
              ],
            ),
            if (summary.anchors.isNotEmpty) ...[
              const SizedBox(height: 10),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final anchor in const ['F', 'G', 'H'])
                    StatusPill(
                      label: anchor,
                      value: summary.anchors[anchor]?.antennaCenterMm == null
                          ? '-'
                          : '${summary.anchors[anchor]!.antennaCenterMm!.toStringAsFixed(0)} mm',
                      tone: summary.anchors[anchor]?.state == 'DONE'
                          ? PillTone.good
                          : PillTone.warn,
                    ),
                ],
              ),
            ],
            const SizedBox(height: 10),
            Text(
              summary.path ??
                  'No ultrasound_F/G/H.csv yet. Run FGH US 30s before final layout solve.',
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 12, color: mutedText),
            ),
          ],
        ),
      ),
    );
  }
}

class LayoutSummaryCard extends StatelessWidget {
  const LayoutSummaryCard({super.key, required this.summary});

  final LayoutSummary summary;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Latest Layout Check',
              style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                StatusPill(
                  label: 'ABCD < EFGH',
                  value: summary.zConventionOk ? 'PASS' : 'missing',
                  tone: summary.zConventionOk ? PillTone.good : PillTone.warn,
                ),
                StatusPill(
                  label: 'US-Z',
                  value: summary.usStatus?.toUpperCase() ?? '-',
                  tone: summary.usStatus == 'pass'
                      ? PillTone.good
                      : summary.usStatus == 'fail'
                      ? PillTone.bad
                      : PillTone.warn,
                ),
                StatusPill(
                  label: 'US RMS',
                  value: summary.usRmsResidual == null
                      ? '-'
                      : '${summary.usRmsResidual!.toStringAsFixed(1)} mm',
                  tone: summary.usStatus == 'fail'
                      ? PillTone.bad
                      : summary.usRmsResidual == null
                      ? PillTone.neutral
                      : PillTone.good,
                ),
                StatusPill(
                  label: 'US Max',
                  value: summary.usMaxResidual == null
                      ? '-'
                      : '${summary.usMaxResidual!.toStringAsFixed(1)} mm',
                  tone: summary.usStatus == 'fail'
                      ? PillTone.bad
                      : summary.usMaxResidual == null
                      ? PillTone.neutral
                      : PillTone.good,
                ),
                StatusPill(
                  label: 'Latest US',
                  value: summary.latestUsLabel,
                  tone: summary.usStale ? PillTone.bad : PillTone.good,
                ),
                StatusPill(
                  label: 'US residuals',
                  value: summary.usResidualLabel,
                  tone: summary.usStatus == 'fail'
                      ? PillTone.bad
                      : summary.usResiduals.isEmpty
                      ? PillTone.neutral
                      : PillTone.good,
                ),
              ],
            ),
            const SizedBox(height: 10),
            if (summary.usStale) ...[
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: toneColor(PillTone.bad).withValues(alpha: 0.12),
                  border: Border.all(color: toneColor(PillTone.bad)),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(
                  'Latest F/G/H ultrasound is newer/different than the ultrasound used by this layout. Run Solver again to update layout_us_height.json.',
                  style: TextStyle(
                    color: toneColor(PillTone.bad),
                    fontWeight: FontWeight.w800,
                  ),
                ),
              ),
              const SizedBox(height: 10),
            ],
            Text(summary.path ?? 'No layout_us_height.json yet.'),
            if (summary.usUsedSource != null) ...[
              const SizedBox(height: 4),
              Text(
                'US used by layout: ${summary.usUsedSource}',
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  color: mutedText,
                  fontFamily: 'monospace',
                  fontSize: 11,
                ),
              ),
            ],
            if (summary.latestUsPath != null) ...[
              const SizedBox(height: 4),
              Text(
                'Latest US: ${summary.latestUsPath}',
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  color: mutedText,
                  fontFamily: 'monospace',
                  fontSize: 11,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class SolverAnalysisCard extends StatefulWidget {
  const SolverAnalysisCard({
    super.key,
    required this.enabled,
    required this.refreshKey,
    required this.selectedLayoutPath,
    required this.onSelectedLayoutPathChanged,
  });

  final bool enabled;
  final int refreshKey;
  final String? selectedLayoutPath;
  final ValueChanged<String?> onSelectedLayoutPathChanged;

  @override
  State<SolverAnalysisCard> createState() => _SolverAnalysisCardState();
}

class _SolverAnalysisCardState extends State<SolverAnalysisCard> {
  late Future<SolverAnalysis> _future;
  String? _selectedVersion;

  @override
  void initState() {
    super.initState();
    _future = widget.enabled
        ? SolverAnalysis.read()
        : Future.value(SolverAnalysis.empty());
  }

  @override
  void didUpdateWidget(covariant SolverAnalysisCard oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.enabled != widget.enabled ||
        oldWidget.refreshKey != widget.refreshKey) {
      _future = widget.enabled
          ? SolverAnalysis.read()
          : Future.value(SolverAnalysis.empty());
      if (!widget.enabled) {
        _selectedVersion = null;
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<SolverAnalysis>(
      future: _future,
      builder: (context, snapshot) {
        final analysis = snapshot.data ?? SolverAnalysis.empty();
        final versions = analysis.layouts.map((e) => e.version).toList();
        AnchorLayoutData? selectedByPath;
        for (final candidate in analysis.layouts) {
          if (candidate.path == widget.selectedLayoutPath) {
            selectedByPath = candidate;
            break;
          }
        }
        if (selectedByPath != null &&
            _selectedVersion != selectedByPath.version) {
          _selectedVersion = selectedByPath.version;
        }
        if (_selectedVersion == null && versions.isNotEmpty) {
          _selectedVersion = versions.contains('v4-io')
              ? 'v4-io'
              : versions.last;
        }
        final layout = analysis.layoutFor(_selectedVersion);
        if (layout != null && widget.selectedLayoutPath != layout.path) {
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (mounted) widget.onSelectedLayoutPathChanged(layout.path);
          });
        }
        return Card(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 5, 12, 6),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(
                      analysis.modeLabel,
                      style: const TextStyle(
                        fontSize: 17,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const Spacer(),
                    if (versions.isNotEmpty)
                      VersionMenu(
                        versions: versions,
                        selected: _selectedVersion,
                        label: 'Solver layout',
                        width: 170,
                        onSelected: (value) {
                          setState(() {
                            _selectedVersion = value;
                          });
                          final selectedLayout = analysis.layoutFor(value);
                          widget.onSelectedLayoutPathChanged(
                            selectedLayout?.path,
                          );
                        },
                      ),
                  ],
                ),
                const SizedBox(height: 10),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    StatusPill(
                      label: 'Mode',
                      value: analysis.mode,
                      tone: analysis.metrics.length > 1
                          ? PillTone.active
                          : PillTone.neutral,
                    ),
                    StatusPill(
                      label: 'Versions',
                      value: '${analysis.metrics.length}',
                      tone: analysis.metrics.isEmpty
                          ? PillTone.warn
                          : PillTone.good,
                    ),
                    if (analysis.bestAutopos != null)
                      StatusPill(
                        label: 'Best RMS',
                        value:
                            '${analysis.bestAutopos!.version} ${fmtMm(analysis.bestAutopos!.autoposRms)}',
                        tone: PillTone.good,
                      ),
                    if (layout != null)
                      StatusPill(
                        label: 'Selected span',
                        value:
                            '${fmtMm(layout.spanX)} x ${fmtMm(layout.spanY)} x ${fmtMm(layout.spanZ)}',
                        tone: PillTone.active,
                      ),
                    if (layout != null)
                      StatusPill(
                        label: 'Layout file',
                        value: layout.isUsHeightLayout
                            ? 'US height'
                            : 'raw UWB',
                        tone: layout.isUsHeightLayout
                            ? PillTone.good
                            : PillTone.warn,
                      ),
                    if (layout?.usStatus != null)
                      StatusPill(
                        label: 'US-Z',
                        value: layout!.usStatus!.toUpperCase(),
                        tone: layout.usStatus == 'pass'
                            ? PillTone.good
                            : PillTone.bad,
                      ),
                    if (layout?.usRmsResidual != null)
                      StatusPill(
                        label: 'US RMS/Max',
                        value:
                            '${layout!.usRmsResidual!.toStringAsFixed(1)} / ${layout.usMaxResidual?.toStringAsFixed(1) ?? '-'} mm',
                        tone: layout.usStatus == 'fail'
                            ? PillTone.bad
                            : PillTone.good,
                      ),
                    if (layout != null && layout.usResidualLabel != '-')
                      StatusPill(
                        label: 'US residuals',
                        value: layout.usResidualLabel,
                        tone: layout.usStatus == 'fail'
                            ? PillTone.bad
                            : PillTone.good,
                      ),
                  ],
                ),
                const SizedBox(height: 12),
                SolverMetricsTable(metrics: analysis.metrics),
                const SizedBox(height: 12),
                SizedBox(
                  height: 360,
                  child: layout == null
                      ? const Center(child: Text('No anchor layout yet.'))
                      : AnchorLayout3DView(layout: layout),
                ),
                if (layout != null) ...[
                  const SizedBox(height: 12),
                  AnchorCoordinateTable(layout: layout),
                ],
                const SizedBox(height: 8),
                Text(
                  analysis.sourcePath ?? 'No solver output detected yet.',
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 12, color: mutedText),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class SolverMetricsTable extends StatefulWidget {
  const SolverMetricsTable({super.key, required this.metrics});

  final List<SolverMetric> metrics;

  @override
  State<SolverMetricsTable> createState() => _SolverMetricsTableState();
}

class _SolverMetricsTableState extends State<SolverMetricsTable> {
  final ScrollController _controller = ScrollController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final metrics = widget.metrics;
    if (metrics.isEmpty) {
      return const Text('No solver metrics yet. Run V4-io or V1 to V4-io.');
    }
    return Scrollbar(
      controller: _controller,
      thumbVisibility: true,
      trackVisibility: true,
      interactive: true,
      child: SingleChildScrollView(
        controller: _controller,
        scrollDirection: Axis.horizontal,
        primary: false,
        child: Padding(
          padding: const EdgeInsets.only(bottom: 14),
          child: DataTable(
            headingRowColor: WidgetStateProperty.all(const Color(0x66242B12)),
            columns: const [
              DataColumn(label: Text('Solver')),
              DataColumn(label: Text('Status')),
              DataColumn(label: Text('AutoPos RMS')),
              DataColumn(label: Text('AutoPos p95')),
              DataColumn(label: Text('Static med')),
              DataColumn(label: Text('Static p95')),
              DataColumn(label: Text('Roto dR RMS')),
              DataColumn(label: Text('Turn center med')),
            ],
            rows: metrics
                .map(
                  (m) => DataRow(
                    cells: [
                      DataCell(Text(m.version)),
                      DataCell(
                        Text(
                          m.statusLabel,
                          style: TextStyle(
                            color: m.hasLayout && m.hasMetrics
                                ? toneColor(PillTone.good)
                                : m.hasLayout
                                ? toneColor(PillTone.warn)
                                : toneColor(PillTone.warn),
                          ),
                        ),
                      ),
                      DataCell(Text(fmtMm(m.autoposRms))),
                      DataCell(Text(fmtMm(m.autoposP95))),
                      DataCell(Text(fmtMm(m.staticMedian))),
                      DataCell(Text(fmtMm(m.staticP95))),
                      DataCell(Text(fmtMm(m.rotoDeltaRRms))),
                      DataCell(Text(fmtMm(m.rotoTurnCenterMedian))),
                    ],
                  ),
                )
                .toList(),
          ),
        ),
      ),
    );
  }
}

class AnchorCoordinateTable extends StatefulWidget {
  const AnchorCoordinateTable({super.key, required this.layout});

  final AnchorLayoutData layout;

  @override
  State<AnchorCoordinateTable> createState() => _AnchorCoordinateTableState();
}

class _AnchorCoordinateTableState extends State<AnchorCoordinateTable> {
  final ScrollController _controller = ScrollController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final layout = widget.layout;
    return Scrollbar(
      controller: _controller,
      thumbVisibility: true,
      trackVisibility: true,
      interactive: true,
      child: SingleChildScrollView(
        controller: _controller,
        scrollDirection: Axis.horizontal,
        primary: false,
        child: Padding(
          padding: const EdgeInsets.only(bottom: 14),
          child: DataTable(
            headingRowColor: WidgetStateProperty.all(const Color(0x66242B12)),
            columns: const [
              DataColumn(label: Text('Anchor')),
              DataColumn(label: Text('X')),
              DataColumn(label: Text('Y')),
              DataColumn(label: Text('Z')),
              DataColumn(label: Text('Layer')),
            ],
            rows: layout.points.map((p) {
              final upper = anchors.indexOf(p.label) >= 4;
              return DataRow(
                cells: [
                  DataCell(Text(p.label)),
                  DataCell(Text(fmtMm(p.x))),
                  DataCell(Text(fmtMm(p.y))),
                  DataCell(Text(fmtMm(p.z))),
                  DataCell(Text(upper ? 'upper' : 'lower')),
                ],
              );
            }).toList(),
          ),
        ),
      ),
    );
  }
}

class AnchorLayout3DView extends StatefulWidget {
  const AnchorLayout3DView({super.key, required this.layout});

  final AnchorLayoutData layout;

  @override
  State<AnchorLayout3DView> createState() => _AnchorLayout3DViewState();
}

Offset _project3D({
  required double x,
  required double y,
  required double z,
  required double centerX,
  required double centerY,
  required double centerZ,
  required Offset screenCenter,
  required double scale,
  required double yaw,
  required double pitch,
}) {
  final x0 = x - centerX;
  final y0 = y - centerY;
  final z0 = z - centerZ;
  final cy = math.cos(yaw);
  final sy = math.sin(yaw);
  final cp = math.cos(pitch);
  final sp = math.sin(pitch);
  final x1 = x0 * cy - y0 * sy;
  final y1 = x0 * sy + y0 * cy;
  final z2 = y1 * sp + z0 * cp;
  return screenCenter + Offset(-x1 * scale, -z2 * scale);
}

class _AnchorHoverPopup extends StatelessWidget {
  const _AnchorHoverPopup({
    required this.anchor,
    required this.position,
    required this.size,
  });

  final AnchorPoint anchor;
  final Offset position;
  final Size size;

  @override
  Widget build(BuildContext context) {
    const width = 150.0;
    const height = 86.0;
    final left = (position.dx + 16)
        .clamp(8.0, math.max(8.0, size.width - width - 8))
        .toDouble();
    final top = (position.dy + 16)
        .clamp(8.0, math.max(8.0, size.height - height - 8))
        .toDouble();
    return Positioned(
      left: left,
      top: top,
      width: width,
      child: IgnorePointer(
        child: DecoratedBox(
          decoration: BoxDecoration(
            color: const Color(0xEE050806),
            border: Border.all(color: controlGreen.withValues(alpha: 0.72)),
            borderRadius: BorderRadius.circular(8),
            boxShadow: [
              BoxShadow(
                color: biospurGlow.withValues(alpha: 0.18),
                blurRadius: 14,
              ),
            ],
          ),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
            child: DefaultTextStyle(
              style: const TextStyle(fontSize: 12, color: mutedText),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    'Anchor ${anchor.label}',
                    style: const TextStyle(
                      color: biospurGlow,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                  const SizedBox(height: 5),
                  Text('X: ${fmtMm(anchor.x)}'),
                  Text('Y: ${fmtMm(anchor.y)}'),
                  Text(
                    '${anchor.label == 'H' ? 'US-Z' : 'Z'}: ${fmtMm(anchor.z)}',
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _TagHoverPopup extends StatelessWidget {
  const _TagHoverPopup({
    required this.frame,
    required this.position,
    required this.size,
  });

  final TrajectoryFrame frame;
  final Offset position;
  final Size size;

  @override
  Widget build(BuildContext context) {
    const width = 205.0;
    const height = 124.0;
    final left = (position.dx + 16)
        .clamp(8.0, math.max(8.0, size.width - width - 8))
        .toDouble();
    final top = (position.dy + 16)
        .clamp(8.0, math.max(8.0, size.height - height - 8))
        .toDouble();
    return Positioned(
      left: left,
      top: top,
      width: width,
      child: IgnorePointer(
        child: DecoratedBox(
          decoration: BoxDecoration(
            color: const Color(0xEE050806),
            border: Border.all(color: biospurGlow.withValues(alpha: 0.78)),
            borderRadius: BorderRadius.circular(8),
            boxShadow: [
              BoxShadow(
                color: biospurGlow.withValues(alpha: 0.20),
                blurRadius: 16,
              ),
            ],
          ),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
            child: DefaultTextStyle(
              style: const TextStyle(fontSize: 12, color: mutedText),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    frame.tag,
                    style: const TextStyle(
                      color: biospurGlow,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                  const SizedBox(height: 5),
                  Text('t: ${frame.hostElapsedS.toStringAsFixed(2)} s'),
                  Text('X: ${fmtMm(frame.xMm)}'),
                  Text('Y: ${fmtMm(frame.yMm)}'),
                  Text('Z: ${fmtMm(frame.zMm)}'),
                  Text(
                    'residual: ${fmtMm(frame.residualRmsMm)}  anchors: ${frame.anchorsUsed}',
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _CenterHoverPopup extends StatelessWidget {
  const _CenterHoverPopup({
    required this.estimate,
    required this.position,
    required this.size,
  });

  final RotoTrajectoryEstimate estimate;
  final Offset position;
  final Size size;

  @override
  Widget build(BuildContext context) {
    const width = 210.0;
    const height = 116.0;
    final left = (position.dx + 16)
        .clamp(8.0, math.max(8.0, size.width - width - 8))
        .toDouble();
    final top = (position.dy + 16)
        .clamp(8.0, math.max(8.0, size.height - height - 8))
        .toDouble();
    return Positioned(
      left: left,
      top: top,
      width: width,
      child: IgnorePointer(
        child: DecoratedBox(
          decoration: BoxDecoration(
            color: const Color(0xEE050806),
            border: Border.all(color: controlGreen.withValues(alpha: 0.78)),
            borderRadius: BorderRadius.circular(8),
            boxShadow: [
              BoxShadow(
                color: biospurGlow.withValues(alpha: 0.18),
                blurRadius: 16,
              ),
            ],
          ),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
            child: DefaultTextStyle(
              style: const TextStyle(fontSize: 12, color: mutedText),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    'Center ${estimate.tag}',
                    style: const TextStyle(
                      color: biospurGlow,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                  const SizedBox(height: 5),
                  Text('X: ${fmtMm(estimate.centerX)}'),
                  Text('Y: ${fmtMm(estimate.centerY)}'),
                  Text('Z: ${fmtMm(estimate.centerZ)}'),
                  Text(
                    'R: ${fmtMm(estimate.radiusMm)}  rms: ${fmtMm(estimate.radiusRmsMm)}',
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _AnchorLayout3DViewState extends State<AnchorLayout3DView> {
  double yaw = -0.75;
  double pitch = -0.55;
  double zoom = 1.0;
  AnchorPoint? _hoveredAnchor;
  Offset? _hoverPosition;

  void _handleWheelZoom(PointerSignalEvent event) {
    if (event is! PointerScrollEvent ||
        !HardwareKeyboard.instance.isControlPressed) {
      return;
    }
    GestureBinding.instance.pointerSignalResolver.register(event, (
      PointerSignalEvent resolved,
    ) {
      final scroll = resolved as PointerScrollEvent;
      final factor = math.exp(-scroll.scrollDelta.dy * 0.0015);
      setState(() {
        zoom = (zoom * factor).clamp(0.55, 4.5);
      });
    });
  }

  @override
  Widget build(BuildContext context) {
    return Listener(
      onPointerSignal: _handleWheelZoom,
      child: GestureDetector(
        onScaleUpdate: (details) {
          setState(() {
            yaw += details.focalPointDelta.dx * 0.01;
            pitch = (pitch - details.focalPointDelta.dy * 0.01).clamp(
              -1.35,
              1.35,
            );
            zoom = (zoom * details.scale).clamp(0.55, 4.5);
          });
        },
        child: Container(
          decoration: BoxDecoration(
            border: Border.all(color: panelLine),
            borderRadius: BorderRadius.circular(8),
          ),
          child: LayoutBuilder(
            builder: (context, constraints) {
              final size = Size(constraints.maxWidth, constraints.maxHeight);
              return MouseRegion(
                onHover: (event) {
                  final anchor = _hitTestAnchor(size, event.localPosition);
                  final position = anchor == null ? null : event.localPosition;
                  if (anchor != _hoveredAnchor || position != _hoverPosition) {
                    setState(() {
                      _hoveredAnchor = anchor;
                      _hoverPosition = position;
                    });
                  }
                },
                onExit: (_) {
                  if (_hoveredAnchor != null) {
                    setState(() {
                      _hoveredAnchor = null;
                      _hoverPosition = null;
                    });
                  }
                },
                child: Stack(
                  children: [
                    Positioned.fill(
                      child: CustomPaint(
                        painter: AnchorLayoutPainter(
                          layout: widget.layout,
                          yaw: yaw,
                          pitch: pitch,
                          zoom: zoom,
                          hoveredAnchorLabel: _hoveredAnchor?.label,
                        ),
                        child: const SizedBox.expand(),
                      ),
                    ),
                    if (_hoveredAnchor != null && _hoverPosition != null)
                      _AnchorHoverPopup(
                        anchor: _hoveredAnchor!,
                        position: _hoverPosition!,
                        size: size,
                      ),
                  ],
                ),
              );
            },
          ),
        ),
      ),
    );
  }

  AnchorPoint? _hitTestAnchor(Size size, Offset pointer) {
    if (size.width <= 0 || size.height <= 0) return null;
    final center = Offset(size.width / 2, size.height / 2 + 12);
    final maxSpan = math.max(
      widget.layout.spanX,
      math.max(widget.layout.spanY, widget.layout.spanZ),
    );
    final scale = maxSpan <= 0
        ? 0.08
        : math.min(size.width, size.height) * 0.62 / maxSpan * zoom;
    AnchorPoint? best;
    var bestDistance = double.infinity;
    for (final anchor in widget.layout.points) {
      final screen = _project3D(
        x: anchor.x,
        y: anchor.y,
        z: anchor.z,
        centerX: widget.layout.centerX,
        centerY: widget.layout.centerY,
        centerZ: widget.layout.centerZ,
        screenCenter: center,
        scale: scale,
        yaw: yaw,
        pitch: pitch,
      );
      final distance = (pointer - screen).distance;
      final labelRect = Rect.fromLTWH(screen.dx + 4, screen.dy - 26, 34, 30);
      if ((distance <= 14 || labelRect.contains(pointer)) &&
          distance < bestDistance) {
        best = anchor;
        bestDistance = distance;
      }
    }
    return best;
  }
}

class AnchorLayoutPainter extends CustomPainter {
  const AnchorLayoutPainter({
    required this.layout,
    required this.yaw,
    required this.pitch,
    required this.zoom,
    this.hoveredAnchorLabel,
    this.hoveredTag,
  });

  final AnchorLayoutData layout;
  final double yaw;
  final double pitch;
  final double zoom;
  final String? hoveredAnchorLabel;
  final String? hoveredTag;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..strokeWidth = 1
      ..style = PaintingStyle.stroke
      ..color = tableLine;
    final textPainter = TextPainter(textDirection: TextDirection.ltr);
    final center = Offset(size.width / 2, size.height / 2 + 12);
    final maxSpan = math.max(
      layout.spanX,
      math.max(layout.spanY, layout.spanZ),
    );
    final scale = maxSpan <= 0
        ? 0.08
        : math.min(size.width, size.height) * 0.62 / maxSpan * zoom;

    Offset projectCoord(double x, double y, double z) => _project3D(
      x: x,
      y: y,
      z: z,
      centerX: layout.centerX,
      centerY: layout.centerY,
      centerZ: layout.centerZ,
      screenCenter: center,
      scale: scale,
      yaw: yaw,
      pitch: pitch,
    );

    Offset project(AnchorPoint p) {
      return projectCoord(p.x, p.y, p.z);
    }

    void line(String a, String b, Color color) {
      final pa = layout.byLabel[a];
      final pb = layout.byLabel[b];
      if (pa == null || pb == null) return;
      canvas.drawLine(project(pa), project(pb), paint..color = color);
    }

    void axis(String label, Offset start, Offset end, Color color) {
      canvas.drawLine(
        start,
        end,
        Paint()
          ..color = color
          ..strokeWidth = 1.6,
      );
      textPainter.text = TextSpan(
        text: label,
        style: TextStyle(
          color: color,
          fontSize: 12,
          fontWeight: FontWeight.w800,
        ),
      );
      textPainter.layout();
      textPainter.paint(canvas, end + const Offset(5, -6));
    }

    final origin = projectCoord(layout.minX, layout.minY, layout.minZ);
    final gridPaint = Paint()
      ..color = tableLine.withValues(alpha: 0.38)
      ..strokeWidth = 0.7;
    for (var i = 0; i <= 4; i++) {
      final t = i / 4;
      final x = layout.minX + layout.spanX * t;
      final y = layout.minY + layout.spanY * t;
      canvas.drawLine(
        projectCoord(x, layout.minY, layout.minZ),
        projectCoord(x, layout.maxY, layout.minZ),
        gridPaint,
      );
      canvas.drawLine(
        projectCoord(layout.minX, y, layout.minZ),
        projectCoord(layout.maxX, y, layout.minZ),
        gridPaint,
      );
      textPainter.text = TextSpan(
        text: x.toStringAsFixed(0),
        style: const TextStyle(color: mutedText, fontSize: 10),
      );
      textPainter.layout();
      textPainter.paint(
        canvas,
        projectCoord(x, layout.minY, layout.minZ) + const Offset(-12, 6),
      );
      textPainter.text = TextSpan(
        text: y.toStringAsFixed(0),
        style: const TextStyle(color: mutedText, fontSize: 10),
      );
      textPainter.layout();
      textPainter.paint(
        canvas,
        projectCoord(layout.minX, y, layout.minZ) + const Offset(-34, -5),
      );
    }

    axis(
      'X ${layout.minX.toStringAsFixed(0)}..${layout.maxX.toStringAsFixed(0)}',
      origin,
      projectCoord(layout.maxX, layout.minY, layout.minZ),
      controlGreen,
    );
    axis(
      'Y ${layout.minY.toStringAsFixed(0)}..${layout.maxY.toStringAsFixed(0)}',
      origin,
      projectCoord(layout.minX, layout.maxY, layout.minZ),
      controlGreen,
    );
    axis(
      'Z ${layout.minZ.toStringAsFixed(0)}..${layout.maxZ.toStringAsFixed(0)}',
      origin,
      projectCoord(layout.minX, layout.minY, layout.maxZ),
      controlGreen,
    );

    for (final edge in const [
      ['A', 'B'],
      ['B', 'C'],
      ['C', 'D'],
      ['D', 'A'],
      ['E', 'F'],
      ['F', 'G'],
      ['G', 'H'],
      ['H', 'E'],
      ['A', 'E'],
      ['B', 'F'],
      ['C', 'G'],
      ['D', 'H'],
    ]) {
      final lower =
          anchors.indexOf(edge.first) < 4 && anchors.indexOf(edge.last) < 4;
      final upper =
          anchors.indexOf(edge.first) >= 4 && anchors.indexOf(edge.last) >= 4;
      line(
        edge.first,
        edge.last,
        upper
            ? biospurGlow
            : lower
            ? controlGreen
            : const Color(0x667A8F65),
      );
    }

    for (final p in layout.points) {
      final screen = project(p);
      final upper = anchors.indexOf(p.label) >= 4;
      final fill = upper ? biospurGlow : controlGreen;
      if (p.label == hoveredAnchorLabel) {
        canvas.drawCircle(
          screen,
          12,
          Paint()
            ..style = PaintingStyle.stroke
            ..strokeWidth = 2.2
            ..color = biospurGlow,
        );
      }
      canvas.drawCircle(screen, 5.5, Paint()..color = fill);
      canvas.drawCircle(
        screen,
        7.5,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1
          ..color = Colors.white.withValues(alpha: 0.85),
      );
      textPainter.text = TextSpan(
        text: p.label,
        style: const TextStyle(
          color: Colors.white,
          fontSize: 12,
          fontWeight: FontWeight.w800,
        ),
      );
      textPainter.layout();
      textPainter.paint(canvas, screen + const Offset(8, -18));
    }

    textPainter.text = TextSpan(
      text:
          '${layout.version}  span ${fmtMm(layout.spanX)} x ${fmtMm(layout.spanY)} x ${fmtMm(layout.spanZ)}',
      style: const TextStyle(color: mutedText, fontSize: 12),
    );
    textPainter.layout(maxWidth: size.width - 24);
    textPainter.paint(canvas, const Offset(12, 12));
  }

  @override
  bool shouldRepaint(covariant AnchorLayoutPainter oldDelegate) {
    return oldDelegate.layout != layout ||
        oldDelegate.yaw != yaw ||
        oldDelegate.pitch != pitch ||
        oldDelegate.zoom != zoom ||
        oldDelegate.hoveredAnchorLabel != hoveredAnchorLabel;
  }
}

class CaptureTab extends StatefulWidget {
  const CaptureTab({
    super.key,
    required this.runner,
    required this.ports,
    required this.selectedLayoutPath,
    required this.selectedSweep,
    required this.selectedSolverMode,
    required this.onSelectedLayoutPathChanged,
    required this.onSelectedSweepChanged,
  });

  final ScriptRunner runner;
  final PortSnapshot ports;
  final String? selectedLayoutPath;
  final SolverSweepInfo? selectedSweep;
  final String selectedSolverMode;
  final ValueChanged<String?> onSelectedLayoutPathChanged;
  final ValueChanged<SolverSweepInfo?> onSelectedSweepChanged;

  @override
  State<CaptureTab> createState() => _CaptureTabState();
}

class _CaptureTabState extends State<CaptureTab>
    with AutomaticKeepAliveClientMixin<CaptureTab> {
  String _kind = 'static';
  final TextEditingController _id = TextEditingController(text: 'ID01');
  final TextEditingController _duration = TextEditingController(text: '120');
  late Future<List<CaptureSessionInfo>> _sessionsFuture;
  late Future<SolverAnalysis> _analysisFuture;
  late Future<List<SolverSweepInfo>> _sweepsFuture;
  CaptureSessionInfo? _selectedSession;
  String? _selectedLayoutPath;
  String? _selectedTag;
  Set<String> _visibleTags = {};
  final Map<String, Set<String>> _completedPlanIds = {
    'static': <String>{},
    'roto': <String>{},
    'wand': <String>{},
  };
  Set<String> _freeKnownTags = {};
  Set<String> _freeSelectedTags = {};
  Set<String> _activeExpectedTags = {};
  bool _anchorPreflightForCapture = false;
  TrajectoryData? _trajectory;
  String? _trajectoryOut;
  String? _loadedTrajectoryOut;
  bool _wasRunning = false;
  bool _deferredTrajectoryLoad = false;

  @override
  bool get wantKeepAlive => true;

  @override
  void initState() {
    super.initState();
    _sessionsFuture = CaptureSessionInfo.scan();
    _analysisFuture = SolverAnalysis.read();
    _sweepsFuture = SolverSweepInfo.scan();
    widget.runner.addListener(_runnerChanged);
  }

  @override
  void dispose() {
    widget.runner.removeListener(_runnerChanged);
    _id.dispose();
    _duration.dispose();
    super.dispose();
  }

  void _runnerChanged() {
    final running = widget.runner.isRunning;
    if (_wasRunning && !running && _trajectoryOut != null) {
      _deferredTrajectoryLoad = true;
      _loadTrajectory(File(_trajectoryOut!));
    }
    _wasRunning = running;
  }

  @override
  void didUpdateWidget(covariant CaptureTab oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.selectedLayoutPath != widget.selectedLayoutPath ||
        oldWidget.selectedSweep != widget.selectedSweep ||
        oldWidget.selectedSolverMode != widget.selectedSolverMode) {
      _analysisFuture = SolverAnalysis.read();
      _sweepsFuture = SolverSweepInfo.scan();
      _selectedLayoutPath = widget.selectedLayoutPath;
    }
  }

  Future<void> _start() async {
    final ports = await PortReader.read();
    if (!mounted) return;
    if (!ports.masterTag) {
      await showBioSpurNotice(
        context,
        title: 'Master Tag not connected',
        message:
            'Static/Roto/Wand/Free capture requires the Master_Tag CDC port. Connect the Master Tag board, then press Connect again.',
      );
      return;
    }
    final id = _id.text.trim();
    final duration = _duration.text.trim();
    if (id.isEmpty || duration.isEmpty) {
      await showBioSpurNotice(
        context,
        title: 'Capture settings incomplete',
        message: 'Set a capture ID and duration before starting capture.',
      );
      return;
    }
    final targetArg = _kind == 'free'
        ? (_freeSelectedTags.toList()..sort())
        : [...?captureTargetsByKind[_kind]];
    if (_kind == 'free' && targetArg.isEmpty) {
      await showBioSpurNotice(
        context,
        title: 'No Free tags selected',
        message:
            'Select one or more BS tags for Free capture. Only selected tags will be placed in this run TDMA roster.',
      );
      return;
    }
    final targetSuffix = _kind == 'free'
        ? ' -targets ${shellQuote(targetArg.join(','))}'
        : '';
    final preflightEnv = _anchorPreflightForCapture
        ? 'BIOSPUR_SKIP_ANCHOR_PREFLIGHT_FOR_CAPTURE=0'
        : 'BIOSPUR_SKIP_ANCHOR_PREFLIGHT_FOR_CAPTURE=1';
    final command =
        '$workspaceSetup && $preflightEnv $_kind -id ${shellQuote(id)} -s ${shellQuote(duration)}$targetSuffix';
    setState(() {
      _activeExpectedTags = targetArg.toSet();
    });
    await widget.runner.start('$_kind $id', command);
  }

  Future<void> _refreshPlayback() async {
    setState(() {
      _sessionsFuture = CaptureSessionInfo.scan();
      _analysisFuture = SolverAnalysis.read();
      _sweepsFuture = SolverSweepInfo.scan();
    });
  }

  Future<void> _exportTrajectory() async {
    final session = _selectedSession;
    final layout = _selectedLayoutPath;
    if (session == null || layout == null) return;
    await Directory('$activeWorkspaceRoot/exports').create(recursive: true);
    final out =
        '$activeWorkspaceRoot/exports/biospur_trajectory_${DateTime.now().millisecondsSinceEpoch}.json';
    setState(() {
      _trajectoryOut = out;
      _loadedTrajectoryOut = null;
      _trajectory = null;
      _deferredTrajectoryLoad = false;
    });
    final tagArg = _selectedTag == null
        ? ''
        : ' --tag ${shellQuote(_selectedTag!)}';
    final command =
        'cd $repoRoot && python3 $fieldRoot/solver/scripts/export_capture_trajectory.py '
        '--layout ${shellQuote(layout)} --capture ${shellQuote(session.path)} '
        '--out ${shellQuote(out)} --max-frames 3000$tagArg';
    await widget.runner.start('Export ${session.id}', command);
  }

  Future<void> _loadTrajectory(File file) async {
    if (!file.existsSync()) return;
    final data = await TrajectoryData.read(file);
    if (!mounted) return;
    setState(() {
      _trajectory = data;
      _loadedTrajectoryOut = file.path;
      _deferredTrajectoryLoad = false;
      _visibleTags = data.tags.toSet();
      _selectedTag = data.tags.contains(_selectedTag)
          ? _selectedTag
          : (data.tags.isEmpty ? null : data.tags.first);
    });
  }

  void _loadTrajectoryIfReady() {
    final out = _trajectoryOut;
    if (out == null ||
        widget.runner.isRunning ||
        _loadedTrajectoryOut == out ||
        !_deferredTrajectoryLoad) {
      return;
    }
    Future.microtask(() => _loadTrajectory(File(out)));
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    _loadTrajectoryIfReady();
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Card(
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Static / Roto / Wand Capture',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 12),
                Wrap(
                  spacing: 12,
                  runSpacing: 10,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    SegmentedButton<String>(
                      segments: const [
                        ButtonSegment(value: 'static', label: Text('Static')),
                        ButtonSegment(value: 'roto', label: Text('Roto')),
                        ButtonSegment(value: 'wand', label: Text('Wand')),
                        ButtonSegment(value: 'free', label: Text('Free')),
                      ],
                      selected: {_kind},
                      onSelectionChanged: (value) {
                        setState(() {
                          _kind = value.first;
                          _id.text = _kind == 'static'
                              ? 'ID01'
                              : _kind == 'roto'
                              ? 'R01'
                              : _kind == 'wand'
                              ? 'W01'
                              : 'F01';
                        });
                      },
                    ),
                    SizedBox(
                      width: 120,
                      child: TextField(
                        controller: _id,
                        decoration: const InputDecoration(
                          labelText: 'ID',
                          border: OutlineInputBorder(),
                          isDense: true,
                        ),
                      ),
                    ),
                    SizedBox(
                      width: 120,
                      child: TextField(
                        controller: _duration,
                        decoration: const InputDecoration(
                          labelText: 'Seconds',
                          border: OutlineInputBorder(),
                          isDense: true,
                        ),
                      ),
                    ),
                    FilledButton.icon(
                      onPressed: widget.runner.isRunning ? null : _start,
                      icon: const Icon(Icons.fiber_manual_record),
                      label: const Text('Start Capture'),
                    ),
                    OutlinedButton.icon(
                      onPressed: widget.runner.isRunning
                          ? widget.runner.stop
                          : null,
                      icon: const Icon(Icons.stop),
                      label: const Text('Stop'),
                    ),
                    FilterChip(
                      selected: _anchorPreflightForCapture,
                      onSelected: widget.runner.isRunning
                          ? null
                          : (value) {
                              setState(() {
                                _anchorPreflightForCapture = value;
                              });
                            },
                      avatar: Icon(
                        _anchorPreflightForCapture
                            ? Icons.security
                            : Icons.flash_on,
                        size: 18,
                      ),
                      label: Text(
                        _anchorPreflightForCapture
                            ? 'Anchor preflight: ON'
                            : 'Anchor preflight: OFF',
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                Text(
                  _anchorPreflightForCapture
                      ? 'Capture will verify/set anchor responder state before TDMA.'
                      : 'Fast capture: anchor preflight is skipped. Use only when anchors are already responders.',
                  style: const TextStyle(color: mutedText, fontSize: 12),
                ),
                const SizedBox(height: 12),
                FutureBuilder<List<Object>>(
                  future: Future.wait([
                    _analysisFuture,
                    StagedSweepInfo.read(),
                    _sweepsFuture,
                  ]),
                  builder: (context, snapshot) {
                    final analysis =
                        (snapshot.data?[0] as SolverAnalysis?) ??
                        SolverAnalysis.empty();
                    final staged =
                        (snapshot.data?[1] as StagedSweepInfo?) ??
                        StagedSweepInfo.empty();
                    final sweeps =
                        (snapshot.data?[2] as List<SolverSweepInfo>?) ??
                        const <SolverSweepInfo>[];
                    final selectedSweep = sweeps.contains(widget.selectedSweep)
                        ? widget.selectedSweep
                        : null;
                    final selectedLayout = analysis.layoutByPath(
                      widget.selectedLayoutPath,
                    );
                    final selectedPath = widget.selectedLayoutPath;
                    final layoutFile = selectedPath == null
                        ? null
                        : File(selectedPath);
                    final layoutExists = layoutFile?.existsSync() ?? false;
                    final layoutModified = layoutExists
                        ? layoutFile!.statSync().modified
                        : null;
                    final requestedName = widget.selectedSweep?.name;
                    final stagedName = staged.sweepName;
                    final sweepPinned = requestedName != null;
                    final stagedSweepRecorded = stagedName != null;
                    final sweepMismatch =
                        requestedName != null &&
                        stagedName != null &&
                        stagedName != requestedName;
                    final stageNewerThanLayout =
                        layoutModified != null &&
                        staged.manifestModified != null &&
                        layoutModified.isBefore(staged.manifestModified!);
                    final layoutBad =
                        selectedPath == null ||
                        !layoutExists ||
                        sweepMismatch ||
                        stageNewerThanLayout;
                    final layoutNeedsPinnedWarning =
                        !layoutBad && !sweepPinned && !stagedSweepRecorded;
                    final layoutStatus = selectedPath == null
                        ? 'no layout selected'
                        : !layoutExists
                        ? 'layout file missing'
                        : sweepMismatch
                        ? 'SWEEP MISMATCH'
                        : stageNewerThanLayout
                        ? 'STALE: rerun solver'
                        : layoutNeedsPinnedWarning
                        ? 'not pinned'
                        : 'OK';
                    final statusTone = layoutBad
                        ? PillTone.bad
                        : layoutNeedsPinnedWarning
                        ? PillTone.warn
                        : PillTone.good;
                    return Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Wrap(
                          spacing: 10,
                          runSpacing: 8,
                          crossAxisAlignment: WrapCrossAlignment.center,
                          children: [
                            SolverLayoutMenu(
                              layouts: analysis.layouts,
                              selectedPath: selectedPath,
                              onSelected: (value) {
                                setState(() {
                                  _selectedLayoutPath = value;
                                });
                                widget.onSelectedLayoutPathChanged(value);
                              },
                            ),
                            SolverSweepMenu(
                              sweeps: sweeps,
                              selected: selectedSweep,
                              onSelected: widget.onSelectedSweepChanged,
                            ),
                            StatusPill(
                              label: 'Layout version',
                              value: selectedLayout?.version ?? 'none',
                              tone: selectedLayout == null
                                  ? PillTone.warn
                                  : PillTone.active,
                            ),
                            StatusPill(
                              label: 'Solver run',
                              value: widget.selectedSolverMode == 'v4-io'
                                  ? 'V4-io only'
                                  : 'V1 to V4-io',
                              tone: PillTone.active,
                            ),
                            StatusPill(
                              label: 'Solver sweep selector',
                              value:
                                  widget.selectedSweep?.shortLabel ??
                                  (stagedName == null
                                      ? 'Auto latest complete (not pinned)'
                                      : 'Auto recorded: $stagedName'),
                              tone:
                                  widget.selectedSweep == null &&
                                      stagedName == null
                                  ? PillTone.neutral
                                  : PillTone.active,
                            ),
                            StatusPill(
                              label: 'Staged sweep used',
                              value: staged.sweepName ?? 'not staged',
                              tone: staged.sweepName == null
                                  ? PillTone.warn
                                  : PillTone.good,
                            ),
                            if (staged.rows != null)
                              StatusPill(
                                label: 'Staged rows',
                                value: '${staged.rows}',
                                tone: PillTone.good,
                              ),
                            StatusPill(
                              label: 'Layout status',
                              value: layoutStatus,
                              tone: statusTone,
                            ),
                            if (layoutModified != null)
                              StatusPill(
                                label: 'Layout mtime',
                                value: shortDateTime(layoutModified),
                                tone: PillTone.neutral,
                              ),
                          ],
                        ),
                        const SizedBox(height: 8),
                        Text(
                          selectedPath == null
                              ? 'Solver layout for realtime/playback: none selected'
                              : 'Solver layout for realtime/playback: $selectedPath',
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            color: mutedText,
                            fontFamily: 'monospace',
                            fontSize: 11,
                          ),
                        ),
                        if (staged.selectedSweepDir != null) ...[
                          const SizedBox(height: 4),
                          Text(
                            'Solver staged sweep source: ${staged.selectedSweepDir}',
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              color: mutedText,
                              fontFamily: 'monospace',
                              fontSize: 11,
                            ),
                          ),
                        ],
                        if (layoutBad || layoutNeedsPinnedWarning) ...[
                          const SizedBox(height: 8),
                          Container(
                            width: double.infinity,
                            padding: const EdgeInsets.all(10),
                            decoration: BoxDecoration(
                              color: toneColor(
                                statusTone,
                              ).withValues(alpha: 0.12),
                              border: Border.all(color: toneColor(statusTone)),
                              borderRadius: BorderRadius.circular(8),
                            ),
                            child: Text(
                              sweepMismatch
                                  ? 'Current solver output was staged from $stagedName, but the selected solver sweep is $requestedName. Go to Anchor Layout Analysis and run solver again for the selected sweep.'
                                  : stageNewerThanLayout
                                  ? 'Staged dataset is newer than this layout file. Run Stage + Run Solver again before using this layout for realtime/playback.'
                                  : layoutNeedsPinnedWarning
                                  ? 'No solver sweep is pinned. The solver command uses latest complete sweep at run time, which can be ambiguous after multiple sweeps. Select a concrete sweep in Anchor Layout Analysis before running solver.'
                                  : 'Selected layout is not ready. Run solver before using realtime/playback.',
                              style: TextStyle(
                                color: toneColor(statusTone),
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                          ),
                        ],
                      ],
                    );
                  },
                ),
                const SizedBox(height: 12),
              ],
            ),
          ),
        ),
        const SizedBox(height: 14),
        if (_kind == 'free')
          FreeTagSelectionPanel(
            knownTags: _freeKnownTags,
            selectedTags: _freeSelectedTags,
            onCheckAvailable: () async {
              final ports = await PortReader.read();
              if (!context.mounted) return;
              if (!ports.masterTag) {
                await showBioSpurNotice(
                  context,
                  title: 'Master Tag not connected',
                  message:
                      'Free tag availability check requires the Master_Tag CDC port. Connect the Master Tag board, then press Connect again.',
                );
                return;
              }
              setState(() {
                _freeKnownTags = freeCaptureTags.toSet();
                _freeSelectedTags = _freeSelectedTags.intersection(
                  _freeKnownTags,
                );
              });
            },
            onToggleTag: (tag) {
              setState(() {
                _freeSelectedTags = {..._freeSelectedTags};
                _freeSelectedTags.contains(tag)
                    ? _freeSelectedTags.remove(tag)
                    : _freeSelectedTags.add(tag);
              });
            },
          )
        else
          ExperimentPlanPanel(
            kind: _kind,
            completedIds: _completedPlanIds[_kind] ?? <String>{},
            onPickId: (id) {
              setState(() {
                _id.text = id;
                final done = _completedPlanIds.putIfAbsent(
                  _kind,
                  () => <String>{},
                );
                done.contains(id) ? done.remove(id) : done.add(id);
              });
            },
          ),
        const SizedBox(height: 14),
        FutureBuilder<List<Object>>(
          future: Future.wait([_sessionsFuture, _analysisFuture]),
          builder: (context, snapshot) {
            final sessions =
                (snapshot.data?[0] as List<CaptureSessionInfo>?) ?? [];
            final analysis =
                (snapshot.data?[1] as SolverAnalysis?) ??
                SolverAnalysis.empty();
            if (_selectedSession != null &&
                !sessions.contains(_selectedSession)) {
              _selectedSession = null;
            }
            if (_selectedSession == null && sessions.isNotEmpty) {
              _selectedSession = sessions.first;
            }
            if (_selectedLayoutPath != null &&
                !analysis.layouts.any((l) => l.path == _selectedLayoutPath)) {
              _selectedLayoutPath = null;
            }
            if (widget.selectedLayoutPath != null &&
                analysis.layouts.any(
                  (l) => l.path == widget.selectedLayoutPath,
                )) {
              _selectedLayoutPath = widget.selectedLayoutPath;
            }
            if (_selectedLayoutPath == null && analysis.layouts.isNotEmpty) {
              _selectedLayoutPath = analysis.layouts.last.path;
              WidgetsBinding.instance.addPostFrameCallback((_) {
                if (mounted) {
                  widget.onSelectedLayoutPathChanged(_selectedLayoutPath);
                }
              });
            }
            return Column(
              children: [
                RealtimeMotionCaptureCard(
                  runner: widget.runner,
                  layoutPath: _selectedLayoutPath,
                  expectedTags: _activeExpectedTags,
                ),
                const SizedBox(height: 14),
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(14),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            const Text(
                              'Capture Playback',
                              style: TextStyle(
                                fontSize: 18,
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                            const Spacer(),
                            TextButton.icon(
                              onPressed: _refreshPlayback,
                              icon: const Icon(Icons.refresh),
                              label: const Text('Refresh'),
                            ),
                          ],
                        ),
                        const SizedBox(height: 12),
                        Wrap(
                          spacing: 10,
                          runSpacing: 10,
                          crossAxisAlignment: WrapCrossAlignment.center,
                          children: [
                            CaptureSessionMenu(
                              sessions: sessions,
                              selected: _selectedSession,
                              onSelected: (value) {
                                setState(() {
                                  _selectedSession = value;
                                  _trajectory = null;
                                  _selectedTag = null;
                                  _visibleTags = {};
                                });
                              },
                            ),
                            SolverLayoutMenu(
                              layouts: analysis.layouts,
                              selectedPath: _selectedLayoutPath,
                              onSelected: (value) {
                                setState(() => _selectedLayoutPath = value);
                                widget.onSelectedLayoutPathChanged(value);
                              },
                            ),
                            FilledButton.icon(
                              onPressed:
                                  widget.runner.isRunning ||
                                      _selectedSession == null ||
                                      _selectedLayoutPath == null
                                  ? null
                                  : _exportTrajectory,
                              icon: const Icon(Icons.route_outlined),
                              label: const Text('Export / Load Trajectory'),
                            ),
                          ],
                        ),
                        if (_selectedSession != null) ...[
                          const SizedBox(height: 8),
                          Text(
                            _selectedSession!.path,
                            style: const TextStyle(
                              color: mutedText,
                              fontFamily: 'monospace',
                              fontSize: 11,
                            ),
                          ),
                        ],
                        const SizedBox(height: 14),
                        TrajectoryPlaybackView(
                          data: _trajectory,
                          layoutPath: _selectedLayoutPath,
                          visibleTags: _visibleTags,
                          onVisibleTagsChanged: (tags) {
                            setState(() => _visibleTags = tags);
                          },
                        ),
                      ],
                    ),
                  ),
                ),
              ],
            );
          },
        ),
      ],
    );
  }
}

class RealtimeMotionCaptureCard extends StatefulWidget {
  const RealtimeMotionCaptureCard({
    super.key,
    required this.runner,
    required this.layoutPath,
    required this.expectedTags,
  });

  final ScriptRunner runner;
  final String? layoutPath;
  final Set<String> expectedTags;

  @override
  State<RealtimeMotionCaptureCard> createState() =>
      _RealtimeMotionCaptureCardState();
}

class _RealtimeMotionCaptureCardState extends State<RealtimeMotionCaptureCard> {
  Timer? _timer;
  bool _busy = false;
  TrajectoryData? _data;
  Set<String> _visibleTags = {};
  String? _sourceCapture;
  String? _lastTrAllPath;
  DateTime? _lastTrAllModified;
  int? _lastTrAllSize;
  String _displayMode = 'recent';
  double _dotSize = 8.0;
  Future<AnchorLayoutData?>? _layoutFuture;
  String? _layoutFuturePath;
  String _status = 'idle';
  String? _activeRunName;
  DateTime? _activeRunStartedAt;

  bool get _captureRunning {
    final name = widget.runner.activeName?.toLowerCase() ?? '';
    return widget.runner.isRunning &&
        (name.startsWith('static ') ||
            name.startsWith('roto ') ||
            name.startsWith('wand ') ||
            name.startsWith('free '));
  }

  bool get _captureStreamingReady {
    if (!_captureRunning) return false;
    final log = widget.runner.logTail.join('\n');
    return log.contains('TDMA verified; start TR capture') ||
        RegExp(r'\[CAPTURE\]\s+\[[#.\s]+\]\s+').hasMatch(log) ||
        log.contains(' row_rate=') ||
        log.contains('[capture] final_path=');
  }

  Set<String> get _effectiveExpectedTags {
    if (widget.expectedTags.isNotEmpty) return widget.expectedTags;
    final name = widget.runner.activeName?.trim().toLowerCase() ?? '';
    final parts = name.split(RegExp(r'\s+'));
    final kind = parts.isEmpty ? null : parts.first;
    return {...?captureTargetsByKind[kind]};
  }

  @override
  void initState() {
    super.initState();
    widget.runner.addListener(_runnerChanged);
    _timer = Timer.periodic(
      const Duration(milliseconds: 250),
      (_) => _refresh(),
    );
  }

  @override
  void didUpdateWidget(covariant RealtimeMotionCaptureCard oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.layoutPath != widget.layoutPath) {
      _layoutFuture = null;
      _layoutFuturePath = null;
    }
    if (oldWidget.expectedTags.join(',') != widget.expectedTags.join(',')) {
      _visibleTags = {};
    }
  }

  @override
  void dispose() {
    widget.runner.removeListener(_runnerChanged);
    _timer?.cancel();
    super.dispose();
  }

  void _runnerChanged() {
    if (_captureRunning) {
      final name = widget.runner.activeName;
      if (_activeRunName != name) {
        setState(() {
          _activeRunName = name;
          _activeRunStartedAt = DateTime.now();
          _data = null;
          _sourceCapture = null;
          _lastTrAllPath = null;
          _lastTrAllModified = null;
          _lastTrAllSize = null;
          _visibleTags = {};
          _status = 'waiting for current capture';
        });
      }
      _refresh();
    } else if (mounted) {
      setState(() => _status = 'idle');
    }
  }

  Future<void> _refresh() async {
    if (!_captureRunning || _busy || widget.layoutPath == null) return;
    final runName = widget.runner.activeName;
    final startedAt = _activeRunStartedAt;
    if (runName == null || startedAt == null) return;
    if (!_captureStreamingReady) {
      if (mounted) {
        setState(() {
          _data = null;
          _sourceCapture = null;
          _lastTrAllPath = null;
          _lastTrAllModified = null;
          _lastTrAllSize = null;
          _visibleTags = {};
          _status = 'waiting for TDMA capture start';
        });
      }
      return;
    }
    _busy = true;
    try {
      final source = RealtimeCaptureFinder.currentRunCaptureWithTrAll(
        runName: runName,
        startedAt: startedAt,
      );
      if (source == null) {
        if (mounted) {
          setState(() => _status = 'waiting for current tr_all.csv');
        }
        return;
      }
      if (_lastTrAllPath == source.trAll.path &&
          _lastTrAllModified == source.trAllModified &&
          _lastTrAllSize == source.trAllSize) {
        return;
      }
      final out =
          '/tmp/biospur_realtime_trajectory_${DateTime.now().millisecondsSinceEpoch}.json';
      final expectedTags = _effectiveExpectedTags;
      final result = await Process.run('python3', [
        '$fieldRoot/solver/scripts/export_capture_trajectory.py',
        '--layout',
        widget.layoutPath!,
        '--capture',
        source.capture.path,
        '--out',
        out,
        '--max-frames',
        '0',
        '--max-frames-per-tag',
        '96',
        '--tail',
        '--tail-rows',
        '1400',
        if (expectedTags.isNotEmpty) ...[
          '--tags',
          (expectedTags.toList()..sort()).join(','),
        ],
      ], workingDirectory: repoRoot);
      if (result.exitCode != 0) {
        if (mounted) {
          setState(() => _status = 'solver waiting: exit ${result.exitCode}');
        }
        return;
      }
      final data = await TrajectoryData.read(File(out));
      if (!mounted) return;
      setState(() {
        _data = data;
        _sourceCapture = source.capture.path;
        _lastTrAllPath = source.trAll.path;
        _lastTrAllModified = source.trAllModified;
        _lastTrAllSize = source.trAllSize;
        final preferredTags = expectedTags.isEmpty
            ? data.tags.toSet()
            : data.tags.toSet().intersection(expectedTags);
        if (_visibleTags.isEmpty) {
          _visibleTags = preferredTags;
        } else {
          _visibleTags = _visibleTags.intersection(preferredTags);
          if (_visibleTags.isEmpty) _visibleTags = preferredTags;
        }
        _status = data.solvedFrames > 0 ? 'live' : 'no selected tag frames';
      });
    } catch (exc) {
      if (mounted) setState(() => _status = 'error: $exc');
    } finally {
      _busy = false;
    }
  }

  @override
  Widget build(BuildContext context) {
    final data = _data;
    final active = _captureRunning;
    final currentTime = data?.frames.isEmpty == false
        ? data!.frames.last.hostElapsedS
        : 0.0;
    final expectedTags = _effectiveExpectedTags;
    final visibleTags = data == null
        ? <String>{}
        : (_visibleTags.isEmpty
              ? (expectedTags.isEmpty
                    ? data.tags.toSet()
                    : data.tags.toSet().intersection(expectedTags))
              : _visibleTags.intersection(data.tags.toSet()));
    final missingExpected = data == null
        ? <String>{}
        : expectedTags.difference(data.tags.toSet());
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Text(
                  'Realtime Motion Capture',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
                ),
                const Spacer(),
                StatusPill(
                  label: 'State',
                  value: active ? _status : 'idle',
                  tone: active
                      ? (_status == 'live' ? PillTone.good : PillTone.warn)
                      : PillTone.neutral,
                ),
              ],
            ),
            const SizedBox(height: 10),
            if (widget.layoutPath == null)
              const Text(
                'No solver layout selected. Run/stage anchor layout analysis first.',
                style: TextStyle(color: mutedText),
              )
            else if (data == null)
              Container(
                height: 220,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  border: Border.all(color: panelLine),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(
                  active
                      ? 'Waiting for live solved tag positions...'
                      : 'Start a Static/Roto/Wand/Free capture to show live tag positions.',
                  style: const TextStyle(color: mutedText),
                ),
              )
            else ...[
              Wrap(
                spacing: 10,
                runSpacing: 8,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  StatusPill(
                    label: 'Solved',
                    value: '${data.solvedFrames}/${data.candidateFrames}',
                    tone: data.solvedFrames > 0 ? PillTone.good : PillTone.warn,
                  ),
                  StatusPill(
                    label: 'Tags',
                    value: data.tags.join(', '),
                    tone: missingExpected.isEmpty
                        ? PillTone.neutral
                        : PillTone.warn,
                  ),
                  if (expectedTags.isNotEmpty)
                    StatusPill(
                      label: 'Expected',
                      value: (expectedTags.toList()..sort()).join(', '),
                      tone: missingExpected.isEmpty
                          ? PillTone.good
                          : PillTone.warn,
                    ),
                  if (missingExpected.isNotEmpty)
                    StatusPill(
                      label: 'Missing',
                      value: (missingExpected.toList()..sort()).join(', '),
                      tone: PillTone.warn,
                    ),
                  for (final tag in data.tags)
                    FilterChip(
                      label: Text(tag),
                      selected: visibleTags.contains(tag),
                      onSelected: (selected) {
                        setState(() {
                          final next = {...visibleTags};
                          selected ? next.add(tag) : next.remove(tag);
                          _visibleTags = next;
                        });
                      },
                    ),
                  SegmentedButton<String>(
                    segments: const [
                      ButtonSegment(
                        value: 'dots',
                        icon: Icon(Icons.adjust),
                        label: Text('Dots only'),
                      ),
                      ButtonSegment(
                        value: 'recent',
                        icon: Icon(Icons.timeline),
                        label: Text('Recent trail'),
                      ),
                      ButtonSegment(
                        value: 'trail',
                        icon: Icon(Icons.route_outlined),
                        label: Text('Full trail'),
                      ),
                    ],
                    selected: {_displayMode},
                    onSelectionChanged: (value) {
                      setState(() => _displayMode = value.first);
                    },
                  ),
                  SizedBox(
                    width: 230,
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Text('Dot'),
                        Expanded(
                          child: Slider(
                            value: _dotSize,
                            min: 2.0,
                            max: 14.0,
                            divisions: 12,
                            label: _dotSize.toStringAsFixed(0),
                            onChanged: (value) {
                              setState(() => _dotSize = value);
                            },
                          ),
                        ),
                        SizedBox(
                          width: 28,
                          child: Text(_dotSize.toStringAsFixed(0)),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 10),
              FutureBuilder<AnchorLayoutData?>(
                future: _layoutFor(widget.layoutPath!),
                builder: (context, snapshot) {
                  final layout = snapshot.data;
                  return LayoutBuilder(
                    builder: (context, constraints) {
                      final wide = constraints.maxWidth >= 900;
                      final allView = Trajectory3DView(
                        data: data,
                        layout: layout,
                        visibleTags: data.tags.toSet(),
                        currentTimeS: currentTime,
                        compactLabel: 'All live tags',
                        displayMode: _displayMode,
                        dotSize: _dotSize,
                      );
                      final selectedView = Trajectory3DView(
                        data: data,
                        layout: layout,
                        visibleTags: visibleTags,
                        currentTimeS: currentTime,
                        compactLabel: 'Selected live tags',
                        displayMode: _displayMode,
                        dotSize: _dotSize,
                      );
                      if (!wide) {
                        return Column(
                          children: [
                            SizedBox(height: 260, child: allView),
                            const SizedBox(height: 10),
                            SizedBox(height: 320, child: selectedView),
                          ],
                        );
                      }
                      return Row(
                        children: [
                          Expanded(
                            child: SizedBox(height: 360, child: allView),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: SizedBox(height: 360, child: selectedView),
                          ),
                        ],
                      );
                    },
                  );
                },
              ),
              if (_sourceCapture != null) ...[
                const SizedBox(height: 8),
                Text(
                  _sourceCapture!,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: mutedText,
                    fontFamily: 'monospace',
                    fontSize: 11,
                  ),
                ),
              ],
            ],
          ],
        ),
      ),
    );
  }

  Future<AnchorLayoutData?> _layoutFor(String path) {
    if (_layoutFuturePath != path || _layoutFuture == null) {
      _layoutFuturePath = path;
      _layoutFuture = AnchorLayoutData.read('live-layout', File(path));
    }
    return _layoutFuture!;
  }
}

class RealtimeCaptureFinder {
  static RealtimeCaptureSource? currentRunCaptureWithTrAll({
    required String runName,
    required DateTime startedAt,
  }) {
    final target = _targetFromRunName(runName);
    if (target == null) return null;
    final root = Directory(capturesRoot);
    if (!root.existsSync()) return null;
    final candidates = <RealtimeCaptureSource>[];
    final minModified = startedAt.subtract(const Duration(seconds: 2));
    for (final dir in root.listSync().whereType<Directory>()) {
      final name = dir.path.split('/').last;
      if (!name.startsWith(target.directoryPrefix)) continue;
      final stat = dir.statSync();
      if (stat.modified.isBefore(minModified)) continue;
      final files =
          dir.listSync(recursive: true).whereType<File>().where((file) {
            final isRangeSource =
                file.path.endsWith('/tr_all.csv') ||
                file.path.endsWith('/raw.log');
            if (!isRangeSource) return false;
            if (file.lengthSync() <= 0) return false;
            return !file.statSync().modified.isBefore(minModified);
          }).toList()..sort((a, b) {
            final aIsCsv = a.path.endsWith('/tr_all.csv');
            final bIsCsv = b.path.endsWith('/tr_all.csv');
            if (aIsCsv != bIsCsv) return aIsCsv ? -1 : 1;
            return b.statSync().modified.compareTo(a.statSync().modified);
          });
      if (files.isEmpty) continue;
      final trAll = files.first;
      candidates.add(
        RealtimeCaptureSource(
          capture: dir,
          trAll: trAll,
          trAllModified: trAll.statSync().modified,
          trAllSize: trAll.lengthSync(),
        ),
      );
    }
    if (candidates.isEmpty) return null;
    candidates.sort((a, b) => b.trAllModified.compareTo(a.trAllModified));
    return candidates.first;
  }

  static _RealtimeCaptureTarget? _targetFromRunName(String runName) {
    final parts = runName.trim().split(RegExp(r'\s+'));
    if (parts.length < 2) return null;
    final kind = parts[0].toLowerCase();
    final id = parts[1];
    final prefix = switch (kind) {
      'static' => 'static_${id}_',
      'roto' => 'roto_${id}_',
      'wand' => 'wand3_${id}_',
      'free' => 'free_${id}_',
      _ => null,
    };
    if (prefix == null) return null;
    return _RealtimeCaptureTarget(directoryPrefix: prefix);
  }

  static RealtimeCaptureSource? latestCaptureWithTrAll() {
    final root = Directory(capturesRoot);
    if (!root.existsSync()) return null;
    final candidates = <RealtimeCaptureSource>[];
    for (final dir in root.listSync().whereType<Directory>()) {
      final name = dir.path.split('/').last;
      if (!RegExp(r'^(static|roto|wand3|free)_').hasMatch(name)) continue;
      final files =
          dir
              .listSync(recursive: true)
              .whereType<File>()
              .where(
                (file) =>
                    file.path.endsWith('/tr_all.csv') && file.lengthSync() > 0,
              )
              .toList()
            ..sort(
              (a, b) => b.statSync().modified.compareTo(a.statSync().modified),
            );
      if (files.isEmpty) continue;
      final trAll = files.first;
      candidates.add(
        RealtimeCaptureSource(
          capture: dir,
          trAll: trAll,
          trAllModified: trAll.statSync().modified,
          trAllSize: trAll.lengthSync(),
        ),
      );
    }
    if (candidates.isEmpty) return null;
    candidates.sort((a, b) => b.trAllModified.compareTo(a.trAllModified));
    return candidates.first;
  }
}

class _RealtimeCaptureTarget {
  const _RealtimeCaptureTarget({required this.directoryPrefix});

  final String directoryPrefix;
}

class RealtimeCaptureSource {
  const RealtimeCaptureSource({
    required this.capture,
    required this.trAll,
    required this.trAllModified,
    required this.trAllSize,
  });

  final Directory capture;
  final File trAll;
  final DateTime trAllModified;
  final int trAllSize;
}

class ExperimentPlanPanel extends StatelessWidget {
  const ExperimentPlanPanel({
    super.key,
    required this.kind,
    required this.completedIds,
    required this.onPickId,
  });

  final String kind;
  final Set<String> completedIds;
  final void Function(String id) onPickId;

  String get _title {
    switch (kind) {
      case 'roto':
        return 'RotoArm Plan';
      case 'wand':
        return 'Wand Plan';
      default:
        return 'Static Plan';
    }
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<ExperimentPlan>(
      future: ExperimentPlan.read(),
      builder: (context, snapshot) {
        final plan = snapshot.data ?? ExperimentPlan.empty();
        final section = plan.sectionFor(kind);
        return Card(
          child: Theme(
            data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
            child: ExpansionTile(
              tilePadding: const EdgeInsets.symmetric(horizontal: 14),
              childrenPadding: const EdgeInsets.fromLTRB(14, 0, 14, 14),
              iconColor: controlGreen,
              collapsedIconColor: controlGreen,
              leading: const Icon(Icons.fact_check_outlined),
              title: Text(
                _title,
                style: const TextStyle(
                  fontSize: 17,
                  fontWeight: FontWeight.w700,
                ),
              ),
              subtitle: Text(
                section.rows.isEmpty
                    ? 'No experiment plan entries loaded.'
                    : '${section.rows.length} recommended IDs from experiment_plan_short.md',
                style: const TextStyle(color: mutedText),
              ),
              children: [
                if (section.minimum.isNotEmpty) ...[
                  Align(
                    alignment: Alignment.centerLeft,
                    child: Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        const Text(
                          'Minimum:',
                          style: TextStyle(fontWeight: FontWeight.w700),
                        ),
                        for (final id in section.minimum)
                          FilterChip(
                            label: Text(id),
                            selected: completedIds.contains(id),
                            onSelected: (_) => onPickId(id),
                          ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 12),
                ],
                if (section.rows.isEmpty)
                  const Align(
                    alignment: Alignment.centerLeft,
                    child: Text('Check docs/experiment_plan_short.md.'),
                  )
                else
                  PlanRowsTable(
                    rows: section.rows,
                    completedIds: completedIds,
                    onPickId: onPickId,
                  ),
                if (section.notes.isNotEmpty) ...[
                  const SizedBox(height: 12),
                  Align(
                    alignment: Alignment.centerLeft,
                    child: Text(
                      section.notes.join('\n'),
                      style: const TextStyle(color: mutedText, height: 1.35),
                    ),
                  ),
                ],
              ],
            ),
          ),
        );
      },
    );
  }
}

class FreeTagSelectionPanel extends StatelessWidget {
  const FreeTagSelectionPanel({
    super.key,
    required this.knownTags,
    required this.selectedTags,
    required this.onCheckAvailable,
    required this.onToggleTag,
  });

  final Set<String> knownTags;
  final Set<String> selectedTags;
  final VoidCallback onCheckAvailable;
  final ValueChanged<String> onToggleTag;

  @override
  Widget build(BuildContext context) {
    final tags = ({...freeCaptureTags, ...knownTags}.toList()..sort());
    final selected = selectedTags.toList()..sort();
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.radar_outlined, color: controlGreen),
                const SizedBox(width: 10),
                const Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Free Tag Selection',
                        style: TextStyle(
                          fontSize: 17,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      SizedBox(height: 2),
                      Text(
                        'Select the BS tags that should enter this capture roster.',
                        style: TextStyle(color: mutedText),
                      ),
                    ],
                  ),
                ),
                TextButton.icon(
                  onPressed: onCheckAvailable,
                  icon: const Icon(Icons.refresh),
                  label: const Text('Check Available Tags'),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final tag in tags)
                  FilterChip(
                    label: Text(tag),
                    selected: selectedTags.contains(tag),
                    onSelected: (_) => onToggleTag(tag),
                  ),
              ],
            ),
            const SizedBox(height: 10),
            Text(
              selected.isEmpty
                  ? 'Selected targets: none'
                  : 'Selected targets: ${selected.join(', ')}',
              style: const TextStyle(color: mutedText),
            ),
            const SizedBox(height: 4),
            const Text(
              'Start Capture passes only these targets to TDMA roster. Powered-on tags that are not selected are not part of this run.',
              style: TextStyle(color: mutedText),
            ),
          ],
        ),
      ),
    );
  }
}

class PlanRowsTable extends StatefulWidget {
  const PlanRowsTable({
    super.key,
    required this.rows,
    required this.completedIds,
    required this.onPickId,
  });

  final List<ExperimentPlanRow> rows;
  final Set<String> completedIds;
  final void Function(String id) onPickId;

  @override
  State<PlanRowsTable> createState() => _PlanRowsTableState();
}

class _PlanRowsTableState extends State<PlanRowsTable> {
  final ScrollController _controller = ScrollController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final columns = constraints.maxWidth >= 760 ? 2 : 1;
        return GridView.builder(
          controller: _controller,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
            crossAxisCount: columns,
            mainAxisExtent: 46,
            crossAxisSpacing: 14,
            mainAxisSpacing: 8,
          ),
          itemCount: widget.rows.length,
          itemBuilder: (context, index) {
            final row = widget.rows[index];
            final done = widget.completedIds.contains(row.id);
            return Container(
              decoration: BoxDecoration(
                color: done
                    ? controlGreen.withValues(alpha: 0.16)
                    : Colors.transparent,
                border: Border.all(color: panelLine),
                borderRadius: BorderRadius.circular(8),
              ),
              padding: const EdgeInsets.symmetric(horizontal: 8),
              child: Row(
                children: [
                  FilterChip(
                    label: Text(row.id),
                    selected: done,
                    onSelected: (_) => widget.onPickId(row.id),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      row.description,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
              ),
            );
          },
        );
      },
    );
  }
}

class TrajectoryPlaybackView extends StatefulWidget {
  const TrajectoryPlaybackView({
    super.key,
    required this.data,
    required this.layoutPath,
    required this.visibleTags,
    required this.onVisibleTagsChanged,
  });

  final TrajectoryData? data;
  final String? layoutPath;
  final Set<String> visibleTags;
  final ValueChanged<Set<String>> onVisibleTagsChanged;

  @override
  State<TrajectoryPlaybackView> createState() => _TrajectoryPlaybackViewState();
}

class CaptureSessionMenu extends StatefulWidget {
  const CaptureSessionMenu({
    super.key,
    required this.sessions,
    required this.selected,
    required this.onSelected,
  });

  final List<CaptureSessionInfo> sessions;
  final CaptureSessionInfo? selected;
  final ValueChanged<CaptureSessionInfo?> onSelected;

  @override
  State<CaptureSessionMenu> createState() => _CaptureSessionMenuState();
}

class _CaptureSessionMenuState extends State<CaptureSessionMenu> {
  @override
  Widget build(BuildContext context) {
    final sessions = widget.sessions;
    final selected = widget.selected;
    final selectedText = selected == null
        ? 'No capture selected'
        : '${selected.kind} ${selected.id}  ${selected.modifiedLabel}';
    return AnchoredInlineMenu(
      width: 430,
      label: 'Capture session',
      value: selectedText,
      enabled: sessions.isNotEmpty,
      menuHeight: math.min(340.0, math.max(56.0, sessions.length * 44.0)),
      itemBuilder: (close) => [
        for (final session in sessions)
          InlineMenuItem(
            selected: session == selected,
            icon: Icons.folder_open_outlined,
            label: '${session.kind} ${session.id}  ${session.modifiedLabel}',
            onTap: () {
              widget.onSelected(session);
              close();
            },
          ),
      ],
    );
  }
}

class SolverSweepMenu extends StatefulWidget {
  const SolverSweepMenu({
    super.key,
    required this.sweeps,
    required this.selected,
    required this.onSelected,
  });

  final List<SolverSweepInfo> sweeps;
  final SolverSweepInfo? selected;
  final ValueChanged<SolverSweepInfo?> onSelected;

  @override
  State<SolverSweepMenu> createState() => _SolverSweepMenuState();
}

class _SolverSweepMenuState extends State<SolverSweepMenu> {
  @override
  Widget build(BuildContext context) {
    final selected = widget.selected;
    return AnchoredInlineMenu(
      width: 430,
      label: 'Solver sweep',
      value: selected == null
          ? 'Auto: latest complete sweep'
          : '${selected.id}  ${selected.swSets} sets  ${selected.modifiedLabel}',
      enabled: true,
      menuHeight: math.min(
        340.0,
        math.max(88.0, (widget.sweeps.length + 1) * 44.0),
      ),
      itemBuilder: (close) => [
        InlineMenuItem(
          selected: selected == null,
          icon: Icons.auto_awesome_motion_outlined,
          label: 'Auto: latest complete sweep',
          onTap: () {
            widget.onSelected(null);
            close();
          },
        ),
        for (final sweep in widget.sweeps)
          InlineMenuItem(
            selected: sweep == selected,
            icon: Icons.grid_4x4_outlined,
            label: '${sweep.id}  ${sweep.swSets} sets  ${sweep.modifiedLabel}',
            onTap: () {
              widget.onSelected(sweep);
              close();
            },
          ),
      ],
    );
  }
}

class SolverModeMenu extends StatefulWidget {
  const SolverModeMenu({
    super.key,
    required this.selected,
    required this.onSelected,
  });

  final String selected;
  final ValueChanged<String?> onSelected;

  @override
  State<SolverModeMenu> createState() => _SolverModeMenuState();
}

class _SolverModeMenuState extends State<SolverModeMenu> {
  static const modes = <String, String>{
    'v1-v4': 'V1 to V4-io',
    'v4-io': 'V4-io only',
  };

  @override
  Widget build(BuildContext context) {
    return AnchoredInlineMenu(
      width: 190,
      label: 'Solver run',
      value: modes[widget.selected] ?? widget.selected,
      enabled: true,
      menuHeight: 92,
      itemBuilder: (close) => [
        for (final entry in modes.entries)
          InlineMenuItem(
            selected: entry.key == widget.selected,
            icon: entry.key == 'v4-io'
                ? Icons.analytics_outlined
                : Icons.stacked_line_chart,
            label: entry.value,
            onTap: () {
              widget.onSelected(entry.key);
              close();
            },
          ),
      ],
    );
  }
}

class SolverLayoutMenu extends StatefulWidget {
  const SolverLayoutMenu({
    super.key,
    required this.layouts,
    required this.selectedPath,
    required this.onSelected,
  });

  final List<AnchorLayoutData> layouts;
  final String? selectedPath;
  final ValueChanged<String?> onSelected;

  @override
  State<SolverLayoutMenu> createState() => _SolverLayoutMenuState();
}

class VersionMenu extends StatefulWidget {
  const VersionMenu({
    super.key,
    required this.versions,
    required this.selected,
    required this.label,
    required this.width,
    required this.onSelected,
  });

  final List<String> versions;
  final String? selected;
  final String label;
  final double width;
  final ValueChanged<String?> onSelected;

  @override
  State<VersionMenu> createState() => _VersionMenuState();
}

class _VersionMenuState extends State<VersionMenu> {
  @override
  Widget build(BuildContext context) {
    return AnchoredInlineMenu(
      width: widget.width,
      label: widget.label,
      value: widget.selected ?? 'No layout',
      enabled: widget.versions.isNotEmpty,
      menuHeight: math.min(
        260.0,
        math.max(48.0, widget.versions.length * 42.0),
      ),
      itemBuilder: (close) => [
        for (final version in widget.versions)
          InlineMenuItem(
            selected: version == widget.selected,
            icon: Icons.view_in_ar_outlined,
            label: version,
            onTap: () {
              widget.onSelected(version);
              close();
            },
          ),
      ],
    );
  }
}

class _SolverLayoutMenuState extends State<SolverLayoutMenu> {
  @override
  Widget build(BuildContext context) {
    AnchorLayoutData? selected;
    for (final layout in widget.layouts) {
      if (layout.path == widget.selectedPath) {
        selected = layout;
        break;
      }
    }
    return AnchoredInlineMenu(
      width: 250,
      label: 'Solver layout',
      value: selected?.version ?? 'No layout',
      enabled: widget.layouts.isNotEmpty,
      menuHeight: math.min(260.0, math.max(48.0, widget.layouts.length * 42.0)),
      itemBuilder: (close) => [
        for (final layout in widget.layouts)
          InlineMenuItem(
            selected: layout.path == widget.selectedPath,
            icon: Icons.view_in_ar_outlined,
            label: layout.version,
            onTap: () {
              widget.onSelected(layout.path);
              close();
            },
          ),
      ],
    );
  }
}

class SpeedMenu extends StatefulWidget {
  const SpeedMenu({
    super.key,
    required this.value,
    required this.enabled,
    required this.onSelected,
  });

  final double value;
  final bool enabled;
  final ValueChanged<double> onSelected;

  @override
  State<SpeedMenu> createState() => _SpeedMenuState();
}

class _SpeedMenuState extends State<SpeedMenu> {
  static const speeds = [0.25, 0.5, 1.0, 2.0, 5.0, 10.0];

  @override
  Widget build(BuildContext context) {
    String label(double value) => value == value.roundToDouble()
        ? '${value.toStringAsFixed(0)}x'
        : '${value}x';

    final current = speeds.indexWhere((speed) => speed == widget.value);
    final index = current >= 0 ? current : speeds.indexOf(1.0);
    final canStep = widget.enabled && speeds.isNotEmpty;

    void step(int delta) {
      if (!canStep) return;
      final next = (index + delta) % speeds.length;
      widget.onSelected(speeds[next < 0 ? next + speeds.length : next]);
    }

    final borderColor = widget.enabled
        ? controlGreen.withValues(alpha: 0.72)
        : controlGreen.withValues(alpha: 0.22);
    final textColor = widget.enabled
        ? Colors.white
        : mutedText.withValues(alpha: 0.45);

    return SizedBox(
      width: 128,
      height: 48,
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: Colors.black.withValues(alpha: 0.18),
          border: Border.all(color: borderColor),
          borderRadius: BorderRadius.circular(6),
        ),
        child: Row(
          children: [
            SizedBox(
              width: 34,
              child: IconButton(
                tooltip: 'Slower',
                onPressed: canStep ? () => step(-1) : null,
                icon: const Icon(Icons.chevron_left),
                iconSize: 20,
                padding: EdgeInsets.zero,
                color: controlGreen,
                disabledColor: mutedText.withValues(alpha: 0.3),
              ),
            ),
            Expanded(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Text(
                    'Speed',
                    style: TextStyle(
                      color: mutedText.withValues(alpha: 0.82),
                      fontSize: 10,
                      height: 1.0,
                    ),
                  ),
                  const SizedBox(height: 3),
                  Text(
                    label(widget.value),
                    style: TextStyle(
                      color: textColor,
                      fontSize: 15,
                      fontWeight: FontWeight.w800,
                      height: 1.0,
                    ),
                  ),
                ],
              ),
            ),
            SizedBox(
              width: 34,
              child: IconButton(
                tooltip: 'Faster',
                onPressed: canStep ? () => step(1) : null,
                icon: const Icon(Icons.chevron_right),
                iconSize: 20,
                padding: EdgeInsets.zero,
                color: controlGreen,
                disabledColor: mutedText.withValues(alpha: 0.3),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class AnchoredInlineMenu extends StatefulWidget {
  const AnchoredInlineMenu({
    super.key,
    required this.width,
    required this.label,
    required this.value,
    required this.enabled,
    required this.menuHeight,
    required this.itemBuilder,
  });

  final double width;
  final String label;
  final String value;
  final bool enabled;
  final double menuHeight;
  final List<Widget> Function(VoidCallback close) itemBuilder;

  @override
  State<AnchoredInlineMenu> createState() => _AnchoredInlineMenuState();
}

class _AnchoredInlineMenuState extends State<AnchoredInlineMenu> {
  final LayerLink _link = LayerLink();
  OverlayEntry? _entry;
  bool _open = false;

  @override
  void dispose() {
    _close();
    super.dispose();
  }

  void _toggle() {
    if (!widget.enabled) return;
    _open ? _close() : _openMenu();
  }

  void _openMenu() {
    if (_entry != null) return;
    setState(() => _open = true);
    _entry = OverlayEntry(
      builder: (context) {
        return Stack(
          children: [
            Positioned.fill(
              child: GestureDetector(
                behavior: HitTestBehavior.translucent,
                onTap: _close,
              ),
            ),
            CompositedTransformFollower(
              link: _link,
              showWhenUnlinked: false,
              offset: const Offset(0, 52),
              child: Material(
                color: Colors.transparent,
                child: SizedBox(
                  width: widget.width,
                  child: InlineMenuList(
                    height: widget.menuHeight,
                    children: widget.itemBuilder(_close),
                  ),
                ),
              ),
            ),
          ],
        );
      },
    );
    Overlay.of(context).insert(_entry!);
  }

  void _close() {
    _entry?.remove();
    _entry = null;
    if (mounted && _open) {
      setState(() => _open = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: widget.width,
      child: CompositedTransformTarget(
        link: _link,
        child: InlineMenuField(
          label: widget.label,
          value: widget.value,
          open: _open,
          enabled: widget.enabled,
          onTap: _toggle,
        ),
      ),
    );
  }
}

class InlineMenuField extends StatelessWidget {
  const InlineMenuField({
    super.key,
    required this.label,
    required this.value,
    required this.open,
    required this.enabled,
    required this.onTap,
  });

  final String label;
  final String value;
  final bool open;
  final bool enabled;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: enabled ? onTap : null,
      borderRadius: BorderRadius.circular(4),
      child: InputDecorator(
        decoration: InputDecoration(
          labelText: label,
          border: const OutlineInputBorder(),
          isDense: true,
          suffixIcon: Icon(open ? Icons.arrow_drop_up : Icons.arrow_drop_down),
        ),
        child: Text(
          value,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(color: enabled ? Colors.white : mutedText),
        ),
      ),
    );
  }
}

class InlineMenuList extends StatelessWidget {
  const InlineMenuList({
    super.key,
    required this.height,
    required this.children,
  });

  final double height;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      height: height,
      margin: const EdgeInsets.only(top: 4),
      decoration: BoxDecoration(
        color: const Color(0xFF050806),
        border: Border.all(color: controlGreen.withValues(alpha: 0.55)),
        borderRadius: BorderRadius.circular(8),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.70),
            blurRadius: 18,
            offset: const Offset(0, 8),
          ),
        ],
      ),
      child: ListView.separated(
        padding: const EdgeInsets.symmetric(vertical: 4),
        itemCount: children.length,
        separatorBuilder: (_, _) =>
            Divider(height: 1, color: panelLine.withValues(alpha: 0.8)),
        itemBuilder: (context, index) => children[index],
      ),
    );
  }
}

class InlineMenuItem extends StatelessWidget {
  const InlineMenuItem({
    super.key,
    required this.selected,
    required this.icon,
    required this.label,
    required this.onTap,
  });

  final bool selected;
  final IconData icon;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      child: Container(
        height: 40,
        padding: const EdgeInsets.symmetric(horizontal: 12),
        color: selected
            ? controlGreen.withValues(alpha: 0.18)
            : Colors.transparent,
        child: Row(
          children: [
            Icon(
              selected ? Icons.check : icon,
              color: selected ? controlGreen : mutedText,
              size: 18,
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                label,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  color: selected ? controlGreen : Colors.white,
                  fontWeight: selected ? FontWeight.w800 : FontWeight.w600,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _TrajectoryPlaybackViewState extends State<TrajectoryPlaybackView> {
  double _frame = 0;
  double _speed = 1.0;
  double _dotSize = 8.0;
  String _displayMode = 'dots';
  bool _loopPlayback = false;
  bool _resumeAfterScrub = false;
  Timer? _playTimer;
  DateTime? _lastPlayTick;
  List<TrajectoryFrame> _playFrames = [];

  @override
  void dispose() {
    _playTimer?.cancel();
    super.dispose();
  }

  @override
  void didUpdateWidget(covariant TrajectoryPlaybackView oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.data != widget.data) {
      _stopPlayback(reset: true);
      _frame = 0;
    }
  }

  bool get _isPlaying => _playTimer != null;

  void _play() {
    if (_playFrames.isEmpty) return;
    if (_frame >= _playFrames.length - 1) {
      _frame = 0;
    }
    _lastPlayTick = DateTime.now();
    _playTimer?.cancel();
    _playTimer = Timer.periodic(const Duration(milliseconds: 50), (_) {
      _tickPlayback();
    });
    setState(() {});
  }

  void _pause() {
    _playTimer?.cancel();
    _playTimer = null;
    _lastPlayTick = null;
    setState(() {});
  }

  void _beginScrub(double _) {
    _resumeAfterScrub = _isPlaying;
    if (_resumeAfterScrub) _pause();
  }

  void _endScrub(double _) {
    if (!_resumeAfterScrub) return;
    _resumeAfterScrub = false;
    _play();
  }

  void _stopPlayback({bool reset = false}) {
    _playTimer?.cancel();
    _playTimer = null;
    _lastPlayTick = null;
    if (reset) _frame = 0;
  }

  void _tickPlayback() {
    if (_playFrames.isEmpty) {
      _stopPlayback();
      return;
    }
    final now = DateTime.now();
    final last = _lastPlayTick ?? now;
    _lastPlayTick = now;
    final dt = now.difference(last).inMicroseconds / 1000000.0;
    final currentIndex = _frame.round().clamp(0, _playFrames.length - 1);
    final currentTime = _playFrames[currentIndex].hostElapsedS;
    final targetTime = currentTime + dt * _speed;
    var next = currentIndex;
    while (next < _playFrames.length - 1 &&
        _playFrames[next + 1].hostElapsedS <= targetTime) {
      next++;
    }
    if (next == currentIndex && currentIndex < _playFrames.length - 1) {
      final nextTime = _playFrames[currentIndex + 1].hostElapsedS;
      if (nextTime <= currentTime || targetTime > currentTime) next++;
    }
    setState(() {
      _frame = next.toDouble();
      if (next >= _playFrames.length - 1) {
        if (_loopPlayback && _playFrames.length > 1) {
          _frame = 0;
          _lastPlayTick = DateTime.now();
        } else {
          _stopPlayback();
        }
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final data = widget.data;
    if (data == null) {
      return Container(
        height: 260,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          border: Border.all(color: panelLine),
          borderRadius: BorderRadius.circular(8),
        ),
        child: const Text(
          'No trajectory loaded. Export a capture session first.',
        ),
      );
    }
    final visibleTags = widget.visibleTags.isEmpty
        ? data.tags.toSet()
        : widget.visibleTags.intersection(data.tags.toSet());
    final expectedTags = data.expectedTags.toSet();
    final missingExpected = expectedTags.difference(data.tags.toSet());
    final timelineFrames = data.framesForTags(data.tags.toSet());
    _playFrames = timelineFrames;
    final maxIndex = math.max(0, timelineFrames.length - 1);
    _frame = _frame.clamp(0, maxIndex.toDouble());
    final current = timelineFrames.isEmpty
        ? null
        : timelineFrames[_frame.round()];
    final currentTime = current?.hostElapsedS ?? 0.0;
    final estimates = data.estimatesForTags(visibleTags);
    final layoutFuture = widget.layoutPath == null
        ? Future<AnchorLayoutData?>.value(null)
        : AnchorLayoutData.read('layout', File(widget.layoutPath!));
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          spacing: 10,
          runSpacing: 10,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            SizedBox(
              width: 130,
              child: StatusPill(
                label: 'Solved',
                value: '${data.solvedFrames}/${data.candidateFrames}',
                tone: data.solvedFrames > 0 ? PillTone.good : PillTone.bad,
              ),
            ),
            SizedBox(
              width: 170,
              child: StatusPill(
                label: 'Tags',
                value: data.tags.isEmpty ? '-' : data.tags.join(', '),
                tone: missingExpected.isEmpty
                    ? PillTone.neutral
                    : PillTone.warn,
              ),
            ),
            if (expectedTags.isNotEmpty)
              SizedBox(
                width: 250,
                child: StatusPill(
                  label: 'Expected',
                  value: (expectedTags.toList()..sort()).join(', '),
                  tone: missingExpected.isEmpty ? PillTone.good : PillTone.warn,
                ),
              ),
            if (missingExpected.isNotEmpty)
              SizedBox(
                width: 250,
                child: StatusPill(
                  label: 'Missing',
                  value: (missingExpected.toList()..sort())
                      .map((tag) {
                        final candidates = data.candidateFramesByTag[tag] ?? 0;
                        final solved = data.solvedFramesByTag[tag] ?? 0;
                        return '$tag $solved/$candidates';
                      })
                      .join(', '),
                  tone: PillTone.warn,
                ),
              ),
            if (current != null)
              SizedBox(
                width: 250,
                child: StatusPill(
                  label: 'Frame',
                  value:
                      't ${current.hostElapsedS.toStringAsFixed(2)} s  residual ${current.residualRmsMm.toStringAsFixed(1)} mm',
                  tone: PillTone.neutral,
                ),
              ),
            FilterChip(
              label: const Text('All'),
              selected: visibleTags.length == data.tags.length,
              onSelected: (selected) {
                widget.onVisibleTagsChanged(
                  selected ? data.tags.toSet() : <String>{},
                );
              },
            ),
            for (final tag in data.tags)
              FilterChip(
                label: Text(tag),
                selected: visibleTags.contains(tag),
                onSelected: (selected) {
                  final next = {...visibleTags};
                  if (selected) {
                    next.add(tag);
                  } else {
                    next.remove(tag);
                  }
                  widget.onVisibleTagsChanged(next);
                },
              ),
            SegmentedButton<String>(
              segments: const [
                ButtonSegment(
                  value: 'dots',
                  icon: Icon(Icons.adjust),
                  label: Text('Dots only'),
                ),
                ButtonSegment(
                  value: 'recent',
                  icon: Icon(Icons.timeline),
                  label: Text('Recent trail'),
                ),
                ButtonSegment(
                  value: 'trail',
                  icon: Icon(Icons.route_outlined),
                  label: Text('Full trail'),
                ),
              ],
              selected: {_displayMode},
              onSelectionChanged: (value) {
                setState(() => _displayMode = value.first);
              },
            ),
            SizedBox(
              width: 230,
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text('Dot'),
                  Expanded(
                    child: Slider(
                      value: _dotSize,
                      min: 2.0,
                      max: 14.0,
                      divisions: 12,
                      label: _dotSize.toStringAsFixed(0),
                      onChanged: (value) => setState(() => _dotSize = value),
                    ),
                  ),
                  SizedBox(width: 28, child: Text(_dotSize.toStringAsFixed(0))),
                ],
              ),
            ),
          ],
        ),
        if (estimates.isNotEmpty) ...[
          const SizedBox(height: 8),
          Wrap(
            spacing: 10,
            runSpacing: 8,
            children: [
              for (final e in estimates)
                StatusPill(
                  label: e.tag,
                  value:
                      'R ${fmtMm(e.radiusMm)}  C(${fmtMm(e.centerX)}, ${fmtMm(e.centerY)}, ${fmtMm(e.centerZ)})  rms ${fmtMm(e.radiusRmsMm)}',
                  tone: PillTone.neutral,
                ),
            ],
          ),
        ],
        const SizedBox(height: 10),
        FutureBuilder<AnchorLayoutData?>(
          future: layoutFuture,
          builder: (context, snapshot) {
            final layout = snapshot.data;
            final allView = Trajectory3DView(
              data: data,
              layout: layout,
              visibleTags: data.tags.toSet(),
              currentTimeS: currentTime,
              compactLabel: 'All trajectories',
              displayMode: _displayMode,
              dotSize: _dotSize,
            );
            final selectedView = Trajectory3DView(
              data: data,
              layout: layout,
              visibleTags: visibleTags,
              currentTimeS: currentTime,
              compactLabel: 'Selected trajectories',
              displayMode: _displayMode,
              dotSize: _dotSize,
            );
            final detail = StatusPill(
              label: 'Detail',
              value: visibleTags.isEmpty ? 'none' : visibleTags.join(', '),
              tone: visibleTags.isEmpty ? PillTone.bad : PillTone.neutral,
            );
            return LayoutBuilder(
              builder: (context, constraints) {
                final wide = constraints.maxWidth >= 900;
                if (!wide) {
                  return Column(
                    children: [
                      SizedBox(height: 260, child: allView),
                      const SizedBox(height: 10),
                      Align(alignment: Alignment.centerLeft, child: detail),
                      const SizedBox(height: 10),
                      SizedBox(height: 380, child: selectedView),
                    ],
                  );
                }
                return Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          StatusPill(
                            label: 'View',
                            value: 'all trajectories',
                            tone: PillTone.neutral,
                          ),
                          const SizedBox(height: 8),
                          SizedBox(height: 460, child: allView),
                        ],
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          detail,
                          const SizedBox(height: 8),
                          SizedBox(height: 460, child: selectedView),
                        ],
                      ),
                    ),
                  ],
                );
              },
            );
          },
        ),
        Row(
          children: [
            IconButton(
              tooltip: _isPlaying ? 'Pause' : 'Play',
              onPressed: timelineFrames.isEmpty
                  ? null
                  : (_isPlaying ? _pause : _play),
              icon: Icon(_isPlaying ? Icons.pause : Icons.play_arrow),
            ),
            IconButton(
              tooltip: 'Stop',
              onPressed: timelineFrames.isEmpty
                  ? null
                  : () => setState(() => _stopPlayback(reset: true)),
              icon: const Icon(Icons.stop),
            ),
            IconButton(
              tooltip: _loopPlayback ? 'Loop on' : 'Loop off',
              onPressed: timelineFrames.isEmpty
                  ? null
                  : () => setState(() => _loopPlayback = !_loopPlayback),
              icon: Icon(
                Icons.repeat,
                color: _loopPlayback ? controlGreen : mutedText,
              ),
            ),
            const SizedBox(width: 4),
            SpeedMenu(
              value: _speed,
              enabled: timelineFrames.isNotEmpty,
              onSelected: (value) {
                setState(() => _speed = value);
              },
            ),
            const SizedBox(width: 12),
            const Text('Time'),
            Expanded(
              child: Slider(
                value: _frame,
                min: 0,
                max: maxIndex.toDouble(),
                divisions: maxIndex > 0 ? math.min(maxIndex, 500) : null,
                onChanged: timelineFrames.isEmpty
                    ? null
                    : (value) {
                        setState(() => _frame = value);
                      },
                onChangeStart: timelineFrames.isEmpty ? null : _beginScrub,
                onChangeEnd: timelineFrames.isEmpty ? null : _endScrub,
              ),
            ),
            Text(
              timelineFrames.isEmpty
                  ? '0 / 0'
                  : '${_frame.round() + 1} / ${timelineFrames.length}',
            ),
          ],
        ),
        Text(
          data.sourcePath,
          style: const TextStyle(
            color: mutedText,
            fontFamily: 'monospace',
            fontSize: 11,
          ),
        ),
      ],
    );
  }
}

class Trajectory3DView extends StatefulWidget {
  const Trajectory3DView({
    super.key,
    required this.data,
    required this.layout,
    required this.visibleTags,
    required this.currentTimeS,
    required this.compactLabel,
    required this.displayMode,
    required this.dotSize,
  });

  final TrajectoryData data;
  final AnchorLayoutData? layout;
  final Set<String> visibleTags;
  final double currentTimeS;
  final String compactLabel;
  final String displayMode;
  final double dotSize;

  @override
  State<Trajectory3DView> createState() => _Trajectory3DViewState();
}

class _Trajectory3DViewState extends State<Trajectory3DView> {
  double yaw = -0.75;
  double pitch = -0.55;
  double zoom = 1.0;
  AnchorPoint? _hoveredAnchor;
  TrajectoryFrame? _hoveredFrame;
  RotoTrajectoryEstimate? _hoveredCenter;
  Offset? _hoverPosition;

  void _handleWheelZoom(PointerSignalEvent event) {
    if (event is! PointerScrollEvent ||
        !HardwareKeyboard.instance.isControlPressed) {
      return;
    }
    GestureBinding.instance.pointerSignalResolver.register(event, (
      PointerSignalEvent resolved,
    ) {
      final scroll = resolved as PointerScrollEvent;
      final factor = math.exp(-scroll.scrollDelta.dy * 0.0015);
      setState(() {
        zoom = (zoom * factor).clamp(0.55, 5.0);
      });
    });
  }

  @override
  Widget build(BuildContext context) {
    return Listener(
      onPointerSignal: _handleWheelZoom,
      child: GestureDetector(
        onScaleUpdate: (details) {
          setState(() {
            yaw += details.focalPointDelta.dx * 0.01;
            pitch = (pitch - details.focalPointDelta.dy * 0.01).clamp(
              -1.35,
              1.35,
            );
            zoom = (zoom * details.scale).clamp(0.55, 5.0);
          });
        },
        child: Container(
          decoration: BoxDecoration(
            border: Border.all(color: panelLine),
            borderRadius: BorderRadius.circular(8),
          ),
          child: LayoutBuilder(
            builder: (context, constraints) {
              final size = Size(constraints.maxWidth, constraints.maxHeight);
              return MouseRegion(
                onHover: (event) {
                  final frame = _hitTestTag(size, event.localPosition);
                  final center = frame == null
                      ? _hitTestCenter(size, event.localPosition)
                      : null;
                  final anchor = frame == null && center == null
                      ? _hitTestAnchor(size, event.localPosition)
                      : null;
                  final position =
                      frame == null && center == null && anchor == null
                      ? null
                      : event.localPosition;
                  if (frame != _hoveredFrame ||
                      center != _hoveredCenter ||
                      anchor != _hoveredAnchor ||
                      position != _hoverPosition) {
                    setState(() {
                      _hoveredFrame = frame;
                      _hoveredCenter = center;
                      _hoveredAnchor = anchor;
                      _hoverPosition = position;
                    });
                  }
                },
                onExit: (_) {
                  if (_hoveredAnchor != null ||
                      _hoveredFrame != null ||
                      _hoveredCenter != null) {
                    setState(() {
                      _hoveredFrame = null;
                      _hoveredCenter = null;
                      _hoveredAnchor = null;
                      _hoverPosition = null;
                    });
                  }
                },
                child: Stack(
                  children: [
                    Positioned.fill(
                      child: CustomPaint(
                        painter: TrajectoryPainter(
                          data: widget.data,
                          layout: widget.layout,
                          visibleTags: widget.visibleTags,
                          currentTimeS: widget.currentTimeS,
                          compactLabel: widget.compactLabel,
                          displayMode: widget.displayMode,
                          dotSize: widget.dotSize,
                          yaw: yaw,
                          pitch: pitch,
                          zoom: zoom,
                          hoveredAnchorLabel: _hoveredAnchor?.label,
                          hoveredTag: _hoveredFrame?.tag,
                          hoveredCenterTag: _hoveredCenter?.tag,
                        ),
                        child: const SizedBox.expand(),
                      ),
                    ),
                    if (_hoveredFrame != null && _hoverPosition != null)
                      _TagHoverPopup(
                        frame: _hoveredFrame!,
                        position: _hoverPosition!,
                        size: size,
                      ),
                    if (_hoveredCenter != null && _hoverPosition != null)
                      _CenterHoverPopup(
                        estimate: _hoveredCenter!,
                        position: _hoverPosition!,
                        size: size,
                      ),
                    if (_hoveredAnchor != null && _hoverPosition != null)
                      _AnchorHoverPopup(
                        anchor: _hoveredAnchor!,
                        position: _hoverPosition!,
                        size: size,
                      ),
                  ],
                ),
              );
            },
          ),
        ),
      ),
    );
  }

  TrajectoryFrame? _hitTestTag(Size size, Offset pointer) {
    final selectedTags = widget.visibleTags.isEmpty
        ? widget.data.tags.toSet()
        : widget.visibleTags;
    final projection = _trajectoryProjection(size, selectedTags);
    if (projection == null) return null;
    TrajectoryFrame? best;
    var bestDistance = double.infinity;
    for (final tag in widget.data.tags.where(selectedTags.contains)) {
      final frame = _nearestFrameForTag(tag);
      if (frame == null) continue;
      final screen = _project3D(
        x: frame.xMm,
        y: frame.yMm,
        z: frame.zMm,
        centerX: projection.centerX,
        centerY: projection.centerY,
        centerZ: projection.centerZ,
        screenCenter: projection.screenCenter,
        scale: projection.scale,
        yaw: yaw,
        pitch: pitch,
      );
      final radius = math.max(12.0, widget.dotSize + 7);
      final distance = (pointer - screen).distance;
      final labelWidth = math.max(52.0, frame.tag.length * 8.0);
      final labelRect = Rect.fromLTWH(
        screen.dx + 4,
        screen.dy - 28,
        labelWidth,
        34,
      );
      if ((distance <= radius || labelRect.contains(pointer)) &&
          distance < bestDistance) {
        best = frame;
        bestDistance = distance;
      }
    }
    return best;
  }

  RotoTrajectoryEstimate? _hitTestCenter(Size size, Offset pointer) {
    final selectedTags = widget.visibleTags.isEmpty
        ? widget.data.tags.toSet()
        : widget.visibleTags;
    final projection = _trajectoryProjection(size, selectedTags);
    if (projection == null) return null;
    RotoTrajectoryEstimate? best;
    var bestDistance = double.infinity;
    for (final estimate in widget.data.estimatesForTags(selectedTags)) {
      final screen = _project3D(
        x: estimate.centerX,
        y: estimate.centerY,
        z: estimate.centerZ,
        centerX: projection.centerX,
        centerY: projection.centerY,
        centerZ: projection.centerZ,
        screenCenter: projection.screenCenter,
        scale: projection.scale,
        yaw: yaw,
        pitch: pitch,
      );
      final distance = (pointer - screen).distance;
      final labelWidth = math.max(80.0, estimate.tag.length * 8.0 + 18);
      final labelRect = Rect.fromLTWH(
        screen.dx + 4,
        screen.dy - 28,
        labelWidth,
        34,
      );
      final radius = math.max(16.0, widget.dotSize + 10);
      if ((distance <= radius || labelRect.contains(pointer)) &&
          distance < bestDistance) {
        best = estimate;
        bestDistance = distance;
      }
    }
    return best;
  }

  AnchorPoint? _hitTestAnchor(Size size, Offset pointer) {
    final layout = widget.layout;
    if (layout == null) return null;
    final selectedTags = widget.visibleTags.isEmpty
        ? widget.data.tags.toSet()
        : widget.visibleTags;
    final projection = _trajectoryProjection(size, selectedTags);
    if (projection == null) return null;
    AnchorPoint? best;
    var bestDistance = double.infinity;
    for (final anchor in layout.points) {
      final screen = _project3D(
        x: anchor.x,
        y: anchor.y,
        z: anchor.z,
        centerX: projection.centerX,
        centerY: projection.centerY,
        centerZ: projection.centerZ,
        screenCenter: projection.screenCenter,
        scale: projection.scale,
        yaw: yaw,
        pitch: pitch,
      );
      final distance = (pointer - screen).distance;
      final labelRect = Rect.fromLTWH(screen.dx + 4, screen.dy - 26, 34, 30);
      if ((distance <= 14 || labelRect.contains(pointer)) &&
          distance < bestDistance) {
        best = anchor;
        bestDistance = distance;
      }
    }
    return best;
  }

  TrajectoryFrame? _nearestFrameForTag(String tag) {
    final tagPoints = widget.data.framesFor(tag);
    if (tagPoints.isEmpty) return null;
    TrajectoryFrame best = tagPoints.first;
    var bestDt = (best.hostElapsedS - widget.currentTimeS).abs();
    for (final frame in tagPoints.skip(1)) {
      final dt = (frame.hostElapsedS - widget.currentTimeS).abs();
      if (dt < bestDt) {
        best = frame;
        bestDt = dt;
      }
    }
    return best;
  }

  _TrajectoryProjection? _trajectoryProjection(
    Size size,
    Set<String> selectedTags,
  ) {
    if (size.width <= 0 || size.height <= 0) return null;
    final points = widget.data.framesForTags(selectedTags);
    if (points.isEmpty) return null;
    final layout = widget.layout;
    final allX = [
      ...points.map((p) => p.xMm),
      if (layout != null) ...layout.points.map((p) => p.x),
    ];
    final allY = [
      ...points.map((p) => p.yMm),
      if (layout != null) ...layout.points.map((p) => p.y),
    ];
    final allZ = [
      ...points.map((p) => p.zMm),
      if (layout != null) ...layout.points.map((p) => p.z),
    ];
    final pad = 180.0;
    final minX = allX.reduce(math.min) - pad;
    final maxX = allX.reduce(math.max) + pad;
    final minY = allY.reduce(math.min) - pad;
    final maxY = allY.reduce(math.max) + pad;
    final minZ = allZ.reduce(math.min) - pad;
    final maxZ = allZ.reduce(math.max) + pad;
    final spanX = math.max(1.0, maxX - minX);
    final spanY = math.max(1.0, maxY - minY);
    final spanZ = math.max(1.0, maxZ - minZ);
    return _TrajectoryProjection(
      centerX: (minX + maxX) / 2,
      centerY: (minY + maxY) / 2,
      centerZ: (minZ + maxZ) / 2,
      screenCenter: Offset(size.width / 2, size.height / 2 + 14),
      scale:
          math.min(size.width, size.height) *
          0.68 /
          math.max(spanX, math.max(spanY, spanZ)) *
          zoom,
    );
  }
}

class _TrajectoryProjection {
  const _TrajectoryProjection({
    required this.centerX,
    required this.centerY,
    required this.centerZ,
    required this.screenCenter,
    required this.scale,
  });

  final double centerX;
  final double centerY;
  final double centerZ;
  final Offset screenCenter;
  final double scale;
}

class TrajectoryPainter extends CustomPainter {
  const TrajectoryPainter({
    required this.data,
    required this.layout,
    required this.visibleTags,
    required this.currentTimeS,
    required this.compactLabel,
    required this.displayMode,
    required this.dotSize,
    required this.yaw,
    required this.pitch,
    required this.zoom,
    this.hoveredAnchorLabel,
    this.hoveredTag,
    this.hoveredCenterTag,
  });

  final TrajectoryData data;
  final AnchorLayoutData? layout;
  final Set<String> visibleTags;
  final double currentTimeS;
  final String compactLabel;
  final String displayMode;
  final double dotSize;
  final double yaw;
  final double pitch;
  final double zoom;
  final String? hoveredAnchorLabel;
  final String? hoveredTag;
  final String? hoveredCenterTag;

  @override
  void paint(Canvas canvas, Size size) {
    final selectedTags = visibleTags.isEmpty ? data.tags.toSet() : visibleTags;
    final points = data.framesForTags(selectedTags);
    if (points.isEmpty) return;
    final allX = [
      ...points.map((p) => p.xMm),
      if (layout != null) ...layout!.points.map((p) => p.x),
    ];
    final allY = [
      ...points.map((p) => p.yMm),
      if (layout != null) ...layout!.points.map((p) => p.y),
    ];
    final allZ = [
      ...points.map((p) => p.zMm),
      if (layout != null) ...layout!.points.map((p) => p.z),
    ];
    final pad = 180.0;
    final minX = allX.reduce(math.min) - pad;
    final maxX = allX.reduce(math.max) + pad;
    final minY = allY.reduce(math.min) - pad;
    final maxY = allY.reduce(math.max) + pad;
    final minZ = allZ.reduce(math.min) - pad;
    final maxZ = allZ.reduce(math.max) + pad;
    final spanX = math.max(1.0, maxX - minX);
    final spanY = math.max(1.0, maxY - minY);
    final spanZ = math.max(1.0, maxZ - minZ);
    final center = Offset(size.width / 2, size.height / 2 + 14);
    final scale =
        math.min(size.width, size.height) *
        0.68 /
        math.max(spanX, math.max(spanY, spanZ)) *
        zoom;
    final cx = (minX + maxX) / 2;
    final cy0 = (minY + maxY) / 2;
    final cz = (minZ + maxZ) / 2;
    final textPainter = TextPainter(textDirection: TextDirection.ltr);

    Offset project(double x, double y, double z) => _project3D(
      x: x,
      y: y,
      z: z,
      centerX: cx,
      centerY: cy0,
      centerZ: cz,
      screenCenter: center,
      scale: scale,
      yaw: yaw,
      pitch: pitch,
    );

    final gridPaint = Paint()
      ..color = tableLine.withValues(alpha: 0.32)
      ..strokeWidth = 0.7;
    for (var i = 0; i <= 4; i++) {
      final t = i / 4;
      final x = minX + spanX * t;
      final y = minY + spanY * t;
      canvas.drawLine(
        project(x, minY, minZ),
        project(x, maxY, minZ),
        gridPaint,
      );
      canvas.drawLine(
        project(minX, y, minZ),
        project(maxX, y, minZ),
        gridPaint,
      );
    }
    void label(String text, Offset at, Color color) {
      textPainter.text = TextSpan(
        text: text,
        style: TextStyle(
          color: color,
          fontSize: 12,
          fontWeight: FontWeight.w700,
        ),
      );
      textPainter.layout();
      textPainter.paint(canvas, at);
    }

    final axisPaint = Paint()
      ..color = controlGreen
      ..strokeWidth = 1.4;
    final origin = project(minX, minY, minZ);
    canvas.drawLine(origin, project(maxX, minY, minZ), axisPaint);
    canvas.drawLine(origin, project(minX, maxY, minZ), axisPaint);
    canvas.drawLine(origin, project(minX, minY, maxZ), axisPaint);
    label(
      'X ${fmtMm(minX)}..${fmtMm(maxX)}',
      project(maxX, minY, minZ),
      controlGreen,
    );
    label(
      'Y ${fmtMm(minY)}..${fmtMm(maxY)}',
      project(minX, maxY, minZ),
      controlGreen,
    );
    label(
      'Z ${fmtMm(minZ)}..${fmtMm(maxZ)}',
      project(minX, minY, maxZ),
      controlGreen,
    );

    void anchorLine(String a, String b, Color color) {
      final byLabel = layout?.byLabel;
      if (byLabel == null) return;
      final pa = byLabel[a];
      final pb = byLabel[b];
      if (pa == null || pb == null) return;
      canvas.drawLine(
        project(pa.x, pa.y, pa.z),
        project(pb.x, pb.y, pb.z),
        Paint()
          ..color = color
          ..strokeWidth = 1.15,
      );
    }

    if (layout != null) {
      for (final edge in const [
        ['A', 'B'],
        ['B', 'C'],
        ['C', 'D'],
        ['D', 'A'],
        ['E', 'F'],
        ['F', 'G'],
        ['G', 'H'],
        ['H', 'E'],
        ['A', 'E'],
        ['B', 'F'],
        ['C', 'G'],
        ['D', 'H'],
      ]) {
        final lower =
            anchors.indexOf(edge.first) < 4 && anchors.indexOf(edge.last) < 4;
        final upper =
            anchors.indexOf(edge.first) >= 4 && anchors.indexOf(edge.last) >= 4;
        anchorLine(
          edge.first,
          edge.last,
          upper
              ? biospurGlow.withValues(alpha: 0.75)
              : lower
              ? controlGreen.withValues(alpha: 0.75)
              : const Color(0x667A8F65),
        );
      }
      for (final anchor in layout!.points) {
        final p = project(anchor.x, anchor.y, anchor.z);
        final upper = anchors.indexOf(anchor.label) >= 4;
        final color = upper ? biospurGlow : controlGreen;
        if (anchor.label == hoveredAnchorLabel) {
          canvas.drawCircle(
            p,
            12,
            Paint()
              ..style = PaintingStyle.stroke
              ..strokeWidth = 2.2
              ..color = biospurGlow,
          );
        }
        canvas.drawCircle(p, 5.5, Paint()..color = color);
        canvas.drawCircle(
          p,
          7.5,
          Paint()
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1
            ..color = Colors.white.withValues(alpha: 0.8),
        );
        label(anchor.label, p + const Offset(8, -16), Colors.white);
      }
    }

    final palette = <Color>[
      controlGreen,
      biospurGlow,
      const Color(0xFFD7EEC1),
      const Color(0xFF88B85A),
    ];
    List<TrajectoryFrame> visibleTrailFor(String tag) {
      final tagPoints = data.framesFor(tag);
      if (displayMode == 'dots') return const [];
      if (displayMode == 'recent') {
        const seconds = 6.0;
        return tagPoints
            .where(
              (p) =>
                  p.hostElapsedS <= currentTimeS &&
                  p.hostElapsedS >= currentTimeS - seconds,
            )
            .toList();
      }
      return tagPoints;
    }

    TrajectoryFrame? nearestFor(String tag) {
      final tagPoints = data.framesFor(tag);
      if (tagPoints.isEmpty) return null;
      TrajectoryFrame best = tagPoints.first;
      var bestDt = (best.hostElapsedS - currentTimeS).abs();
      for (final p in tagPoints.skip(1)) {
        final dt = (p.hostElapsedS - currentTimeS).abs();
        if (dt < bestDt) {
          best = p;
          bestDt = dt;
        }
      }
      return best;
    }

    final estimates = data.estimatesForTags(selectedTags);
    for (final estimate in estimates) {
      final centerPt = project(
        estimate.centerX,
        estimate.centerY,
        estimate.centerZ,
      );
      final color =
          palette[data.tags.indexOf(estimate.tag).clamp(0, palette.length - 1)];
      final centerPaint = Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = math.max(1.1, dotSize * 0.22)
        ..color = color;
      final centerRadius = math.max(2.0, dotSize * 0.85);
      final cross = math.max(2.0, dotSize * 0.60);
      if (estimate.tag == hoveredCenterTag) {
        canvas.drawCircle(
          centerPt,
          centerRadius + 6,
          Paint()
            ..style = PaintingStyle.stroke
            ..strokeWidth = math.max(1.4, dotSize * 0.18)
            ..color = biospurGlow,
        );
      }
      canvas.drawCircle(centerPt, centerRadius, centerPaint);
      canvas.drawLine(
        centerPt + Offset(-cross, 0),
        centerPt + Offset(cross, 0),
        centerPaint,
      );
      canvas.drawLine(
        centerPt + Offset(0, -cross),
        centerPt + Offset(0, cross),
        centerPaint,
      );
      label('C ${estimate.tag}', centerPt + const Offset(9, -9), mutedText);
    }

    for (final tag in data.tags.where(selectedTags.contains)) {
      final tagPoints = visibleTrailFor(tag);
      final color =
          palette[data.tags.indexOf(tag).clamp(0, palette.length - 1)];
      if (tagPoints.isNotEmpty) {
        final pathPaint = Paint()
          ..color = color.withValues(
            alpha: displayMode == 'recent' ? 0.55 : 0.72,
          )
          ..strokeWidth = 1.2
          ..style = PaintingStyle.stroke;
        final path = Path();
        for (var i = 0; i < tagPoints.length; i++) {
          final p = project(
            tagPoints[i].xMm,
            tagPoints[i].yMm,
            tagPoints[i].zMm,
          );
          if (i == 0) {
            path.moveTo(p.dx, p.dy);
          } else {
            path.lineTo(p.dx, p.dy);
          }
        }
        canvas.drawPath(path, pathPaint);
        for (
          var i = 0;
          i < tagPoints.length;
          i += math.max(1, tagPoints.length ~/ 350)
        ) {
          final p = project(
            tagPoints[i].xMm,
            tagPoints[i].yMm,
            tagPoints[i].zMm,
          );
          canvas.drawCircle(
            p,
            1.4,
            Paint()..color = color.withValues(alpha: 0.42),
          );
        }
      }
      final dot = nearestFor(tag);
      if (dot == null) continue;
      final p = project(dot.xMm, dot.yMm, dot.zMm);
      if (dot.tag == hoveredTag) {
        canvas.drawCircle(
          p,
          dotSize + 8,
          Paint()
            ..style = PaintingStyle.stroke
            ..strokeWidth = math.max(1.4, dotSize * 0.20)
            ..color = biospurGlow,
        );
      }
      canvas.drawCircle(p, dotSize, Paint()..color = color);
      canvas.drawCircle(
        p,
        dotSize + 4,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = math.max(1.0, dotSize * 0.16)
          ..color = Colors.white.withValues(alpha: 0.88),
      );
      label(tag, p + const Offset(10, -18), Colors.white);
    }
    label(
      '$compactLabel  ${displayMode == 'dots'
          ? 'dots only'
          : displayMode == 'recent'
          ? 'recent 6s'
          : 'full trail'}  t ${currentTimeS.toStringAsFixed(2)} s',
      const Offset(12, 12),
      mutedText,
    );
  }

  @override
  bool shouldRepaint(covariant TrajectoryPainter oldDelegate) {
    return oldDelegate.data != data ||
        oldDelegate.layout != layout ||
        oldDelegate.visibleTags.join(',') != visibleTags.join(',') ||
        oldDelegate.currentTimeS != currentTimeS ||
        oldDelegate.compactLabel != compactLabel ||
        oldDelegate.displayMode != displayMode ||
        oldDelegate.dotSize != dotSize ||
        oldDelegate.yaw != yaw ||
        oldDelegate.pitch != pitch ||
        oldDelegate.zoom != zoom ||
        oldDelegate.hoveredAnchorLabel != hoveredAnchorLabel ||
        oldDelegate.hoveredTag != hoveredTag ||
        oldDelegate.hoveredCenterTag != hoveredCenterTag;
  }
}

class RunnerLogCard extends StatefulWidget {
  const RunnerLogCard({super.key, required this.runner});

  final ScriptRunner runner;

  @override
  State<RunnerLogCard> createState() => _RunnerLogCardState();
}

class _RunnerLogCardState extends State<RunnerLogCard> {
  double _logHeight = 44;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.runner,
      builder: (context, _) {
        final runner = widget.runner;
        return Card(
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onVerticalDragUpdate: (details) {
                    setState(() {
                      _logHeight = (_logHeight - details.delta.dy).clamp(
                        44.0,
                        320.0,
                      );
                    });
                  },
                  onDoubleTap: () {
                    setState(() {
                      _logHeight = _logHeight > 44 ? 44 : 180;
                    });
                  },
                  child: MouseRegion(
                    cursor: SystemMouseCursors.resizeUpDown,
                    child: SizedBox(
                      height: 10,
                      child: Center(
                        child: Container(
                          width: 60,
                          height: 3,
                          decoration: BoxDecoration(
                            color: controlGreen.withValues(alpha: 0.55),
                            borderRadius: BorderRadius.circular(999),
                          ),
                        ),
                      ),
                    ),
                  ),
                ),
                Row(
                  children: [
                    const Text(
                      'Command Output',
                      style: TextStyle(
                        fontSize: 15,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const Spacer(),
                    if (runner.activeName != null) Text(runner.activeName!),
                  ],
                ),
                const SizedBox(height: 4),
                if (runner.isRunning || runner.progressText != null) ...[
                  Row(
                    children: [
                      Expanded(
                        child: LinearProgressIndicator(
                          value: runner.progressBarValue,
                          minHeight: 8,
                          backgroundColor: const Color(0x55384515),
                          valueColor: AlwaysStoppedAnimation<Color>(
                            runner.progressTone == PillTone.bad
                                ? toneColor(PillTone.bad)
                                : controlGreen,
                          ),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Text(
                        runner.progressLabel,
                        style: TextStyle(
                          color: runner.progressTone == PillTone.bad
                              ? toneColor(PillTone.bad)
                              : controlGreen,
                          fontWeight: FontWeight.w700,
                          fontSize: 12,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 4),
                ],
                Container(
                  width: double.infinity,
                  height: _logHeight,
                  padding: const EdgeInsets.all(7),
                  decoration: BoxDecoration(
                    border: Border.all(color: panelLine),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: SingleChildScrollView(
                    reverse: true,
                    child: Text(
                      runner.logTail.isEmpty
                          ? 'No command output yet.'
                          : runner.logTail.join('\n'),
                      style: const TextStyle(
                        color: Color(0xFFD7EEC1),
                        fontFamily: 'monospace',
                        fontSize: 11,
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class ScriptRunner extends ChangeNotifier {
  Process? _process;
  final List<String> logTail = [];
  String? activeName;
  int? lastExitCode;
  double? progressValue;
  String? progressText;

  bool get isRunning => _process != null;

  Future<void> start(String name, String command) async {
    if (_process != null) return;
    activeName = name;
    lastExitCode = null;
    progressValue = null;
    progressText = 'starting';
    logTail.clear();
    _append('\$ $command');
    notifyListeners();
    final process = await Process.start(
      'setsid',
      ['/bin/bash', '-lc', command],
      workingDirectory: repoRoot,
      runInShell: false,
    );
    _process = process;
    notifyListeners();
    process.stdout
        .transform(utf8.decoder)
        .transform(const LineSplitter())
        .listen(_append);
    process.stderr
        .transform(utf8.decoder)
        .transform(const LineSplitter())
        .listen((line) {
          _append('[stderr] $line');
        });
    process.exitCode.then((code) {
      lastExitCode = code;
      _append('[exit] $code');
      _process = null;
      progressValue = 1.0;
      progressText = code == 0 ? 'complete' : 'failed: exit $code';
      notifyListeners();
    });
  }

  Future<void> stop() async {
    final process = _process;
    if (process == null) return;
    final pgid = process.pid;
    await _signalProcessGroup(pgid, 'TERM');
    progressValue = null;
    progressText = 'stopping';
    _append('[ui] sent SIGTERM to process group $pgid');
    notifyListeners();
    Future<void>.delayed(const Duration(seconds: 1), () async {
      if (_process?.pid != pgid) return;
      await _signalProcessGroup(pgid, 'KILL');
      _append('[ui] sent SIGKILL to process group $pgid');
      notifyListeners();
    });
  }

  Future<void> _signalProcessGroup(int pgid, String signal) async {
    final result = await Process.run('kill', ['-$signal', '-$pgid']);
    if (result.exitCode != 0) {
      _append(
        '[ui] process-group signal failed; fallback direct $signal to pid $pgid',
      );
      _process?.kill(switch (signal) {
        'KILL' => ProcessSignal.sigkill,
        'TERM' => ProcessSignal.sigterm,
        _ => ProcessSignal.sigint,
      });
    }
  }

  void _append(String line) {
    logTail.add(line);
    if (logTail.length > 240) {
      logTail.removeRange(0, logTail.length - 240);
    }
    _updateProgressFromLine(line);
    notifyListeners();
  }

  String get progressLabel {
    final text = progressText ?? (isRunning ? 'running' : 'idle');
    final value = progressValue;
    if (value == null) return text;
    return '${(value * 100).clamp(0, 100).toStringAsFixed(0)}%  $text';
  }

  double? get progressBarValue {
    if (isRunning) return progressValue;
    if (progressText == null) return null;
    return 1.0;
  }

  PillTone get progressTone {
    if (lastExitCode == null) return PillTone.active;
    return lastExitCode == 0 ? PillTone.good : PillTone.bad;
  }

  void _updateProgressFromLine(String line) {
    final ratio = RegExp(
      r'\]\s+\[[#.\s]+\]\s+(\d+)/(\d+)\s+(.+)$',
    ).firstMatch(line);
    if (ratio != null) {
      final done = int.tryParse(ratio.group(1) ?? '');
      final total = int.tryParse(ratio.group(2) ?? '');
      if (done != null && total != null && total > 0) {
        progressValue = done / total;
        progressText = '${ratio.group(1)}/${ratio.group(2)} ${ratio.group(3)}';
        return;
      }
    }

    final percent = RegExp(
      r'\]\s+\[[#.\s]+\]\s+([0-9.]+)%\s+(.+)$',
    ).firstMatch(line);
    if (percent != null) {
      final value = double.tryParse(percent.group(1) ?? '');
      if (value != null) {
        progressValue = (value / 100).clamp(0.0, 1.0);
        progressText = percent.group(2);
        return;
      }
    }

    if (line.startsWith('[solve]')) {
      progressValue = null;
      progressText = 'solver running';
    } else if (line.startsWith('[run]')) {
      progressValue = null;
      progressText = 'launching solver';
    }
  }
}

class PortReader {
  static Future<PortSnapshot> read() async {
    final dir = Directory('/dev/serial/by-id');
    if (!dir.existsSync()) return PortSnapshot.empty();
    final names = dir.listSync().map((e) => e.path.split('/').last).join('\n');
    return PortSnapshot(
      masterAnchor: names.contains(
        'BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02',
      ),
      masterTag: names.contains(
        'Master_Tag_BioSpur_BLE_Control_6918E0384172A49F',
      ),
    );
  }
}

class PortSnapshot {
  const PortSnapshot({required this.masterAnchor, required this.masterTag});

  final bool masterAnchor;
  final bool masterTag;

  bool get anyMaster => masterAnchor || masterTag;

  factory PortSnapshot.empty() =>
      const PortSnapshot(masterAnchor: false, masterTag: false);
}

class SweepReader {
  static final Map<String, _RoundLogSnapshot> _roundLogCache = {};

  static Future<SweepSnapshot> readLatest({DateTime? minModified}) async {
    final root = Directory(capturesRoot);
    if (!root.existsSync()) return SweepSnapshot.empty();
    final candidates =
        root
            .listSync()
            .whereType<Directory>()
            .where((d) => d.path.split('/').last.startsWith('sweep_'))
            .where((d) {
              if (minModified == null) return true;
              return !d.statSync().modified.isBefore(minModified);
            })
            .toList()
          ..sort(
            (a, b) => b.statSync().modified.compareTo(a.statSync().modified),
          );
    if (candidates.isEmpty) return SweepSnapshot.empty();
    return _readSweepDir(candidates.first);
  }

  static Future<SweepSnapshot> _readSweepDir(Directory sweepDir) async {
    final rounds = {for (final a in anchors) a: RoundState.empty(a)};
    LiveSwRow? latestRow;
    DateTime? latestRowModified;
    var finalResponderOk = false;
    var targetSets = _inferTargetSets(sweepDir);
    final sweepDataDir = _findSweepDataDir(sweepDir, targetSets);

    for (final anchor in anchors) {
      final log = File('${sweepDataDir.path}/round_$anchor/master.log');
      if (!log.existsSync()) continue;
      final parsedLog = await _readRoundLog(log, anchor);
      final count = parsedLog.count;
      final minQ = parsedLog.minQuality;
      final lastForRound = parsedLog.lastRow;
      final state = count >= targetSets
          ? RoundStage.pass
          : count > 0
          ? RoundStage.running
          : RoundStage.waiting;
      rounds[anchor] = RoundState(
        anchor: anchor,
        state: state,
        swCount: count,
        minQuality: minQ,
      );
      if (lastForRound != null &&
          (latestRowModified == null ||
              parsedLog.modified.isAfter(latestRowModified))) {
        latestRow = lastForRound;
        latestRowModified = parsedLog.modified;
      }
    }

    final summary = File('${sweepDataDir.path}/summary.json');
    if (summary.existsSync()) {
      try {
        final decoded =
            jsonDecode(await summary.readAsString()) as Map<String, dynamic>;
        final summaryRounds = decoded['rounds'];
        if (summaryRounds is Map<String, dynamic>) {
          for (final anchor in anchors) {
            final item = summaryRounds[anchor];
            if (item is! Map<String, dynamic>) continue;
            final swCount =
                (item['sw_count'] as num?)?.toInt() ?? rounds[anchor]!.swCount;
            targetSets = math.max(targetSets, swCount);
            rounds[anchor] = rounds[anchor]!.copyWith(
              swCount: swCount,
              state: item['success'] == true
                  ? RoundStage.pass
                  : swCount > 0
                  ? RoundStage.running
                  : RoundStage.fail,
              minQuality:
                  (item['min_quality_seen'] as num?)?.toInt() ??
                  rounds[anchor]!.minQuality,
            );
            final swLine = item['sw_line']?.toString();
            if (latestRow == null && swLine != null && swLine.contains('SW-')) {
              final parsed = LiveSwRow.tryParse(swLine);
              if (parsed != null) {
                latestRow = parsed.copyWith(setIndex: swCount);
              }
            }
          }
        }
        final finalResponder = decoded['session_final_responder_result'];
        final finalResponderEnabled = decoded['final_responder'] == true;
        finalResponderOk =
            (finalResponder is Map<String, dynamic> &&
                finalResponder['success'] == true) ||
            finalResponder == true ||
            finalResponderEnabled;
      } catch (_) {
        // Keep live estimate if summary is being written.
      }
    }

    return SweepSnapshot(
      directory: sweepDir.path,
      rounds: rounds,
      currentRow: latestRow,
      targetSets: targetSets,
      finalResponderOk: finalResponderOk,
    );
  }

  static int _inferTargetSets(Directory sweepDir) {
    final name = sweepDir.path.split('/').last;
    final match = RegExp(r'_([0-9]+)_prewarm[0-9]+_').firstMatch(name);
    return int.tryParse(match?.group(1) ?? '') ?? 1000;
  }

  static Directory _findSweepDataDir(Directory sweepDir, int targetSets) {
    final preferred = Directory('${sweepDir.path}/sweep$targetSets');
    if (preferred.existsSync()) return preferred;
    final legacy = Directory('${sweepDir.path}/sweep1000');
    if (legacy.existsSync()) return legacy;
    final candidates =
        sweepDir
            .listSync()
            .whereType<Directory>()
            .where((d) => d.path.split('/').last.startsWith('sweep'))
            .toList()
          ..sort(
            (a, b) => b.statSync().modified.compareTo(a.statSync().modified),
          );
    return candidates.isEmpty ? preferred : candidates.first;
  }

  static Future<_RoundLogSnapshot> _readRoundLog(
    File log,
    String anchor,
  ) async {
    final stat = await log.stat();
    final cached = _roundLogCache[log.path];
    if (cached != null &&
        cached.modified == stat.modified &&
        cached.size == stat.size) {
      return cached;
    }

    final lines = await log.readAsLines();
    var count = 0;
    int? minQ;
    LiveSwRow? lastForRound;
    for (final line in lines) {
      final parsed = LiveSwRow.tryParse(line);
      if (parsed == null || parsed.master != 'SW-$anchor') continue;
      count += 1;
      lastForRound = parsed.copyWith(setIndex: count);
      for (final sample in parsed.values.values) {
        minQ = minQ == null ? sample.quality : math.min(minQ, sample.quality);
      }
    }

    final snapshot = _RoundLogSnapshot(
      modified: stat.modified,
      size: stat.size,
      count: count,
      minQuality: minQ,
      lastRow: lastForRound,
    );
    _roundLogCache[log.path] = snapshot;
    return snapshot;
  }
}

class _RoundLogSnapshot {
  const _RoundLogSnapshot({
    required this.modified,
    required this.size,
    required this.count,
    required this.minQuality,
    required this.lastRow,
  });

  final DateTime modified;
  final int size;
  final int count;
  final int? minQuality;
  final LiveSwRow? lastRow;
}

class SweepSnapshot {
  const SweepSnapshot({
    required this.directory,
    required this.rounds,
    required this.currentRow,
    required this.targetSets,
    required this.finalResponderOk,
  });

  final String? directory;
  final Map<String, RoundState> rounds;
  final LiveSwRow? currentRow;
  final int targetSets;
  final bool finalResponderOk;

  factory SweepSnapshot.empty() => SweepSnapshot(
    directory: null,
    rounds: {for (final a in anchors) a: RoundState.empty(a)},
    currentRow: null,
    targetSets: 1000,
    finalResponderOk: false,
  );

  String get displayName => directory?.split('/').last ?? 'no sweep loaded';
  String? get currentMaster => currentRow?.master;
  String? get currentAnchor => currentMaster?.replaceFirst('SW-', '');
  int get totalSwCount => rounds.values.fold(0, (sum, r) => sum + r.swCount);
  int get targetTotal => targetSets * anchors.length;
  double get overallProgress =>
      targetTotal == 0 ? 0 : (totalSwCount / targetTotal).clamp(0.0, 1.0);
  bool get complete => anchors.every(
    (a) => rounds[a]?.state == RoundStage.pass && (rounds[a]?.swCount ?? 0) > 0,
  );
}

class RoundState {
  const RoundState({
    required this.anchor,
    required this.state,
    required this.swCount,
    required this.minQuality,
  });

  final String anchor;
  final RoundStage state;
  final int swCount;
  final int? minQuality;

  factory RoundState.empty(String anchor) => RoundState(
    anchor: anchor,
    state: RoundStage.waiting,
    swCount: 0,
    minQuality: null,
  );

  RoundState copyWith({RoundStage? state, int? swCount, int? minQuality}) {
    return RoundState(
      anchor: anchor,
      state: state ?? this.state,
      swCount: swCount ?? this.swCount,
      minQuality: minQuality ?? this.minQuality,
    );
  }

  double progress(int targetSets) =>
      targetSets <= 0 ? 0 : (swCount / targetSets).clamp(0.0, 1.0);
}

enum RoundStage {
  waiting('WAITING', PillTone.neutral),
  running('RUNNING', PillTone.active),
  pass('PASS', PillTone.good),
  fail('FAIL', PillTone.bad);

  const RoundStage(this.label, this.tone);
  final String label;
  final PillTone tone;
}

class LiveSwRow {
  const LiveSwRow({
    required this.master,
    required this.values,
    required this.setIndex,
  });

  final String master;
  final Map<String, DistanceSample> values;
  final int setIndex;

  LiveSwRow copyWith({int? setIndex}) => LiveSwRow(
    master: master,
    values: values,
    setIndex: setIndex ?? this.setIndex,
  );

  static LiveSwRow? tryParse(String line) {
    final idx = line.indexOf('SW-');
    if (idx < 0) return null;
    final payload = line.substring(idx).trim();
    final parts = payload
        .split(',')
        .map((p) => p.trim())
        .where((p) => p.isNotEmpty)
        .toList();
    if (parts.isEmpty) return null;
    final master = parts.first;
    if (!RegExp(r'^SW-[A-H]$').hasMatch(master)) return null;
    final values = <String, DistanceSample>{};
    var i = 1;
    while (i + 2 < parts.length) {
      final peer = parts[i];
      final distance = int.tryParse(
        double.tryParse(parts[i + 1])?.round().toString() ?? '',
      );
      final quality = int.tryParse(
        double.tryParse(parts[i + 2])?.round().toString() ?? '',
      );
      if (anchors.contains(peer) && distance != null && quality != null) {
        values[peer] = DistanceSample(distanceMm: distance, quality: quality);
      }
      i += 3;
    }
    return LiveSwRow(master: master, values: values, setIndex: 0);
  }
}

class DistanceSample {
  const DistanceSample({required this.distanceMm, required this.quality});

  final int distanceMm;
  final int quality;
}

class UltrasoundSummary {
  const UltrasoundSummary({
    required this.path,
    required this.timestamp,
    required this.state,
    required this.medianMm,
    required this.offsetMm,
    required this.antennaCenterMm,
    required this.ok,
    required this.anchors,
  });

  final String? path;
  final String? timestamp;
  final String? state;
  final double? medianMm;
  final double? offsetMm;
  final double? antennaCenterMm;
  final int? ok;
  final Map<String, UltrasoundAnchorSummary> anchors;

  int get doneCount => anchors.values.where((a) => a.state == 'DONE').length;

  int get expectedCount => 3;

  String get offsetsLabel {
    if (anchors.isEmpty) {
      return offsetMm == null ? '-' : '+${offsetMm!.toStringAsFixed(0)} mm';
    }
    final parts = <String>[];
    for (final anchor in const ['F', 'G', 'H']) {
      final offset = anchors[anchor]?.offsetMm;
      if (offset == null) continue;
      final sign = offset >= 0 ? '+' : '';
      parts.add('$anchor$sign${offset.toStringAsFixed(0)}');
    }
    return parts.isEmpty ? '-' : parts.join('/');
  }

  String get antennaCenterLabel {
    if (anchors.isEmpty) {
      return antennaCenterMm == null
          ? '-'
          : '${antennaCenterMm!.toStringAsFixed(0)} mm';
    }
    final parts = <String>[];
    for (final anchor in const ['F', 'G', 'H']) {
      final height = anchors[anchor]?.antennaCenterMm;
      if (height == null) continue;
      parts.add('$anchor${height.toStringAsFixed(0)}');
    }
    final prefix = parts.isEmpty ? 'F/G/H' : parts.join('/');
    return '$prefix $doneCount/3';
  }

  factory UltrasoundSummary.empty() => const UltrasoundSummary(
    path: null,
    timestamp: null,
    state: null,
    medianMm: null,
    offsetMm: null,
    antennaCenterMm: null,
    ok: null,
    anchors: {},
  );

  static Future<UltrasoundSummary> readLatest() async {
    final root = Directory(capturesRoot);
    if (!root.existsSync()) return UltrasoundSummary.empty();
    final anchors = <String, UltrasoundAnchorSummary>{};
    for (final anchor in const ['F', 'G', 'H']) {
      final files =
          root
              .listSync(recursive: true)
              .whereType<File>()
              .where((f) => f.path.endsWith('/ultrasound_$anchor.csv'))
              .toList()
            ..sort(
              (a, b) => b.statSync().modified.compareTo(a.statSync().modified),
            );
      if (files.isEmpty) continue;
      final summary = await UltrasoundAnchorSummary.read(anchor, files.first);
      if (summary.path != null) anchors[anchor] = summary;
    }
    if (anchors.isNotEmpty) {
      final done = anchors.values.where((a) => a.state == 'DONE').toList();
      final preferred =
          anchors['H'] ?? (done.isEmpty ? anchors.values.last : done.last);
      final antHeights = anchors.values
          .map((a) => a.antennaCenterMm)
          .whereType<double>()
          .toList();
      return UltrasoundSummary(
        path: anchors.values.map((a) => a.path).whereType<String>().join(' ; '),
        timestamp: preferred.timestamp,
        state: done.length == 3 ? 'DONE' : '${done.length}/3',
        medianMm: preferred.medianMm,
        offsetMm: preferred.offsetMm,
        antennaCenterMm: antHeights.isEmpty
            ? null
            : antHeights.reduce((a, b) => a + b) / antHeights.length,
        ok: done.isEmpty ? null : done.map((a) => a.ok ?? 0).reduce(math.min),
        anchors: anchors,
      );
    }

    // Backward compatibility with older single-H captures.
    final files =
        root
            .listSync(recursive: true)
            .whereType<File>()
            .where((f) => f.path.endsWith('/ultrasound_H.csv'))
            .toList()
          ..sort(
            (a, b) => b.statSync().modified.compareTo(a.statSync().modified),
          );
    if (files.isEmpty) return UltrasoundSummary.empty();
    return _read(files.first);
  }

  static Future<UltrasoundSummary> _read(File file) async {
    final one = await UltrasoundAnchorSummary.read('H', file);
    if (one.path == null) return UltrasoundSummary.empty();
    return UltrasoundSummary(
      path: one.path,
      timestamp: one.timestamp,
      state: one.state,
      medianMm: one.medianMm,
      offsetMm: one.offsetMm,
      antennaCenterMm: one.antennaCenterMm,
      ok: one.ok,
      anchors: {'H': one},
    );
  }
}

class UltrasoundAnchorSummary {
  const UltrasoundAnchorSummary({
    required this.anchor,
    required this.path,
    required this.timestamp,
    required this.state,
    required this.medianMm,
    required this.offsetMm,
    required this.antennaCenterMm,
    required this.ok,
  });

  final String anchor;
  final String? path;
  final String? timestamp;
  final String? state;
  final double? medianMm;
  final double? offsetMm;
  final double? antennaCenterMm;
  final int? ok;

  static Future<UltrasoundAnchorSummary> read(String anchor, File file) async {
    try {
      final lines = await file.readAsLines();
      if (lines.length < 2) {
        return UltrasoundAnchorSummary.empty(anchor);
      }
      final header = lines.first.split(',');
      Map<String, String>? best;
      for (final line in lines.skip(1)) {
        final values = line.split(',');
        final row = <String, String>{};
        for (var i = 0; i < header.length && i < values.length; i++) {
          row[header[i]] = values[i];
        }
        if (row['state'] == 'DONE') {
          best = row;
        } else if (best == null &&
            (row['median_ant_center_mm'] ?? '').isNotEmpty) {
          best = row;
        }
      }
      best ??= <String, String>{};
      return UltrasoundAnchorSummary(
        anchor: anchor,
        path: file.path,
        timestamp: emptyToNull(best['timestamp']),
        state: emptyToNull(best['state']),
        medianMm: double.tryParse(best['median_mm'] ?? ''),
        offsetMm: double.tryParse(best['ant_center_offset_mm'] ?? ''),
        antennaCenterMm: double.tryParse(best['median_ant_center_mm'] ?? ''),
        ok: int.tryParse(best['ok'] ?? ''),
      );
    } catch (_) {
      return UltrasoundAnchorSummary.empty(anchor);
    }
  }

  factory UltrasoundAnchorSummary.empty(String anchor) =>
      UltrasoundAnchorSummary(
        anchor: anchor,
        path: null,
        timestamp: null,
        state: null,
        medianMm: null,
        offsetMm: null,
        antennaCenterMm: null,
        ok: null,
      );
}

class LayoutSummary {
  const LayoutSummary({
    required this.path,
    required this.zConventionOk,
    required this.hZ,
    required this.usOffset,
    required this.usUsedAntCenter,
    required this.usUsedSource,
    required this.latestUsAntCenter,
    required this.latestUsPath,
    required this.usStatus,
    required this.usRmsResidual,
    required this.usMaxResidual,
    required this.usResiduals,
    required this.usUsedSources,
    required this.latestUsByAnchor,
  });

  final String? path;
  final bool zConventionOk;
  final double? hZ;
  final double? usOffset;
  final double? usUsedAntCenter;
  final String? usUsedSource;
  final double? latestUsAntCenter;
  final String? latestUsPath;
  final String? usStatus;
  final double? usRmsResidual;
  final double? usMaxResidual;
  final Map<String, double> usResiduals;
  final Map<String, String> usUsedSources;
  final Map<String, double> latestUsByAnchor;

  bool get usStale {
    for (final entry in latestUsByAnchor.entries) {
      final usedPath = usUsedSources[entry.key];
      final latestPath = latestUsPathFor(entry.key);
      if (usedPath != null && latestPath != null && usedPath != latestPath) {
        return true;
      }
    }
    return false;
  }

  String get latestUsLabel {
    if (latestUsByAnchor.isEmpty) return '-';
    return latestUsByAnchor.entries
        .map((e) => '${e.key}:${e.value.toStringAsFixed(0)}')
        .join(' ');
  }

  String get usResidualLabel {
    if (usResiduals.isEmpty) return '-';
    return ['F', 'G', 'H']
        .where((anchor) => usResiduals.containsKey(anchor))
        .map((anchor) {
          final value = usResiduals[anchor]!;
          final sign = value >= 0 ? '+' : '';
          return '$anchor:$sign${value.toStringAsFixed(1)}';
        })
        .join(' ');
  }

  String? latestUsPathFor(String anchor) => _latestUsPathByAnchor[anchor];

  static Map<String, String> _latestUsPathByAnchor = {};

  factory LayoutSummary.empty() => const LayoutSummary(
    path: null,
    zConventionOk: false,
    hZ: null,
    usOffset: null,
    usUsedAntCenter: null,
    usUsedSource: null,
    latestUsAntCenter: null,
    latestUsPath: null,
    usStatus: null,
    usRmsResidual: null,
    usMaxResidual: null,
    usResiduals: {},
    usUsedSources: {},
    latestUsByAnchor: {},
  );

  static Future<LayoutSummary> read() async {
    final latestUs = await UltrasoundSummary.readLatest();
    final candidates = [
      File(
        '$activeSolverOutputs/v1_to_v4_io_field_check/v4-io/layout_us_height.json',
      ),
      File('$activeSolverOutputs/v4io_field_check/v4-io/layout_us_height.json'),
      File('$activeSolverOutputs/v1_to_v4_io_field_check/v4-io/layout.json'),
      File('$activeSolverOutputs/v4io_field_check/v4-io/layout.json'),
    ];
    File? path;
    for (final candidate in candidates) {
      if (candidate.existsSync()) {
        path = candidate;
        break;
      }
    }
    if (path == null) return LayoutSummary.empty();
    try {
      final decoded =
          jsonDecode(await path.readAsString()) as Map<String, dynamic>;
      final list = decoded['anchors'] as List<dynamic>;
      final z = <String, double>{};
      for (final item in list) {
        if (item is Map<String, dynamic>) {
          z[item['label'].toString()] = (item['z_mm'] as num).toDouble();
        }
      }
      final lower =
          anchors.take(4).map((a) => z[a] ?? 0).reduce((a, b) => a + b) / 4;
      final upper =
          anchors.skip(4).map((a) => z[a] ?? 0).reduce((a, b) => a + b) / 4;
      final meta = decoded['extra']?['ultrasound_height_alignment'];
      final metaMap = meta is Map<String, dynamic> ? meta : null;
      final usAll = metaMap?['ultrasound'] is Map<String, dynamic>
          ? metaMap!['ultrasound'] as Map<String, dynamic>
          : null;
      final usedSources = <String, String>{};
      double? usedMean;
      if (usAll != null) {
        final heights = <double>[];
        for (final entry in usAll.entries) {
          if (entry.value is! Map<String, dynamic>) continue;
          final anchor = entry.key.toUpperCase();
          final item = entry.value as Map<String, dynamic>;
          final source = item['source_csv']?.toString();
          if (source != null) usedSources[anchor] = source;
          final h = (item['height_ant_center_mm'] as num?)?.toDouble();
          if (h != null) heights.add(h);
        }
        if (heights.isNotEmpty) {
          usedMean = heights.reduce((a, b) => a + b) / heights.length;
        }
      }
      final latestByAnchor = <String, double>{};
      final latestPathByAnchor = <String, String>{};
      for (final entry in latestUs.anchors.entries) {
        final h = entry.value.antennaCenterMm;
        final p = entry.value.path;
        if (h != null) latestByAnchor[entry.key] = h;
        if (p != null) latestPathByAnchor[entry.key] = p;
      }
      _latestUsPathByAnchor = latestPathByAnchor;
      final residuals = <String, double>{};
      final residualList = metaMap?['residuals'];
      if (residualList is List<dynamic>) {
        for (final item in residualList) {
          if (item is! Map<String, dynamic>) continue;
          final anchor = item['anchor']?.toString().toUpperCase();
          final value = (item['residual_mm'] as num?)?.toDouble();
          if (anchor != null && value != null) residuals[anchor] = value;
        }
      }
      return LayoutSummary(
        path: path.path,
        zConventionOk: lower < upper,
        hZ: z['H'],
        usOffset: null,
        usUsedAntCenter: usedMean,
        usUsedSource: usedSources.values.join(' ; '),
        latestUsAntCenter: latestUs.antennaCenterMm,
        latestUsPath: latestUs.path,
        usStatus: metaMap?['status']?.toString(),
        usRmsResidual: (metaMap?['rms_residual_mm'] as num?)?.toDouble(),
        usMaxResidual: (metaMap?['max_residual_mm'] as num?)?.toDouble(),
        usResiduals: residuals,
        usUsedSources: usedSources,
        latestUsByAnchor: latestByAnchor,
      );
    } catch (_) {
      return LayoutSummary.empty();
    }
  }
}

class SolverAnalysis {
  const SolverAnalysis({
    required this.mode,
    required this.sourcePath,
    required this.metrics,
    required this.layouts,
  });

  final String mode;
  final String? sourcePath;
  final List<SolverMetric> metrics;
  final List<AnchorLayoutData> layouts;

  factory SolverAnalysis.empty() => const SolverAnalysis(
    mode: 'none',
    sourcePath: null,
    metrics: [],
    layouts: [],
  );

  String get modeLabel => mode == 'v1-v4'
      ? 'Anchor Evaluation: V1 to V4-io'
      : mode == 'v4-io'
      ? 'Anchor Evaluation: V4-io'
      : 'Anchor Evaluation';

  SolverMetric? get bestAutopos {
    final valid = metrics.where((m) => m.autoposRms != null).toList();
    if (valid.isEmpty) return null;
    valid.sort((a, b) => a.autoposRms!.compareTo(b.autoposRms!));
    return valid.first;
  }

  AnchorLayoutData? layoutFor(String? version) {
    if (layouts.isEmpty) return null;
    return layouts.firstWhere(
      (l) => l.version == version,
      orElse: () => layouts.last,
    );
  }

  AnchorLayoutData? layoutByPath(String? path) {
    if (path == null || path.isEmpty) return null;
    for (final layout in layouts) {
      if (layout.path == path) return layout;
    }
    return null;
  }

  static Future<SolverAnalysis> read() async {
    final v1Root = Directory('$activeSolverOutputs/v1_to_v4_io_field_check');
    final v4Root = Directory('$activeSolverOutputs/v4io_field_check');
    const expected = ['v1-old', 'v2', 'v3-lite', 'v3-full', 'v4-io'];
    final v1Layouts = v1Root.existsSync()
        ? await _readLayouts(v1Root, expected)
        : <AnchorLayoutData>[];
    final v4Layouts = v4Root.existsSync()
        ? await _readLayouts(v4Root, const ['v4-io'])
        : <AnchorLayoutData>[];
    if (v1Root.existsSync()) {
      final byVersion = {for (final l in v1Layouts) l.version: l};
      for (final layout in v4Layouts) {
        byVersion.putIfAbsent(layout.version, () => layout);
      }
      final layouts = expected
          .map((v) => byVersion[v])
          .whereType<AnchorLayoutData>()
          .toList();
      final metrics = await _readMetrics(
        v1Root,
        expected,
        layoutVersions: layouts.map((e) => e.version).toSet(),
        fallbackRoot: v4Root.existsSync() ? v4Root : null,
      );
      return SolverAnalysis(
        mode: 'v1-v4',
        sourcePath: v1Root.path,
        metrics: metrics,
        layouts: layouts,
      );
    }

    if (v4Root.existsSync()) {
      return SolverAnalysis(
        mode: 'v4-io',
        sourcePath: v4Root.path,
        metrics: await _readMetrics(v4Root, const [
          'v4-io',
        ], layoutVersions: v4Layouts.map((e) => e.version).toSet()),
        layouts: v4Layouts,
      );
    }
    return SolverAnalysis.empty();
  }

  static Future<List<AnchorLayoutData>> _readLayouts(
    Directory root,
    List<String> versions,
  ) async {
    final out = <AnchorLayoutData>[];
    for (final version in versions) {
      final dir = Directory('${root.path}/$version');
      if (!dir.existsSync()) continue;
      final preferred = File('${dir.path}/layout_us_height.json');
      final fallback = File('${dir.path}/layout.json');
      final file = preferred.existsSync() ? preferred : fallback;
      if (!file.existsSync()) continue;
      final layout = await AnchorLayoutData.read(version, file);
      if (layout != null) out.add(layout);
    }
    return out;
  }

  static Future<List<SolverMetric>> _readMetrics(
    Directory root,
    List<String> versions, {
    required Set<String> layoutVersions,
    Directory? fallbackRoot,
  }) async {
    final byVersion = <String, SolverMetric>{};

    Future<void> loadTable(Directory dir) async {
      final table = File('${dir.path}/tables/version_summary.csv');
      if (!table.existsSync()) return;
      final rows = await readCsvRows(table);
      for (final row in rows) {
        final version = row['version'] ?? '';
        if (version.isEmpty) continue;
        byVersion[version] = SolverMetric(
          version: version,
          hasLayout: layoutVersions.contains(version),
          autoposRms: parseDouble(row['autopos_rms']),
          autoposP95: parseDouble(row['autopos_p95']),
          staticMedian: parseDouble(row['static_median']),
          staticP95: parseDouble(row['static_p95']),
          rotoDeltaRRms: parseDouble(row['roto_deltaR_rms']),
          rotoTurnCenterMedian: parseDouble(row['roto_turn_center_median']),
        );
      }
    }

    await loadTable(root);
    if (fallbackRoot != null) {
      await loadTable(fallbackRoot);
    }

    for (final version in versions) {
      final dir = Directory('${root.path}/$version');
      final fallbackDir = fallbackRoot == null
          ? null
          : Directory('${fallbackRoot.path}/$version');
      final derived =
          await SolverMetric.deriveFromVersionDir(version, dir) ??
          (fallbackDir == null
              ? null
              : await SolverMetric.deriveFromVersionDir(version, fallbackDir));
      if (derived != null) {
        final existing = byVersion[version];
        byVersion[version] = derived.copyWith(
          hasLayout: layoutVersions.contains(version),
          autoposRms: existing?.autoposRms ?? derived.autoposRms,
          autoposP95: existing?.autoposP95 ?? derived.autoposP95,
          staticMedian: existing?.staticMedian ?? derived.staticMedian,
          staticP95: existing?.staticP95 ?? derived.staticP95,
          rotoDeltaRRms: existing?.rotoDeltaRRms ?? derived.rotoDeltaRRms,
          rotoTurnCenterMedian:
              existing?.rotoTurnCenterMedian ?? derived.rotoTurnCenterMedian,
        );
      }
      byVersion.putIfAbsent(
        version,
        () => SolverMetric(
          version: version,
          hasLayout: layoutVersions.contains(version),
        ),
      );
    }
    return versions.map((v) => byVersion[v]).whereType<SolverMetric>().toList();
  }
}

class SolverMetric {
  const SolverMetric({
    required this.version,
    this.hasLayout = false,
    this.autoposRms,
    this.autoposP95,
    this.staticMedian,
    this.staticP95,
    this.rotoDeltaRRms,
    this.rotoTurnCenterMedian,
  });

  final String version;
  final bool hasLayout;
  final double? autoposRms;
  final double? autoposP95;
  final double? staticMedian;
  final double? staticP95;
  final double? rotoDeltaRRms;
  final double? rotoTurnCenterMedian;

  bool get hasMetrics =>
      autoposRms != null ||
      autoposP95 != null ||
      staticMedian != null ||
      staticP95 != null ||
      rotoDeltaRRms != null ||
      rotoTurnCenterMedian != null;

  String get statusLabel {
    if (!hasLayout) return 'missing';
    if (!hasMetrics) return 'layout only';
    return 'ok';
  }

  SolverMetric copyWith({
    bool? hasLayout,
    double? autoposRms,
    double? autoposP95,
    double? staticMedian,
    double? staticP95,
    double? rotoDeltaRRms,
    double? rotoTurnCenterMedian,
  }) {
    return SolverMetric(
      version: version,
      hasLayout: hasLayout ?? this.hasLayout,
      autoposRms: autoposRms ?? this.autoposRms,
      autoposP95: autoposP95 ?? this.autoposP95,
      staticMedian: staticMedian ?? this.staticMedian,
      staticP95: staticP95 ?? this.staticP95,
      rotoDeltaRRms: rotoDeltaRRms ?? this.rotoDeltaRRms,
      rotoTurnCenterMedian: rotoTurnCenterMedian ?? this.rotoTurnCenterMedian,
    );
  }

  static Future<SolverMetric?> deriveFromVersionDir(
    String version,
    Directory dir,
  ) async {
    if (!dir.existsSync()) return null;
    final residual = File('${dir.path}/layout_residuals_solve.csv');
    final staticCsv = File('${dir.path}/static_all_captures.csv');
    double? autoposRms;
    double? autoposP95;
    double? staticMedian;
    double? staticP95;

    if (residual.existsSync()) {
      final rows = await readCsvRows(residual);
      final abs = rows
          .map((r) => parseDouble(r['abs_residual_mm']))
          .whereType<double>()
          .toList();
      autoposRms = rms(abs);
      autoposP95 = percentile(abs, 0.95);
    }

    if (staticCsv.existsSync()) {
      final rows = await readCsvRows(staticCsv);
      final d3 = rows
          .where((r) => r['status'] == 'ok')
          .map((r) => parseDouble(r['D3_std']))
          .whereType<double>()
          .toList();
      staticMedian = percentile(d3, 0.50);
      staticP95 = percentile(d3, 0.95);
    }

    if (autoposRms == null &&
        autoposP95 == null &&
        staticMedian == null &&
        staticP95 == null) {
      return null;
    }
    return SolverMetric(
      version: version,
      autoposRms: autoposRms,
      autoposP95: autoposP95,
      staticMedian: staticMedian,
      staticP95: staticP95,
    );
  }
}

class AnchorLayoutData {
  const AnchorLayoutData({
    required this.version,
    required this.path,
    required this.points,
    required this.isUsHeightLayout,
    required this.usStatus,
    required this.usRmsResidual,
    required this.usMaxResidual,
    required this.usResiduals,
  });

  final String version;
  final String path;
  final List<AnchorPoint> points;
  final bool isUsHeightLayout;
  final String? usStatus;
  final double? usRmsResidual;
  final double? usMaxResidual;
  final Map<String, double> usResiduals;

  Map<String, AnchorPoint> get byLabel => {for (final p in points) p.label: p};
  double get minX => points.map((p) => p.x).reduce(math.min);
  double get maxX => points.map((p) => p.x).reduce(math.max);
  double get minY => points.map((p) => p.y).reduce(math.min);
  double get maxY => points.map((p) => p.y).reduce(math.max);
  double get minZ => points.map((p) => p.z).reduce(math.min);
  double get maxZ => points.map((p) => p.z).reduce(math.max);
  double get spanX => maxX - minX;
  double get spanY => maxY - minY;
  double get spanZ => maxZ - minZ;
  double get centerX => (minX + maxX) / 2;
  double get centerY => (minY + maxY) / 2;
  double get centerZ => (minZ + maxZ) / 2;

  String get usResidualLabel {
    if (usResiduals.isEmpty) return '-';
    return ['F', 'G', 'H']
        .where((anchor) => usResiduals.containsKey(anchor))
        .map((anchor) {
          final value = usResiduals[anchor]!;
          final sign = value >= 0 ? '+' : '';
          return '$anchor:$sign${value.toStringAsFixed(1)}';
        })
        .join(' ');
  }

  static Future<AnchorLayoutData?> read(String version, File file) async {
    try {
      final decoded =
          jsonDecode(await file.readAsString()) as Map<String, dynamic>;
      final list = decoded['anchors'];
      if (list is! List) return null;
      final points = <AnchorPoint>[];
      for (final item in list) {
        if (item is! Map<String, dynamic>) continue;
        final label = item['label']?.toString();
        final x = (item['x_mm'] as num?)?.toDouble();
        final y = (item['y_mm'] as num?)?.toDouble();
        final z = (item['z_mm'] as num?)?.toDouble();
        if (label == null || x == null || y == null || z == null) continue;
        points.add(AnchorPoint(label: label, x: x, y: y, z: z));
      }
      if (points.isEmpty) return null;
      final lower =
          points
              .where((p) => anchors.take(4).contains(p.label))
              .map((p) => p.z)
              .fold(0.0, (a, b) => a + b) /
          4;
      final upper =
          points
              .where((p) => anchors.skip(4).contains(p.label))
              .map((p) => p.z)
              .fold(0.0, (a, b) => a + b) /
          4;
      final isUsHeightLayout = file.path.endsWith('/layout_us_height.json');
      final zSign = isUsHeightLayout ? 1.0 : (lower < upper ? 1.0 : -1.0);
      var adjusted = points
          .map(
            (p) => AnchorPoint(label: p.label, x: p.x, y: p.y, z: p.z * zSign),
          )
          .toList();
      double signedArea(List<String> labels) {
        final byLabel = {for (final p in adjusted) p.label: p};
        var area = 0.0;
        for (var i = 0; i < labels.length; i++) {
          final a = byLabel[labels[i]];
          final b = byLabel[labels[(i + 1) % labels.length]];
          if (a == null || b == null) return 0.0;
          area += a.x * b.y - b.x * a.y;
        }
        return area / 2.0;
      }

      // Distances cannot distinguish a mirror image. For display and field use,
      // force the documented right-handed convention:
      // ABCD and EFGH are counter-clockwise in XY while ABCD stays below EFGH.
      final lowerArea = signedArea(const ['A', 'B', 'C', 'D']);
      final upperArea = signedArea(const ['E', 'F', 'G', 'H']);
      if (lowerArea < 0 || upperArea < 0) {
        adjusted = adjusted
            .map((p) => AnchorPoint(label: p.label, x: p.x, y: -p.y, z: p.z))
            .toList();
      }
      adjusted.sort((a, b) => a.label.compareTo(b.label));
      final meta = decoded['extra']?['ultrasound_height_alignment'];
      final metaMap = meta is Map<String, dynamic> ? meta : null;
      final residuals = <String, double>{};
      final residualList = metaMap?['residuals'];
      if (residualList is List<dynamic>) {
        for (final item in residualList) {
          if (item is! Map<String, dynamic>) continue;
          final anchor = item['anchor']?.toString().toUpperCase();
          final value = (item['residual_mm'] as num?)?.toDouble();
          if (anchor != null && value != null) residuals[anchor] = value;
        }
      }
      return AnchorLayoutData(
        version: version,
        path: file.path,
        points: adjusted,
        isUsHeightLayout: isUsHeightLayout,
        usStatus: metaMap?['status']?.toString(),
        usRmsResidual: (metaMap?['rms_residual_mm'] as num?)?.toDouble(),
        usMaxResidual: (metaMap?['max_residual_mm'] as num?)?.toDouble(),
        usResiduals: residuals,
      );
    } catch (_) {
      return null;
    }
  }
}

class AnchorPoint {
  const AnchorPoint({
    required this.label,
    required this.x,
    required this.y,
    required this.z,
  });

  final String label;
  final double x;
  final double y;
  final double z;
}

class ExperimentPlan {
  const ExperimentPlan({
    required this.staticSection,
    required this.rotoSection,
    required this.wandSection,
  });

  final ExperimentPlanSection staticSection;
  final ExperimentPlanSection rotoSection;
  final ExperimentPlanSection wandSection;

  factory ExperimentPlan.empty() => const ExperimentPlan(
    staticSection: ExperimentPlanSection.empty(),
    rotoSection: ExperimentPlanSection.empty(),
    wandSection: ExperimentPlanSection.empty(),
  );

  ExperimentPlanSection sectionFor(String kind) {
    switch (kind) {
      case 'roto':
        return rotoSection;
      case 'wand':
        return wandSection;
      default:
        return staticSection;
    }
  }

  static Future<ExperimentPlan> read() async {
    final file = File(experimentPlanShort);
    if (!file.existsSync()) return ExperimentPlan.empty();
    final text = await file.readAsString();
    return ExperimentPlan(
      staticSection: _parseSection(
        text,
        start: '## Phase 2 - Static BSF66F Dataset',
        next: '## Phase 3 - RotoArm Dataset',
        minimumPrefix: 'If time is short, minimum static set:',
      ),
      rotoSection: _parseSection(
        text,
        start: '## Phase 3 - RotoArm Dataset',
        next: '## Phase 4 - Wand Dataset',
        minimumPrefix: 'If time is short, minimum Roto set:',
      ),
      wandSection: _parseSection(
        text,
        start: '## Phase 4 - Wand Dataset',
        next: '## Stop Conditions',
        minimumPrefix: '',
      ),
    );
  }

  static ExperimentPlanSection _parseSection(
    String text, {
    required String start,
    required String next,
    required String minimumPrefix,
  }) {
    final startIndex = text.indexOf(start);
    if (startIndex < 0) return const ExperimentPlanSection.empty();
    final nextIndex = text.indexOf(next, startIndex + start.length);
    final section = text.substring(
      startIndex,
      nextIndex < 0 ? text.length : nextIndex,
    );
    final rows = <ExperimentPlanRow>[];
    for (final raw in const LineSplitter().convert(section)) {
      final line = raw.trim();
      if (!line.startsWith('|')) continue;
      if (line.contains('---') || line.contains('| ID |')) continue;
      final cells = line
          .split('|')
          .map((cell) => cell.trim())
          .where((cell) => cell.isNotEmpty)
          .toList();
      if (cells.length < 2) continue;
      final id = cells.first;
      if (!RegExp(r'^(ID|R|W)\d+', caseSensitive: false).hasMatch(id)) {
        continue;
      }
      rows.add(ExperimentPlanRow(id: id, description: cells[1]));
    }

    final minimum = <String>[];
    if (minimumPrefix.isNotEmpty) {
      final minIndex = section.indexOf(minimumPrefix);
      if (minIndex >= 0) {
        final after = section.substring(minIndex + minimumPrefix.length);
        final block = RegExp(r'```text\s*([\s\S]*?)```').firstMatch(after);
        final ids = block?.group(1) ?? '';
        minimum.addAll(
          RegExp(
            r'\b(?:ID|R|W)\d+\b',
          ).allMatches(ids).map((m) => m.group(0)!).toList(),
        );
      }
    }

    final notes = <String>[];
    final noteIndex = section.indexOf('Notes:');
    if (noteIndex >= 0) {
      for (final raw in const LineSplitter().convert(
        section.substring(noteIndex),
      )) {
        final line = raw.trim();
        if (line.startsWith('- ')) notes.add(line.substring(2));
      }
    }
    if (section.contains('Known Wand geometry:')) {
      notes.add(
        'Known geometry: BSCCF4--285mm--T center--385mm--BS9336; T center--595mm--BS955A',
      );
    }
    return ExperimentPlanSection(rows: rows, minimum: minimum, notes: notes);
  }
}

class ExperimentPlanSection {
  const ExperimentPlanSection({
    required this.rows,
    required this.minimum,
    required this.notes,
  });

  const ExperimentPlanSection.empty()
    : rows = const [],
      minimum = const [],
      notes = const [];

  final List<ExperimentPlanRow> rows;
  final List<String> minimum;
  final List<String> notes;
}

class ExperimentPlanRow {
  const ExperimentPlanRow({required this.id, required this.description});

  final String id;
  final String description;
}

class CaptureSessionInfo {
  const CaptureSessionInfo({
    required this.kind,
    required this.id,
    required this.path,
    required this.modified,
  });

  final String kind;
  final String id;
  final String path;
  final DateTime modified;

  @override
  bool operator ==(Object other) =>
      other is CaptureSessionInfo && other.path == path;

  @override
  int get hashCode => path.hashCode;

  String get modifiedLabel {
    final local = modified.toLocal();
    String two(int v) => v.toString().padLeft(2, '0');
    return '${local.year}${two(local.month)}${two(local.day)} ${two(local.hour)}:${two(local.minute)}';
  }

  static Future<List<CaptureSessionInfo>> scan() async {
    final root = Directory(capturesRoot);
    if (!root.existsSync()) return [];
    final out = <CaptureSessionInfo>[];
    for (final dir in root.listSync().whereType<Directory>()) {
      final name = dir.path.split('/').last;
      final hasTrAll = dir.listSync().whereType<Directory>().any(
        (d) => File('${d.path}/tr_all.csv').existsSync(),
      );
      if (!File('${dir.path}/summary.json').existsSync() && !hasTrAll) {
        continue;
      }
      final kind = name.startsWith('static_')
          ? 'static'
          : name.startsWith('roto_')
          ? 'roto'
          : name.startsWith('wand3_')
          ? 'wand'
          : name.startsWith('free_')
          ? 'free'
          : null;
      if (kind == null) continue;
      final id =
          RegExp(
            r'^(?:static|roto|wand3|free)_([^_]+)_',
          ).firstMatch(name)?.group(1) ??
          name;
      out.add(
        CaptureSessionInfo(
          kind: kind,
          id: id,
          path: dir.path,
          modified: dir.statSync().modified,
        ),
      );
    }
    out.sort((a, b) => b.modified.compareTo(a.modified));
    return out;
  }
}

class SolverSweepInfo {
  const SolverSweepInfo({
    required this.id,
    required this.name,
    required this.path,
    required this.modified,
    required this.swSets,
  });

  final String id;
  final String name;
  final String path;
  final DateTime modified;
  final int swSets;

  @override
  bool operator ==(Object other) =>
      other is SolverSweepInfo && other.path == path;

  @override
  int get hashCode => path.hashCode;

  String get modifiedLabel {
    final local = modified.toLocal();
    String two(int v) => v.toString().padLeft(2, '0');
    return '${local.year}${two(local.month)}${two(local.day)} ${two(local.hour)}:${two(local.minute)}';
  }

  String get shortLabel => '$id $swSets sets $modifiedLabel';

  static Future<List<SolverSweepInfo>> scan() async {
    final root = Directory(capturesRoot);
    if (!root.existsSync()) return [];
    final out = <SolverSweepInfo>[];
    for (final dir in root.listSync().whereType<Directory>()) {
      final name = dir.path.split('/').last;
      if (!name.startsWith('sweep_')) continue;
      final summary = _findSummary(dir);
      if (summary == null) continue;
      final info = await _fromSummary(dir, summary);
      if (info != null) out.add(info);
    }
    out.sort((a, b) => b.modified.compareTo(a.modified));
    return out;
  }

  static File? _findSummary(Directory dir) {
    final files =
        dir
            .listSync(recursive: true)
            .whereType<File>()
            .where((f) => f.path.endsWith('/summary.json'))
            .toList()
          ..sort(
            (a, b) => b.statSync().modified.compareTo(a.statSync().modified),
          );
    for (final file in files) {
      try {
        final decoded = jsonDecode(file.readAsStringSync());
        if (decoded is Map<String, dynamic> &&
            decoded['rounds'] is Map<String, dynamic>) {
          return file;
        }
      } catch (_) {
        continue;
      }
    }
    return null;
  }

  static Future<SolverSweepInfo?> _fromSummary(
    Directory dir,
    File summary,
  ) async {
    try {
      final decoded =
          jsonDecode(await summary.readAsString()) as Map<String, dynamic>;
      final rounds = decoded['rounds'];
      if (rounds is! Map<String, dynamic>) return null;
      final order =
          (decoded['order'] as List<dynamic>?)?.cast<Object?>() ?? anchors;
      for (final item in order) {
        final label = item.toString();
        final row = rounds[label];
        if (row is! Map<String, dynamic>) return null;
        final swCount = (row['sw_count'] as num?)?.toInt() ?? 0;
        if (row['success'] != true || swCount <= 0) return null;
      }
      final name = dir.path.split('/').last;
      final id =
          RegExp(
            r'^sweep_([^_]+(?:_[^_]+)*)_\d+_prewarm\d+_\d+$',
          ).firstMatch(name)?.group(1) ??
          RegExp(r'^sweep_([^_]+)_').firstMatch(name)?.group(1) ??
          name;
      final swSets =
          (decoded['sw_sets'] as num?)?.toInt() ??
          int.tryParse(
            RegExp(r'_([0-9]+)_prewarm').firstMatch(name)?.group(1) ?? '',
          ) ??
          0;
      return SolverSweepInfo(
        id: id,
        name: name,
        path: dir.path,
        modified: summary.statSync().modified,
        swSets: swSets,
      );
    } catch (_) {
      return null;
    }
  }
}

class StagedSweepInfo {
  const StagedSweepInfo({
    required this.status,
    required this.source,
    required this.requestedSweep,
    required this.selectedSweepDir,
    required this.rows,
    required this.manifestModified,
  });

  final String? status;
  final String? source;
  final String? requestedSweep;
  final String? selectedSweepDir;
  final int? rows;
  final DateTime? manifestModified;

  factory StagedSweepInfo.empty() => const StagedSweepInfo(
    status: null,
    source: null,
    requestedSweep: null,
    selectedSweepDir: null,
    rows: null,
    manifestModified: null,
  );

  String? get sweepName {
    final dir = selectedSweepDir;
    if (dir == null || dir.isEmpty) return null;
    return dir.split('/').last;
  }

  static Future<StagedSweepInfo> read() async {
    final file = File('$activeStagedDataset/stage_manifest.json');
    if (!file.existsSync()) return StagedSweepInfo.empty();
    try {
      final decoded = jsonDecode(await file.readAsString());
      if (decoded is! Map<String, dynamic>) return StagedSweepInfo.empty();
      final sweep = decoded['sweep'];
      if (sweep is! Map<String, dynamic>) return StagedSweepInfo.empty();
      return StagedSweepInfo(
        status: sweep['status']?.toString(),
        source: sweep['source']?.toString(),
        requestedSweep: sweep['requested_sweep']?.toString(),
        selectedSweepDir: sweep['selected_sweep_dir']?.toString(),
        rows: (sweep['rows'] as num?)?.toInt(),
        manifestModified: file.statSync().modified,
      );
    } catch (_) {
      return StagedSweepInfo.empty();
    }
  }
}

class TrajectoryData {
  const TrajectoryData({
    required this.sourcePath,
    required this.candidateFrames,
    required this.solvedFrames,
    required this.expectedTags,
    required this.candidateFramesByTag,
    required this.solvedFramesByTag,
    required this.tags,
    required this.frames,
  });

  final String sourcePath;
  final int candidateFrames;
  final int solvedFrames;
  final List<String> expectedTags;
  final Map<String, int> candidateFramesByTag;
  final Map<String, int> solvedFramesByTag;
  final List<String> tags;
  final List<TrajectoryFrame> frames;

  List<TrajectoryFrame> framesFor(String? tag) {
    if (tag == null || tag.isEmpty) return frames;
    return frames.where((f) => f.tag == tag).toList();
  }

  List<TrajectoryFrame> framesForTags(Set<String> selectedTags) {
    if (selectedTags.isEmpty) return [];
    final out = frames.where((f) => selectedTags.contains(f.tag)).toList();
    out.sort((a, b) {
      final byTime = a.hostElapsedS.compareTo(b.hostElapsedS);
      if (byTime != 0) return byTime;
      return a.tag.compareTo(b.tag);
    });
    return out;
  }

  List<RotoTrajectoryEstimate> estimatesForTags(Set<String> selectedTags) {
    final out = <RotoTrajectoryEstimate>[];
    for (final tag in tags.where(selectedTags.contains)) {
      final estimate = RotoTrajectoryEstimate.fromFrames(tag, framesFor(tag));
      if (estimate != null) out.add(estimate);
    }
    return out;
  }

  static Future<TrajectoryData> read(File file) async {
    final decoded =
        jsonDecode(await file.readAsString()) as Map<String, dynamic>;
    final rawFrames = decoded['frames'];
    final frames = rawFrames is List
        ? rawFrames
              .whereType<Map<String, dynamic>>()
              .map(TrajectoryFrame.fromJson)
              .whereType<TrajectoryFrame>()
              .toList()
        : <TrajectoryFrame>[];
    return TrajectoryData(
      sourcePath: decoded['tr_all_csv']?.toString() ?? file.path,
      candidateFrames:
          (decoded['candidate_frames'] as num?)?.toInt() ?? frames.length,
      solvedFrames:
          (decoded['solved_frames'] as num?)?.toInt() ?? frames.length,
      expectedTags:
          (decoded['expected_tags'] as List?)
              ?.map((e) => e.toString())
              .toList() ??
          const <String>[],
      candidateFramesByTag:
          (decoded['candidate_frames_by_tag'] as Map?)?.map(
            (key, value) => MapEntry(key.toString(), (value as num).toInt()),
          ) ??
          const <String, int>{},
      solvedFramesByTag:
          (decoded['solved_frames_by_tag'] as Map?)?.map(
            (key, value) => MapEntry(key.toString(), (value as num).toInt()),
          ) ??
          const <String, int>{},
      tags:
          (decoded['tags'] as List?)?.map((e) => e.toString()).toList() ??
          frames.map((e) => e.tag).toSet().toList(),
      frames: frames,
    );
  }
}

class TrajectoryFrame {
  const TrajectoryFrame({
    required this.tag,
    required this.sweep,
    required this.hostElapsedS,
    required this.xMm,
    required this.yMm,
    required this.zMm,
    required this.anchorsUsed,
    required this.residualRmsMm,
  });

  final String tag;
  final int sweep;
  final double hostElapsedS;
  final double xMm;
  final double yMm;
  final double zMm;
  final int anchorsUsed;
  final double residualRmsMm;

  static TrajectoryFrame? fromJson(Map<String, dynamic> json) {
    try {
      return TrajectoryFrame(
        tag: json['tag'].toString(),
        sweep: (json['sweep'] as num).toInt(),
        hostElapsedS: (json['host_elapsed_s'] as num?)?.toDouble() ?? 0,
        xMm: (json['x_mm'] as num).toDouble(),
        yMm: (json['y_mm'] as num).toDouble(),
        zMm: (json['z_mm'] as num).toDouble(),
        anchorsUsed: (json['anchors_used'] as num?)?.toInt() ?? 0,
        residualRmsMm: (json['residual_rms_mm'] as num?)?.toDouble() ?? 0,
      );
    } catch (_) {
      return null;
    }
  }
}

class RotoTrajectoryEstimate {
  const RotoTrajectoryEstimate({
    required this.tag,
    required this.centerX,
    required this.centerY,
    required this.centerZ,
    required this.radiusMm,
    required this.radiusRmsMm,
  });

  final String tag;
  final double centerX;
  final double centerY;
  final double centerZ;
  final double radiusMm;
  final double radiusRmsMm;

  static RotoTrajectoryEstimate? fromFrames(
    String tag,
    List<TrajectoryFrame> frames,
  ) {
    if (frames.length < 8) return null;
    final cx = frames.map((f) => f.xMm).reduce((a, b) => a + b) / frames.length;
    final cy = frames.map((f) => f.yMm).reduce((a, b) => a + b) / frames.length;
    final cz = frames.map((f) => f.zMm).reduce((a, b) => a + b) / frames.length;
    final radii = frames
        .map(
          (f) => math.sqrt(
            math.pow(f.xMm - cx, 2) +
                math.pow(f.yMm - cy, 2) +
                math.pow(f.zMm - cz, 2),
          ),
        )
        .toList();
    final radius = radii.reduce((a, b) => a + b) / radii.length;
    final rms = math.sqrt(
      radii.map((r) => math.pow(r - radius, 2)).reduce((a, b) => a + b) /
          radii.length,
    );
    return RotoTrajectoryEstimate(
      tag: tag,
      centerX: cx,
      centerY: cy,
      centerZ: cz,
      radiusMm: radius,
      radiusRmsMm: rms,
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
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
      decoration: BoxDecoration(
        color: toneColor(tone).withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: toneColor(tone).withValues(alpha: 0.28)),
      ),
      child: Text(
        label.isEmpty ? value : '$label: $value',
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(
          color: toneColor(tone),
          fontWeight: FontWeight.w700,
          fontSize: 12,
        ),
      ),
    );
  }
}

Future<void> showBioSpurNotice(
  BuildContext context, {
  required String title,
  required String message,
}) {
  return showDialog<void>(
    context: context,
    builder: (context) {
      return Dialog(
        backgroundColor: Colors.transparent,
        insetPadding: const EdgeInsets.symmetric(horizontal: 28, vertical: 24),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 460),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(8),
            child: Stack(
              children: [
                const Positioned.fill(child: BiospurBackground()),
                Container(
                  decoration: BoxDecoration(
                    color: biospurBlack.withValues(alpha: 0.78),
                    border: Border.all(color: panelLine),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  padding: const EdgeInsets.all(18),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          const Icon(
                            Icons.error_outline,
                            color: controlGreen,
                            size: 24,
                          ),
                          const SizedBox(width: 10),
                          Expanded(
                            child: Text(
                              title,
                              style: const TextStyle(
                                fontSize: 18,
                                fontWeight: FontWeight.w800,
                              ),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      Text(
                        message,
                        style: const TextStyle(color: mutedText, height: 1.35),
                      ),
                      const SizedBox(height: 18),
                      Align(
                        alignment: Alignment.centerRight,
                        child: FilledButton(
                          onPressed: () => Navigator.of(context).pop(),
                          child: const Text('OK'),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      );
    },
  );
}

Future<bool> showBioSpurConfirm(
  BuildContext context, {
  required String title,
  required String message,
  required String confirmLabel,
}) async {
  final result = await showDialog<bool>(
    context: context,
    builder: (context) {
      return Dialog(
        backgroundColor: Colors.transparent,
        insetPadding: const EdgeInsets.symmetric(horizontal: 28, vertical: 24),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 520),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(8),
            child: Stack(
              children: [
                const Positioned.fill(child: BiospurBackground()),
                Container(
                  decoration: BoxDecoration(
                    color: biospurBlack.withValues(alpha: 0.82),
                    border: Border.all(color: toneColor(PillTone.bad)),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  padding: const EdgeInsets.all(18),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Icon(
                            Icons.warning_amber_rounded,
                            color: toneColor(PillTone.bad),
                            size: 26,
                          ),
                          const SizedBox(width: 10),
                          Expanded(
                            child: Text(
                              title,
                              style: const TextStyle(
                                fontSize: 18,
                                fontWeight: FontWeight.w800,
                              ),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      Text(
                        message,
                        style: const TextStyle(color: mutedText, height: 1.35),
                      ),
                      const SizedBox(height: 18),
                      Row(
                        mainAxisAlignment: MainAxisAlignment.end,
                        children: [
                          TextButton(
                            onPressed: () => Navigator.of(context).pop(false),
                            child: const Text('Cancel'),
                          ),
                          const SizedBox(width: 8),
                          FilledButton.icon(
                            style: FilledButton.styleFrom(
                              backgroundColor: toneColor(PillTone.bad),
                              foregroundColor: Colors.white,
                            ),
                            onPressed: () => Navigator.of(context).pop(true),
                            icon: const Icon(Icons.delete_forever_outlined),
                            label: Text(confirmLabel),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      );
    },
  );
  return result ?? false;
}

class LegendDot extends StatelessWidget {
  const LegendDot({super.key, required this.color, required this.label});

  final Color color;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 10,
          height: 10,
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
        ),
        const SizedBox(width: 5),
        Text(label, style: const TextStyle(fontSize: 12)),
      ],
    );
  }
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
      return controlGreen;
    case PillTone.neutral:
      return const Color(0xFF64748B);
  }
}

PillTone sampleTone(DistanceSample? sample) {
  if (sample == null) return PillTone.neutral;
  if (sample.distanceMm <= 0 || sample.quality < 90) return PillTone.bad;
  if (sample.quality <= 95) return PillTone.warn;
  return PillTone.good;
}

String percent(double value) => '${(value * 100).toStringAsFixed(1)}%';

String fmtMm(double? value) {
  if (value == null || value.isNaN) return '-';
  return '${value.toStringAsFixed(1)} mm';
}

String shortDateTime(DateTime value) {
  final local = value.toLocal();
  String two(int v) => v.toString().padLeft(2, '0');
  return '${two(local.month)}-${two(local.day)} ${two(local.hour)}:${two(local.minute)}';
}

double? parseDouble(String? value) {
  if (value == null || value.isEmpty) return null;
  return double.tryParse(value);
}

double? rms(List<double> values) {
  if (values.isEmpty) return null;
  final sumSq = values.fold(0.0, (sum, v) => sum + v * v);
  return math.sqrt(sumSq / values.length);
}

double? percentile(List<double> values, double q) {
  if (values.isEmpty) return null;
  final sorted = [...values]..sort();
  final pos = (sorted.length - 1) * q.clamp(0.0, 1.0);
  final lo = pos.floor();
  final hi = pos.ceil();
  if (lo == hi) return sorted[lo];
  final t = pos - lo;
  return sorted[lo] * (1 - t) + sorted[hi] * t;
}

Future<List<Map<String, String>>> readCsvRows(File file) async {
  final lines = await file.readAsLines();
  if (lines.length < 2) return [];
  final header = splitCsvLine(lines.first);
  final rows = <Map<String, String>>[];
  for (final line in lines.skip(1)) {
    if (line.trim().isEmpty) continue;
    final values = splitCsvLine(line);
    rows.add({
      for (var i = 0; i < header.length && i < values.length; i++)
        header[i]: values[i],
    });
  }
  return rows;
}

List<String> splitCsvLine(String line) {
  final out = <String>[];
  final buf = StringBuffer();
  var quoted = false;
  for (var i = 0; i < line.length; i++) {
    final ch = line[i];
    if (ch == '"') {
      quoted = !quoted;
    } else if (ch == ',' && !quoted) {
      out.add(buf.toString());
      buf.clear();
    } else {
      buf.write(ch);
    }
  }
  out.add(buf.toString());
  return out;
}

String? emptyToNull(String? value) {
  if (value == null || value.isEmpty) return null;
  return value;
}

String shellQuote(String value) => "'${value.replaceAll("'", "'\\''")}'";

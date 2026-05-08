import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';

import '../shared/models/log_models.dart';
import '../shared/services/device_port_detector.dart';
import '../shared/services/log_repository.dart';
import '../shared/services/script_runner.dart';

class AutopositioningPage extends StatefulWidget {
  const AutopositioningPage({super.key});

  @override
  State<AutopositioningPage> createState() => _AutopositioningPageState();
}

class _AutopositioningPageState extends State<AutopositioningPage> {
  late final Timer _timer;
  late final StreamSubscription<void> _runnerSub;
  late final TextEditingController _targetsController;
  late Future<_AutopositioningSnapshot> _future;
  DevicePortSnapshot _ports = const DevicePortSnapshot(
    ports: [],
    masterAnchor: null,
    masterTag: null,
    listener: null,
  );
  bool _armPush = false;
  bool _wasRunning = false;
  int _seenCompletedRuns = 0;

  @override
  void initState() {
    super.initState();
    _targetsController = TextEditingController(text: 'BSF66F,BS2DCE,BSDC91');
    _future = _load();
    _scanPorts();
    _timer = Timer.periodic(const Duration(seconds: 5), (_) {
      if (mounted) {
        setState(() {
          _future = _load();
        });
      }
    });
    _runnerSub = ScriptRunner.instance.changes.listen((_) {
      if (!mounted) return;
      _handleRunnerChange();
      setState(() {});
    });
  }

  @override
  void dispose() {
    _runnerSub.cancel();
    _timer.cancel();
    _targetsController.dispose();
    super.dispose();
  }

  Future<_AutopositioningSnapshot> _load() async {
    final repo = LogRepository.instance;
    final runState = await _loadRunState();
    return _AutopositioningSnapshot(
      layout: await repo.loadRuntimeAnchorLayout(),
      rangesTail: await repo.latestRef115Ranges(limit: 12),
      refSummary: await repo.latestRef115Summary(),
      candidate: await _loadCandidate(),
      runState: runState,
    );
  }

  Future<AutoPosRunState> _loadRunState() async {
    final outDirFile = File('/tmp/biospur_gui_autopos_outdir');
    if (!outDirFile.existsSync()) {
      return const AutoPosRunState(baseOutDir: null);
    }
    final base = (await outDirFile.readAsString()).trim();
    if (base.isEmpty) {
      return const AutoPosRunState(baseOutDir: null);
    }
    final autoposDir = Directory('$base/autopos');
    final summary = File('${autoposDir.path}/summary.json');
    final pairs = File('${autoposDir.path}/pairs_all.csv');
    final solve = File('${autoposDir.path}/inter_anchor_free_solve.json');
    final candidate = File('${autoposDir.path}/layout_candidate.json');
    final validationDir = Directory('$base/validation');
    final latestPushDir = _latestChildDir(base, 'apos_push_');
    return AutoPosRunState(
      baseOutDir: base,
      autoposDir: autoposDir.path,
      summaryExists: summary.existsSync(),
      pairsExists: pairs.existsSync(),
      solveExists: solve.existsSync(),
      candidateExists: candidate.existsSync(),
      validationExists: validationDir.existsSync(),
      latestPushDir: latestPushDir,
      latestPreflightDir: _latestChildDir('logs', 'gui_anchor_preflight_'),
    );
  }

  String? _latestChildDir(String parent, String prefix) {
    final dir = Directory(parent);
    if (!dir.existsSync()) return null;
    final matches = dir
        .listSync()
        .whereType<Directory>()
        .where((d) => d.path.split('/').last.startsWith(prefix))
        .toList()
      ..sort((a, b) => b.path.compareTo(a.path));
    return matches.isEmpty ? null : matches.first.path;
  }

  Future<LayoutCandidateSnapshot?> _loadCandidate() async {
    final outDirFile = File('/tmp/biospur_gui_autopos_outdir');
    if (!outDirFile.existsSync()) {
      return null;
    }
    final base = outDirFile.readAsStringSync().trim();
    if (base.isEmpty) {
      return null;
    }
    final candidateFile = File('$base/autopos/layout_candidate.json');
    if (!candidateFile.existsSync()) {
      return null;
    }
    final decoded =
        jsonDecode(await candidateFile.readAsString()) as Map<String, dynamic>;
    final anchors = <AnchorLayoutEntry>[];
    final anchorList = decoded['anchors'] as List<dynamic>? ?? const [];
    for (final item in anchorList) {
      if (item is! Map<String, dynamic>) continue;
      anchors.add(
        AnchorLayoutEntry(
          id: item['id']?.toString() ?? '?',
          name: item['label']?.toString() ?? '?',
          x: (item['x_mm'] as num?)?.toInt() ?? 0,
          y: (item['y_mm'] as num?)?.toInt() ?? 0,
          z: (item['z_mm'] as num?)?.toInt() ?? 0,
        ),
      );
    }
    return LayoutCandidateSnapshot(
      path: candidateFile.path,
      selectedResult: decoded['selected_result']?.toString() ?? 'unknown',
      sourceFile: decoded['source_file']?.toString() ?? '',
      stats: decoded['stats'] as Map<String, dynamic>? ?? const {},
      anchors: anchors,
    );
  }

  Future<void> _scanPorts() async {
    final snapshot = await DevicePortDetector.instance.scan();
    if (!mounted) return;
    setState(() {
      _ports = snapshot;
    });
    final found = <String>[];
    if (snapshot.masterAnchor != null) found.add('Master_Anchor');
    if (snapshot.masterTag != null) found.add('Master_Tag');
    if (snapshot.listener != null) found.add('Listener');
    _showBanner(
      found.isEmpty
          ? 'No known control ports detected.'
          : 'Detected: ${found.join(', ')}',
    );
  }

  String? get _anchorPort => _ports.masterAnchor?.path;
  String? get _tagPort => _ports.masterTag?.path;

  bool get _hasAnchor => _anchorPort != null;
  bool get _hasTag => _tagPort != null;
  bool get _hasDistinctMasters =>
      _hasAnchor && _hasTag && _anchorPort != _tagPort;

  Future<void> _run(String name, String command) async {
    if (ScriptRunner.instance.isRunning) {
      _showBanner('Another workflow step is already running.');
      return;
    }
    _showBanner('Starting: $name');
    await ScriptRunner.instance.start(name, command);
  }

  void _handleRunnerChange() {
    final runner = ScriptRunner.instance;
    final isRunning = runner.isRunning;
    if (!_wasRunning && isRunning && runner.activeName != null) {
      _showBanner('Running: ${runner.activeName}');
    }
    if (_seenCompletedRuns != runner.completedRuns) {
      _seenCompletedRuns = runner.completedRuns;
      final name = runner.lastFinishedName ?? 'Command';
      final code = runner.lastExitCode ?? -1;
      _showBanner(
        code == 0 ? '$name finished successfully.' : '$name failed (exit $code).',
        isError: code != 0,
      );
    }
    _wasRunning = isRunning;
  }

  void _showBanner(String message, {bool isError = false}) {
    if (!mounted) return;
    final messenger = ScaffoldMessenger.maybeOf(context);
    messenger?.hideCurrentSnackBar();
    messenger?.showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: isError ? Colors.red.shade700 : null,
        duration: const Duration(seconds: 3),
      ),
    );
  }

  String _q(String value) => "'${value.replaceAll("'", "'\\''")}'";

  String _preflightCommand() {
    final cmd = [
      'python3 scripts/verify_all_anchor_responder_runtime.py',
      '--port ${_q(_anchorPort ?? '')}',
      '--out-dir logs/gui_anchor_preflight_\$(date +%Y%m%d_%H%M%S)',
      '--live-output',
    ].join(' ');
    return cmd;
  }

  String _sweepCommand() {
    return [
      'OUT=logs/gui_autopos_\$(date +%Y%m%d_%H%M%S)',
      'mkdir -p "\$OUT"',
      'echo "\$OUT" > /tmp/biospur_gui_autopos_outdir',
      [
        'python3 scripts/run_autopos_sweep_loop.py',
        '--port ${_q(_anchorPort ?? '')}',
        '--order ABCDEFGH',
        '--sw-sets 500',
        '--prewarm-sw-sets 10',
        '--timeout-s 2400',
        '--warmup-min-quality 90',
        '--quiet-tag-name -',
        '--no-bootstrap-autopos-reset',
        '--reuse-resident-anchor-master',
        '--out-dir "\$OUT/autopos"',
      ].join(' '),
    ].join(' && ');
  }

  String _extractPairsCommand() {
    return [
      'OUT=\$(cat /tmp/biospur_gui_autopos_outdir)',
      [
        'python3 scripts/autopos_extract_pairs_from_sweep_summary.py',
        '--summary-json "\$OUT/autopos/summary.json"',
        '--out-csv "\$OUT/autopos/pairs_all.csv"',
      ].join(' '),
    ].join(' && ');
  }

  String _solveLayoutCommand() {
    return [
      'OUT=\$(cat /tmp/biospur_gui_autopos_outdir)',
      [
        'python3 autopos_pipeline/scripts/solve_inter_anchor_free.py',
        '--pairs-csv "\$OUT/autopos/pairs_all.csv"',
        '--init-layout data/anchor_layout_ah_runtime.json',
        '--output "\$OUT/autopos/inter_anchor_free_solve.json"',
        '--sigma-mm 30',
        '--f-scale 2',
        '--losses linear,huber,soft_l1',
      ].join(' '),
      [
        'python3 scripts/gui_prepare_layout_candidate.py',
        '--input "\$OUT/autopos/inter_anchor_free_solve.json"',
        '--output "\$OUT/autopos/layout_candidate.json"',
      ].join(' '),
    ].join(' && ');
  }

  String _pushAposCommand() {
    return [
      'OUT=\$(cat /tmp/biospur_gui_autopos_outdir)',
      [
        'python3 scripts/gui_push_apos_verified.py',
        '--port ${_q(_tagPort ?? '')}',
        '--layout-input "\$OUT/autopos/layout_candidate.json"',
        '--targets ${_q(_targetsController.text.trim())}',
        '--out-dir "\$OUT/apos_push_\$(date +%Y%m%d_%H%M%S)"',
      ].join(' '),
    ].join(' && ');
  }

  String _validationCommand() {
    return [
      'OUT=\$(cat /tmp/biospur_gui_autopos_outdir)',
      [
        'python3 scripts/run_dual_master_tdma_capture.py',
        '--anchor-port ${_q(_anchorPort ?? '')}',
        '--tag-port ${_q(_tagPort ?? '')}',
        '--duration 180',
        '--targets BSF66F,BS2DCE,BSDC91',
        '--profiles BSF66F:static,BS2DCE:roto,BSDC91:roto',
        '--static-hz 5',
        '--roto-hz 10',
        '--motion-hz 5',
        '--with-listener',
        '--out-dir "\$OUT/validation"',
      ].join(' '),
    ].join(' && ');
  }

  List<WorkflowStepState> _buildStepStates({
    required ScriptRunner runner,
    required AutoPosRunState runState,
    required LayoutCandidateSnapshot? candidate,
  }) {
    String? failureFor(String stepName) {
      if (runner.lastFinishedName != stepName || runner.lastExitCode == 0) {
        return null;
      }
      return runner.lastFailureHint ?? 'Command exited with code ${runner.lastExitCode}.';
    }

    final base = runState.baseOutDir ?? 'logs/gui_autopos_<timestamp>';
    final autopos = runState.autoposDir ?? '$base/autopos';

    return [
      WorkflowStepState(
        title: '1. Preflight Anchors',
        ready: _hasAnchor,
        done: runState.latestPreflightDir != null,
        prerequisites: [
          _hasAnchor ? 'Master_Anchor detected' : 'Need Master_Anchor control port',
        ],
        outputs: [
          runState.latestPreflightDir ?? 'logs/gui_anchor_preflight_<timestamp>',
        ],
        failureHint: failureFor('Anchor preflight'),
      ),
      WorkflowStepState(
        title: '2. Run 500-set Sweep',
        ready: _hasAnchor,
        done: runState.summaryExists,
        prerequisites: [
          _hasAnchor ? 'Master_Anchor detected' : 'Need Master_Anchor control port',
        ],
        outputs: [
          '$autopos/summary.json',
        ],
        failureHint: failureFor('AutoPos 500-set sweep'),
      ),
      WorkflowStepState(
        title: '3. Extract Pairs',
        ready: runState.summaryExists,
        done: runState.pairsExists,
        prerequisites: [
          runState.summaryExists
              ? 'Sweep summary present'
              : 'Need $autopos/summary.json',
        ],
        outputs: [
          '$autopos/pairs_all.csv',
        ],
        failureHint: failureFor('Extract AutoPos pairs'),
      ),
      WorkflowStepState(
        title: '4. Solve Layout',
        ready: runState.pairsExists,
        done: runState.solveExists && runState.candidateExists && candidate != null,
        prerequisites: [
          runState.pairsExists ? 'pairs_all.csv present' : 'Need $autopos/pairs_all.csv',
        ],
        outputs: [
          '$autopos/inter_anchor_free_solve.json',
          '$autopos/layout_candidate.json',
        ],
        failureHint: failureFor('Solve AutoPos layout'),
      ),
      WorkflowStepState(
        title: '5. Push APOS + Verify',
        ready: _hasTag && candidate != null && _armPush,
        done: runState.latestPushDir != null,
        prerequisites: [
          _hasTag ? 'Master_Tag detected' : 'Need Master_Tag control port',
          candidate != null ? 'Candidate layout loaded' : 'Need candidate layout',
          _armPush ? 'Push gate armed' : 'Arm push gate first',
        ],
        outputs: [
          runState.latestPushDir ?? '$base/apos_push_<timestamp>',
        ],
        failureHint: failureFor('Push APOS + verify'),
      ),
      WorkflowStepState(
        title: '6. 180s Validate',
        ready: _hasDistinctMasters,
        done: runState.validationExists,
        prerequisites: [
          _hasDistinctMasters
              ? 'Distinct Master_Anchor + Master_Tag ports'
              : 'Need distinct anchor/tag masters',
        ],
        outputs: [
          '$base/validation',
        ],
        failureHint: failureFor('180s validation capture'),
      ),
    ];
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<_AutopositioningSnapshot>(
      future: _future,
      builder: (context, snapshot) {
        final data = snapshot.data;
        final refSummary = data?.refSummary;
        final candidate = data?.candidate;
        final runState = data?.runState ?? const AutoPosRunState(baseOutDir: null);
        return RefreshIndicator(
          onRefresh: () async {
            await _scanPorts();
            setState(() {
              _future = _load();
            });
            await _future;
          },
          child: ListView(
            padding: const EdgeInsets.all(16),
            children: [
              _HeaderCard(
                anchorReady: _hasAnchor,
                tagReady: _hasTag,
                distinctReady: _hasDistinctMasters,
                onDetect: _scanPorts,
              ),
              const SizedBox(height: 16),
              _ControlPlanesCard(snapshot: _ports),
              const SizedBox(height: 16),
              _WorkflowCard(
                canRunAnchor: _hasAnchor,
                canRunDual: _hasDistinctMasters,
                canRunPush: _hasTag && candidate != null && _armPush,
                onPreflight: () =>
                    _run('Anchor preflight', _preflightCommand()),
                onSweep: () => _run('AutoPos 500-set sweep', _sweepCommand()),
                onExtract: () =>
                    _run('Extract AutoPos pairs', _extractPairsCommand()),
                onSolve: () =>
                    _run('Solve AutoPos layout', _solveLayoutCommand()),
                onPush: () => _run('Push APOS + verify', _pushAposCommand()),
                onValidate: () =>
                    _run('180s validation capture', _validationCommand()),
              ),
              const SizedBox(height: 16),
              _RunnerStatusCard(
                runner: ScriptRunner.instance,
                hasAnchor: _hasAnchor,
                hasTag: _hasTag,
                hasDistinctMasters: _hasDistinctMasters,
                hasCandidate: candidate != null,
                armPush: _armPush,
              ),
              const SizedBox(height: 16),
              _StepStatusCard(
                steps: _buildStepStates(
                  runner: ScriptRunner.instance,
                  runState: runState,
                  candidate: candidate,
                ),
              ),
              const SizedBox(height: 16),
              _RunnerCard(),
              const SizedBox(height: 16),
              _CandidateReviewCard(candidate: candidate),
              const SizedBox(height: 16),
              _AposPushCard(
                targetsController: _targetsController,
                armed: _armPush,
                enabled: _hasTag && candidate != null,
                onArmChanged: (value) {
                  setState(() {
                    _armPush = value ?? false;
                  });
                },
              ),
              const SizedBox(height: 16),
              _InfoCard(
                title: 'Current Reference Monitor',
                body: refSummary == null
                    ? 'No Ref115 summary loaded yet.'
                    : '${refSummary.formatMean()} | ${refSummary.formatResidual()}',
              ),
              const SizedBox(height: 16),
              _LayoutCard(
                  layout: data?.layout ?? const AnchorLayoutSnapshot([])),
              const SizedBox(height: 16),
              _RangesCard(lines: data?.rangesTail ?? const []),
            ],
          ),
        );
      },
    );
  }
}

class _AutopositioningSnapshot {
  final AnchorLayoutSnapshot layout;
  final List<String> rangesTail;
  final SessionSummary? refSummary;
  final LayoutCandidateSnapshot? candidate;
  final AutoPosRunState runState;

  _AutopositioningSnapshot({
    required this.layout,
    required this.rangesTail,
    required this.refSummary,
    required this.candidate,
    required this.runState,
  });
}

class AutoPosRunState {
  final String? baseOutDir;
  final String? autoposDir;
  final bool summaryExists;
  final bool pairsExists;
  final bool solveExists;
  final bool candidateExists;
  final bool validationExists;
  final String? latestPushDir;
  final String? latestPreflightDir;

  const AutoPosRunState({
    required this.baseOutDir,
    this.autoposDir,
    this.summaryExists = false,
    this.pairsExists = false,
    this.solveExists = false,
    this.candidateExists = false,
    this.validationExists = false,
    this.latestPushDir,
    this.latestPreflightDir,
  });
}

class WorkflowStepState {
  final String title;
  final bool ready;
  final bool done;
  final List<String> prerequisites;
  final List<String> outputs;
  final String? failureHint;

  const WorkflowStepState({
    required this.title,
    required this.ready,
    required this.done,
    required this.prerequisites,
    required this.outputs,
    required this.failureHint,
  });
}

class LayoutCandidateSnapshot {
  final String path;
  final String selectedResult;
  final String sourceFile;
  final Map<String, dynamic> stats;
  final List<AnchorLayoutEntry> anchors;

  const LayoutCandidateSnapshot({
    required this.path,
    required this.selectedResult,
    required this.sourceFile,
    required this.stats,
    required this.anchors,
  });

  String? _num(String key, {int digits = 1}) {
    final value = stats[key];
    if (value is num) {
      return value.toStringAsFixed(digits);
    }
    return null;
  }

  String summaryLine() {
    final rms = _num('rms_mm');
    final median = _num('median_abs_mm');
    final max = _num('max_abs_mm');
    return 'result=$selectedResult  rms=${rms ?? '-'} mm  median=${median ?? '-'} mm  max=${max ?? '-'} mm';
  }
}

class _HeaderCard extends StatelessWidget {
  final bool anchorReady;
  final bool tagReady;
  final bool distinctReady;
  final VoidCallback onDetect;

  const _HeaderCard({
    required this.anchorReady,
    required this.tagReady,
    required this.distinctReady,
    required this.onDetect,
  });

  @override
  Widget build(BuildContext context) {
    final color = distinctReady ? Colors.green : Colors.orange;
    final label = distinctReady
        ? 'Ready'
        : anchorReady || tagReady
            ? 'Partial'
            : 'Not detected';
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          children: [
            Icon(Icons.hub_outlined, color: color),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('AutoPos Control',
                      style:
                          TextStyle(fontSize: 20, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 4),
                  Text(
                      'Master_Anchor: ${anchorReady ? 'found' : 'missing'}  |  Master_Tag: ${tagReady ? 'found' : 'missing'}'),
                ],
              ),
            ),
            Chip(label: Text(label)),
            const SizedBox(width: 12),
            FilledButton.icon(
              onPressed: onDetect,
              icon: const Icon(Icons.search),
              label: const Text('Detect'),
            ),
          ],
        ),
      ),
    );
  }
}

class _ControlPlanesCard extends StatelessWidget {
  final DevicePortSnapshot snapshot;

  const _ControlPlanesCard({required this.snapshot});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Control Planes',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            const SizedBox(height: 12),
            _PortRow(title: 'Master_Anchor', port: snapshot.masterAnchor),
            const Divider(height: 20),
            _PortRow(title: 'Master_Tag', port: snapshot.masterTag),
            const Divider(height: 20),
            _PortRow(title: 'Listener', port: snapshot.listener),
            if (snapshot.ports.isNotEmpty) ...[
              const SizedBox(height: 12),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: snapshot.ports
                    .map((p) => Chip(
                        label:
                            Text('${p.role.label}: ${p.path.split('/').last}')))
                    .toList(),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _PortRow extends StatelessWidget {
  final String title;
  final DevicePortInfo? port;

  const _PortRow({required this.title, required this.port});

  @override
  Widget build(BuildContext context) {
    final found = port != null;
    return Row(
      children: [
        Icon(found ? Icons.check_circle : Icons.radio_button_unchecked,
            color: found ? Colors.green : Colors.grey),
        const SizedBox(width: 12),
        SizedBox(
            width: 140,
            child: Text(title,
                style: const TextStyle(fontWeight: FontWeight.w600))),
        Expanded(
          child: Text(
            found ? '${port!.path}  ->  ${port!.targetPath}' : 'Missing',
            style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }
}

class _WorkflowCard extends StatelessWidget {
  final bool canRunAnchor;
  final bool canRunDual;
  final bool canRunPush;
  final VoidCallback onPreflight;
  final VoidCallback onSweep;
  final VoidCallback onExtract;
  final VoidCallback onSolve;
  final VoidCallback onPush;
  final VoidCallback onValidate;

  const _WorkflowCard({
    required this.canRunAnchor,
    required this.canRunDual,
    required this.canRunPush,
    required this.onPreflight,
    required this.onSweep,
    required this.onExtract,
    required this.onSolve,
    required this.onPush,
    required this.onValidate,
  });

  @override
  Widget build(BuildContext context) {
    final running = ScriptRunner.instance.isRunning;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('AutoPos Workflow',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            const SizedBox(height: 12),
            Wrap(
              spacing: 10,
              runSpacing: 10,
              children: [
                _ActionButton(
                  icon: Icons.fact_check_outlined,
                  label: 'Preflight Anchors',
                  enabled: canRunAnchor && !running,
                  onPressed: onPreflight,
                ),
                _ActionButton(
                  icon: Icons.radar_outlined,
                  label: 'Run 500-set Sweep',
                  enabled: canRunAnchor && !running,
                  onPressed: onSweep,
                ),
                _ActionButton(
                  icon: Icons.table_chart_outlined,
                  label: 'Extract Pairs',
                  enabled: !running,
                  onPressed: onExtract,
                ),
                _ActionButton(
                  icon: Icons.auto_graph_outlined,
                  label: 'Solve Layout',
                  enabled: !running,
                  onPressed: onSolve,
                ),
                _ActionButton(
                  icon: Icons.publish_outlined,
                  label: 'Push APOS + Verify',
                  enabled: canRunPush && !running,
                  onPressed: onPush,
                ),
                _ActionButton(
                  icon: Icons.play_circle_outline,
                  label: '180s Validate',
                  enabled: canRunDual && !running,
                  onPressed: onValidate,
                ),
                OutlinedButton.icon(
                  onPressed: running ? ScriptRunner.instance.stop : null,
                  icon: const Icon(Icons.stop_circle_outlined),
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

class _ActionButton extends StatelessWidget {
  final IconData icon;
  final String label;
  final bool enabled;
  final VoidCallback onPressed;

  const _ActionButton({
    required this.icon,
    required this.label,
    required this.enabled,
    required this.onPressed,
  });

  @override
  Widget build(BuildContext context) {
    return FilledButton.tonalIcon(
      onPressed: enabled ? onPressed : null,
      icon: Icon(icon),
      label: Text(label),
    );
  }
}

class _StepStatusCard extends StatelessWidget {
  final List<WorkflowStepState> steps;

  const _StepStatusCard({required this.steps});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Step Readiness',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 12),
            ...steps.map(
              (step) => Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: _StepTile(step: step),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _StepTile extends StatelessWidget {
  final WorkflowStepState step;

  const _StepTile({required this.step});

  @override
  Widget build(BuildContext context) {
    final Color statusColor = step.done
        ? Colors.green
        : step.ready
            ? Colors.orange
            : Colors.grey;

    final statusText = step.done
        ? 'Done'
        : step.ready
            ? 'Ready'
            : 'Blocked';

    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        border: Border.all(color: Colors.grey.shade300),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.circle, size: 12, color: statusColor),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  step.title,
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
              ),
              Chip(label: Text(statusText)),
            ],
          ),
          const SizedBox(height: 8),
          const Text(
            'Prerequisites',
            style: TextStyle(fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 4),
          ...step.prerequisites.map((line) => Text('• $line')),
          const SizedBox(height: 8),
          const Text(
            'Expected outputs',
            style: TextStyle(fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 4),
          ...step.outputs.map(
            (line) => Text(
              '• $line',
              style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
            ),
          ),
          if (step.failureHint != null) ...[
            const SizedBox(height: 8),
            Text(
              'Last failure: ${step.failureHint}',
              style: TextStyle(
                color: Colors.red.shade700,
                fontFamily: 'monospace',
                fontSize: 12,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _RunnerCard extends StatelessWidget {
  const _RunnerCard();

  @override
  Widget build(BuildContext context) {
    final runner = ScriptRunner.instance;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(runner.isRunning ? Icons.sync : Icons.terminal),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    runner.isRunning
                        ? 'Running: ${runner.activeName}'
                        : 'Command Runner',
                    style: const TextStyle(
                        fontSize: 18, fontWeight: FontWeight.w600),
                  ),
                ),
              ],
            ),
            if (runner.activeCommand != null) ...[
              const SizedBox(height: 8),
              Text(runner.activeCommand!,
                  style:
                      const TextStyle(fontFamily: 'monospace', fontSize: 12)),
            ],
            const SizedBox(height: 12),
            Container(
              width: double.infinity,
              constraints: const BoxConstraints(minHeight: 120, maxHeight: 280),
              padding: const EdgeInsets.all(12),
              color: Colors.black87,
              child: SingleChildScrollView(
                reverse: true,
                child: Text(
                  runner.logTail.isEmpty
                      ? 'No command output yet.'
                      : runner.logTail.join('\n'),
                  style: const TextStyle(
                      fontFamily: 'monospace',
                      color: Colors.white,
                      fontSize: 12),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _RunnerStatusCard extends StatelessWidget {
  final ScriptRunner runner;
  final bool hasAnchor;
  final bool hasTag;
  final bool hasDistinctMasters;
  final bool hasCandidate;
  final bool armPush;

  const _RunnerStatusCard({
    required this.runner,
    required this.hasAnchor,
    required this.hasTag,
    required this.hasDistinctMasters,
    required this.hasCandidate,
    required this.armPush,
  });

  @override
  Widget build(BuildContext context) {
    final steps = <String>[
      hasAnchor ? 'Anchor control OK' : 'Anchor control missing',
      hasTag ? 'Tag control OK' : 'Tag control missing',
      hasDistinctMasters ? 'Dual-master split OK' : 'Masters not distinct yet',
      hasCandidate ? 'Candidate layout ready' : 'No candidate layout yet',
      armPush ? 'APOS push armed' : 'APOS push not armed',
    ];
    final status = runner.isRunning
        ? 'Running: ${runner.activeName}'
        : runner.lastFinishedName != null
            ? (runner.lastExitCode == 0
                ? 'Last result: ${runner.lastFinishedName} OK'
                : 'Last result: ${runner.lastFinishedName} failed (${runner.lastExitCode})')
            : 'Idle';

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Workflow Status',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 8),
            Text(status),
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: steps.map((step) => Chip(label: Text(step))).toList(),
            ),
          ],
        ),
      ),
    );
  }
}

class _CandidateReviewCard extends StatelessWidget {
  final LayoutCandidateSnapshot? candidate;

  const _CandidateReviewCard({required this.candidate});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Candidate Layout Review',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 8),
            if (candidate == null) ...[
              const Text('No candidate layout yet. Run Solve Layout first.'),
            ] else ...[
              Text(candidate!.summaryLine()),
              const SizedBox(height: 6),
              Text(
                candidate!.path,
                style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
              ),
              const SizedBox(height: 6),
              Text(
                'source=${candidate!.sourceFile}',
                style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
              ),
              const SizedBox(height: 10),
              Wrap(
                spacing: 10,
                runSpacing: 8,
                children: candidate!.anchors
                    .map(
                      (a) => Chip(
                        label: Text('${a.name}: (${a.x}, ${a.y}, ${a.z})'),
                      ),
                    )
                    .toList(),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _AposPushCard extends StatelessWidget {
  final TextEditingController targetsController;
  final bool armed;
  final bool enabled;
  final ValueChanged<bool?> onArmChanged;

  const _AposPushCard({
    required this.targetsController,
    required this.armed,
    required this.enabled,
    required this.onArmChanged,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'APOS Push Gate',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: targetsController,
              enabled: enabled,
              decoration: const InputDecoration(
                labelText: 'Targets',
                hintText: 'BSF66F,BS2DCE,BSDC91',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 10),
            CheckboxListTile(
              value: armed,
              onChanged: enabled ? onArmChanged : null,
              contentPadding: EdgeInsets.zero,
              title: const Text('Arm APOS push to Tag NVS'),
              subtitle: const Text(
                'Push stays disabled until a candidate layout exists and this gate is armed.',
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _InfoCard extends StatelessWidget {
  final String title;
  final String body;

  const _InfoCard({required this.title, required this.body});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title,
                style:
                    const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            Text(body),
          ],
        ),
      ),
    );
  }
}

class _LayoutCard extends StatelessWidget {
  final AnchorLayoutSnapshot layout;

  const _LayoutCard({required this.layout});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Runtime Anchor Layout',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            if (layout.anchors.isEmpty)
              const Text('No runtime layout loaded yet.')
            else
              Wrap(
                spacing: 12,
                runSpacing: 8,
                children: layout.anchors
                    .map(
                      (a) => Chip(
                        label: Text('${a.name}: (${a.x}, ${a.y}, ${a.z})'),
                      ),
                    )
                    .toList(),
              ),
          ],
        ),
      ),
    );
  }
}

class _RangesCard extends StatelessWidget {
  final List<String> lines;

  const _RangesCard({required this.lines});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Latest Ref115 Ranges',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            if (lines.isEmpty)
              const Text('No ranges.csv data found.')
            else
              ...lines.map(
                (line) => Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Text(line,
                      style: const TextStyle(
                          fontFamily: 'monospace', fontSize: 12)),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

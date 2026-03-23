import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';

import '../shared/models/log_models.dart';
import '../shared/services/live_feed_runner.dart';
import '../shared/services/log_repository.dart';
import '../shared/widgets/simple_series_chart.dart';

class LiveViewPage extends StatefulWidget {
  const LiveViewPage({super.key});

  @override
  State<LiveViewPage> createState() => _LiveViewPageState();
}

class _LiveViewPageState extends State<LiveViewPage> {
  late final Timer _timer;
  late final LiveFeedRunner _liveFeed;
  StreamSubscription<void>? _liveFeedSubscription;
  late Future<_LiveViewSnapshot> _future;

  @override
  void initState() {
    super.initState();
    _liveFeed = LiveFeedRunner.instance;
    _future = _load();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) {
        setState(() {
          _future = _load();
        });
      }
    });
    _liveFeedSubscription = _liveFeed.changes.listen((_) {
      if (mounted && _liveFeed.isRunning) {
        setState(() {
          _future = _load();
        });
      }
    });
  }

  @override
  void dispose() {
    _timer.cancel();
    _liveFeedSubscription?.cancel();
    super.dispose();
  }

  Future<_LiveViewSnapshot> _load() async {
    if (_liveFeed.isRunning) {
      return _loadFromLiveFeed();
    }
    return _loadFromFiles();
  }

  Future<_LiveViewSnapshot> _loadFromFiles() async {
    final repo = LogRepository.instance;
    final refPath = await repo.latestRef115ActiveRawPath();
    final motionPath = await repo.latestMasterBleActiveRawPath();
    final refSummary = await repo.latestRef115Summary();
    final motionSummary = await repo.latestMasterBleSummary();
    final refSource = await _loadSource(refPath);
    final motionSource = await _loadSource(motionPath);
    return _LiveViewSnapshot(
      refSummary: refSummary,
      motionSummary: motionSummary,
      refSource: refSource,
      motionSource: motionSource,
      refreshedAt: DateTime.now(),
      mode: 'file-backed',
    );
  }

  Future<_LiveViewSnapshot> _loadFromLiveFeed() async {
    final repo = LogRepository.instance;
    final lines = _liveFeed.logTail;
    final refLines = lines.where((line) => line.startsWith('[ref] ')).map((line) => line.substring(6)).toList();
    final motionLines = lines.where((line) => line.startsWith('[motion] ')).map((line) => line.substring(9)).toList();
    final refSamples = repo.parsePositionSamplesFromLines(refLines, limit: 120);
    final motionSamples = repo.parsePositionSamplesFromLines(motionLines, limit: 120);
    final refTail = _tailText(refLines, 40);
    final motionTail = _tailText(motionLines, 40);
    return _LiveViewSnapshot(
      refSummary: null,
      motionSummary: null,
      refSource: _LiveSourceSnapshot(
        path: 'direct live feed (ref115)',
        modified: DateTime.now(),
        tail: refTail,
        samples: refSamples,
      ),
      motionSource: _LiveSourceSnapshot(
        path: 'direct live feed (motion tag)',
        modified: DateTime.now(),
        tail: motionTail,
        samples: motionSamples,
      ),
      refreshedAt: DateTime.now(),
      mode: 'direct-serial',
    );
  }

  String _tailText(List<String> lines, int maxLines) {
    if (lines.isEmpty) return '';
    final tail = lines.length <= maxLines ? lines : lines.sublist(lines.length - maxLines);
    return tail.join('\n');
  }

  Future<void> _startDirectLiveFeed() async {
    if (_liveFeed.isRunning) return;
    await _liveFeed.start(
      'Direct serial live feed',
      'python3 scripts/tail_live_serials.py '
          '--ref-port /dev/serial/by-id/usb-SEGGER_J-Link_000760186115-if00 '
          '--motion-port /dev/serial/by-id/usb-SEGGER_J-Link_000683234364-if00',
    );
    if (mounted) {
      setState(() {
        _future = _load();
      });
    }
  }

  Future<void> _stopDirectLiveFeed() async {
    await _liveFeed.stop();
    if (mounted) {
      setState(() {
        _future = _load();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<_LiveViewSnapshot>(
      future: _future,
      builder: (context, snapshot) {
        final data = snapshot.data;
        return RefreshIndicator(
          onRefresh: () async {
            setState(() {
              _future = _load();
            });
            await _future;
          },
          child: ListView(
            padding: const EdgeInsets.all(16),
            children: [
              _LiveControlCard(
                isRunning: _liveFeed.isRunning,
                activeName: _liveFeed.activeName,
                activeCommand: _liveFeed.activeCommand,
                onStart: _startDirectLiveFeed,
                onStop: _stopDirectLiveFeed,
                mode: data?.mode ?? (_liveFeed.isRunning ? 'direct-serial' : 'file-backed'),
              ),
              const SizedBox(height: 16),
              _LiveSummaryCard(
                title: 'Ref115 Live',
                summary: data?.refSummary,
                source: data?.refSource,
              ),
              const SizedBox(height: 16),
              _LiveSummaryCard(
                title: 'Rotation Tag Live',
                summary: data?.motionSummary,
                source: data?.motionSource,
              ),
            ],
          ),
        );
      },
    );
  }
}

class _LiveViewSnapshot {
  final SessionSummary? refSummary;
  final SessionSummary? motionSummary;
  final _LiveSourceSnapshot? refSource;
  final _LiveSourceSnapshot? motionSource;
  final DateTime refreshedAt;
  final String mode;

  _LiveViewSnapshot({
    required this.refSummary,
    required this.motionSummary,
    required this.refSource,
    required this.motionSource,
    required this.refreshedAt,
    required this.mode,
  });
}

class _LiveControlCard extends StatelessWidget {
  final bool isRunning;
  final String? activeName;
  final String? activeCommand;
  final String mode;
  final VoidCallback onStart;
  final VoidCallback onStop;

  const _LiveControlCard({
    required this.isRunning,
    required this.activeName,
    required this.activeCommand,
    required this.mode,
    required this.onStart,
    required this.onStop,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Live Source', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            Text('Mode: $mode'),
            Text(isRunning ? 'Direct feed: ${activeName ?? 'running'}' : 'Direct feed: stopped'),
            if (activeCommand != null) ...[
              const SizedBox(height: 4),
              Text(activeCommand!, style: const TextStyle(fontSize: 12)),
            ],
            const SizedBox(height: 12),
            Wrap(
              spacing: 12,
              runSpacing: 8,
              children: [
                FilledButton(
                  onPressed: isRunning ? null : onStart,
                  child: const Text('Start Direct Live'),
                ),
                OutlinedButton(
                  onPressed: isRunning ? onStop : null,
                  child: const Text('Stop Direct Live'),
                ),
              ],
            ),
            const SizedBox(height: 8),
            const Text(
              'Direct live reads serial output directly. Recording logs is optional.',
              style: TextStyle(fontSize: 12, color: Colors.black54),
            ),
          ],
        ),
      ),
    );
  }
}

class _LiveSourceSnapshot {
  final String path;
  final DateTime modified;
  final String tail;
  final List<PositionSample> samples;

  const _LiveSourceSnapshot({
    required this.path,
    required this.modified,
    required this.tail,
    required this.samples,
  });
}

class _LiveSummaryCard extends StatelessWidget {
  final String title;
  final SessionSummary? summary;
  final _LiveSourceSnapshot? source;

  const _LiveSummaryCard({
    required this.title,
    required this.summary,
    required this.source,
  });

  @override
  Widget build(BuildContext context) {
    final source = this.source;
    final samples = source?.samples ?? const <PositionSample>[];
    final xValues = samples.map((s) => s.xMm.toDouble()).toList();
    final yValues = samples.map((s) => s.yMm.toDouble()).toList();
    final zValues = samples.map((s) => s.zMm.toDouble()).toList();
    final rmsValues = samples.map((s) => s.rmsMm.toDouble()).toList();
    final maxValues = samples.map((s) => s.maxMm.toDouble()).toList();
    final age = source == null ? null : DateTime.now().difference(source.modified);
    final liveStatus = _liveStatus(age);
    final latestSweep = samples.isEmpty ? null : samples.last.sweep;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            Text(summary == null ? 'No summary yet.' : summary!.formatResidual()),
            const SizedBox(height: 4),
            Text(
              source == null
                  ? 'Source: none'
                  : 'Source: ${source.path}\nUpdated: ${_formatAge(age)} ago',
              style: const TextStyle(fontSize: 12, color: Colors.black54),
            ),
            if (source != null) ...[
              const SizedBox(height: 4),
              Text(
                'Status: $liveStatus | samples=${samples.length}${latestSweep == null ? '' : ' | latest sweep=$latestSweep'}',
                style: const TextStyle(fontSize: 12, color: Colors.black54),
              ),
            ],
            const SizedBox(height: 12),
            SimpleSeriesChart(
              title: 'XYZ',
              series: [
                SeriesLine(label: 'X', color: Colors.blue, values: xValues),
                SeriesLine(label: 'Y', color: Colors.green, values: yValues),
                SeriesLine(label: 'Z', color: Colors.red, values: zValues),
              ],
            ),
            const SizedBox(height: 12),
            SimpleSeriesChart(
              title: 'RMS / Max',
              series: [
                SeriesLine(label: 'RMS', color: Colors.orange, values: rmsValues),
                SeriesLine(label: 'Max', color: Colors.purple, values: maxValues),
              ],
            ),
            const SizedBox(height: 12),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: Theme.of(context).colorScheme.surfaceContainerHighest,
                borderRadius: BorderRadius.circular(12),
              ),
              child: SelectableText(
                source == null || source.tail.isEmpty ? 'No raw log tail available yet.' : source.tail,
                style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
              ),
            ),
          ],
        ),
      ),
    );
  }

  String _formatAge(Duration? age) {
    if (age == null) return 'n/a';
    final seconds = age.inSeconds.abs();
    if (seconds < 60) return '${seconds}s';
    final minutes = seconds ~/ 60;
    final rest = seconds % 60;
    return '${minutes}m ${rest}s';
  }

  String _liveStatus(Duration? age) {
    if (age == null) return 'unknown';
    if (age.inSeconds <= 2) return 'live';
    if (age.inSeconds <= 10) return 'stale';
    return 'old';
  }
}

Future<_LiveSourceSnapshot?> _loadSource(String? path) async {
  if (path == null) return null;
  final file = File(path);
  if (!file.existsSync()) return null;
  final stat = await file.stat();
  final tail = await LogRepository.instance.readTail(path, maxLines: 60);
  final samples = await LogRepository.instance.parsePositionSamplesFromRaw(path, limit: 80);
  return _LiveSourceSnapshot(
    path: path,
    modified: stat.modified,
    tail: tail,
    samples: samples,
  );
}

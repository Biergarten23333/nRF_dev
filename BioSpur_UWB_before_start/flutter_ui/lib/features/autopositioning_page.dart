import 'dart:async';

import 'package:flutter/material.dart';

import '../shared/models/log_models.dart';
import '../shared/services/log_repository.dart';

class AutopositioningPage extends StatefulWidget {
  const AutopositioningPage({super.key});

  @override
  State<AutopositioningPage> createState() => _AutopositioningPageState();
}

class _AutopositioningPageState extends State<AutopositioningPage> {
  late final Timer _timer;
  late Future<_AutopositioningSnapshot> _future;

  @override
  void initState() {
    super.initState();
    _future = _load();
    _timer = Timer.periodic(const Duration(seconds: 5), (_) {
      if (mounted) {
        setState(() {
          _future = _load();
        });
      }
    });
  }

  @override
  void dispose() {
    _timer.cancel();
    super.dispose();
  }

  Future<_AutopositioningSnapshot> _load() async {
    final repo = LogRepository.instance;
    return _AutopositioningSnapshot(
      layout: await repo.loadRuntimeAnchorLayout(),
      rangesTail: await repo.latestRef115Ranges(limit: 12),
      refSummary: await repo.latestRef115Summary(),
    );
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<_AutopositioningSnapshot>(
      future: _future,
      builder: (context, snapshot) {
        final data = snapshot.data;
        final refSummary = data?.refSummary;
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
              _InfoCard(
                title: 'Ref115 Autopositioning',
                body: 'Calibration captures `Tag115 -> Anchor` ranges, solves the '
                    'anchor layout, then rebuilds the runtime anchor table.',
              ),
              const SizedBox(height: 16),
              _InfoCard(
                title: 'Current Reference Monitor',
                body: refSummary == null
                    ? 'No Ref115 summary loaded yet.'
                    : '${refSummary.formatMean()} | ${refSummary.formatResidual()}',
              ),
              const SizedBox(height: 16),
              _LayoutCard(layout: data?.layout ?? const AnchorLayoutSnapshot([])),
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

  _AutopositioningSnapshot({
    required this.layout,
    required this.rangesTail,
    required this.refSummary,
  });
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
            Text(title, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
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
            const Text('Runtime Anchor Layout', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
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
            const Text('Latest Ref115 Ranges', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            if (lines.isEmpty)
              const Text('No ranges.csv data found.')
            else
              ...lines.map(
                (line) => Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Text(line, style: const TextStyle(fontFamily: 'monospace', fontSize: 12)),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

import 'dart:async';

import 'package:flutter/material.dart';

import '../shared/models/log_models.dart';
import '../shared/services/log_repository.dart';

class DashboardPage extends StatefulWidget {
  const DashboardPage({super.key});

  @override
  State<DashboardPage> createState() => _DashboardPageState();
}

class _DashboardPageState extends State<DashboardPage> {
  late final Timer _timer;
  late Future<_DashboardSnapshot> _future;

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

  Future<_DashboardSnapshot> _load() async {
    final repo = LogRepository.instance;
    final refSummary = await repo.latestRef115Summary();
    final motionSummary = await repo.latestMasterBleSummary();
    final recentTag = await repo.recentTagSessions(limit: 5);
    final anchorLayout = await repo.loadRuntimeAnchorLayout();
    return _DashboardSnapshot(
      refSummary: refSummary,
      motionSummary: motionSummary,
      recentTagSessions: recentTag,
      anchorLayout: anchorLayout,
    );
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<_DashboardSnapshot>(
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
              _SystemHeader(
                refSummary: data?.refSummary,
                motionSummary: data?.motionSummary,
              ),
              const SizedBox(height: 16),
              _DeviceGrid(
                refSummary: data?.refSummary,
                motionSummary: data?.motionSummary,
              ),
              const SizedBox(height: 16),
              _AnchorLayoutCard(layout: data?.anchorLayout ?? const AnchorLayoutSnapshot([])),
              const SizedBox(height: 16),
              _RecentSessionsCard(sessions: data?.recentTagSessions ?? const []),
            ],
          ),
        );
      },
    );
  }
}

class _DashboardSnapshot {
  final SessionSummary? refSummary;
  final SessionSummary? motionSummary;
  final List<SessionEntry> recentTagSessions;
  final AnchorLayoutSnapshot anchorLayout;

  _DashboardSnapshot({
    required this.refSummary,
    required this.motionSummary,
    required this.recentTagSessions,
    required this.anchorLayout,
  });
}

class _SystemHeader extends StatelessWidget {
  final SessionSummary? refSummary;
  final SessionSummary? motionSummary;

  const _SystemHeader({
    required this.refSummary,
    required this.motionSummary,
  });

  @override
  Widget build(BuildContext context) {
    final ref = refSummary;
    final motion = motionSummary;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('System Status', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            Text(
              'Ref115: ${ref == null ? 'no summary yet' : '${ref.formatMean()} | ${ref.formatResidual()}'}',
            ),
            const SizedBox(height: 4),
            Text(
              'Motion BLE: ${motion == null ? 'no summary yet' : '${motion.formatMean()} | ${motion.formatResidual()}'}',
            ),
            const SizedBox(height: 4),
            Text('Reference monitor subset: B,D,F,G'),
          ],
        ),
      ),
    );
  }
}

class _DeviceGrid extends StatelessWidget {
  final SessionSummary? refSummary;
  final SessionSummary? motionSummary;

  const _DeviceGrid({
    required this.refSummary,
    required this.motionSummary,
  });

  @override
  Widget build(BuildContext context) {
    final ref = refSummary;
    final motion = motionSummary;
    return LayoutBuilder(
      builder: (context, constraints) {
        final columns = constraints.maxWidth >= 900 ? 3 : constraints.maxWidth >= 600 ? 2 : 1;
        return GridView.count(
          crossAxisCount: columns,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          crossAxisSpacing: 12,
          mainAxisSpacing: 12,
          childAspectRatio: 2.25,
          children: [
            _MiniDeviceCard(
              title: 'Ref115',
              subtitle: ref == null
                  ? 'waiting for live summary'
                  : 'xyz ${ref.formatMean()} | ${ref.formatStd()}',
            ),
            _MiniDeviceCard(
              title: 'Rotation Tag',
              subtitle: motion == null
                  ? 'waiting for BLE motion data'
                  : 'xyz ${motion.formatMean()} | ${motion.formatResidual()}',
            ),
            const _MiniDeviceCard(
              title: '52840 Master',
              subtitle: 'BLE receiver / command plane active',
            ),
          ],
        );
      },
    );
  }
}

class _MiniDeviceCard extends StatelessWidget {
  final String title;
  final String subtitle;

  const _MiniDeviceCard({
    required this.title,
    required this.subtitle,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
            const SizedBox(height: 6),
            Text(subtitle),
          ],
        ),
      ),
    );
  }
}

class _AnchorLayoutCard extends StatelessWidget {
  final AnchorLayoutSnapshot layout;

  const _AnchorLayoutCard({required this.layout});

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

class _RecentSessionsCard extends StatelessWidget {
  final List<SessionEntry> sessions;

  const _RecentSessionsCard({required this.sessions});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Recent Tag Sessions', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            if (sessions.isEmpty)
              const Text('No tag sessions found.')
            else
              ...sessions.take(5).map(
                    (session) => Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Text(
                        '${session.name} | ${session.summary?.formatResidual() ?? 'no summary'}',
                      ),
                    ),
                  ),
          ],
        ),
      ),
    );
  }
}

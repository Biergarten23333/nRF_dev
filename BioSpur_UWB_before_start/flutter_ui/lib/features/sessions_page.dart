import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../shared/models/log_models.dart';
import '../shared/services/log_repository.dart';
import '../shared/services/script_runner.dart';

class SessionsPage extends StatefulWidget {
  const SessionsPage({super.key});

  @override
  State<SessionsPage> createState() => _SessionsPageState();
}

class _SessionsPageState extends State<SessionsPage> {
  late final Timer _timer;
  late final ScriptRunner _runner;
  late Future<_SessionsSnapshot> _future;

  @override
  void initState() {
    super.initState();
    _runner = ScriptRunner.instance;
    _future = _load();
    _timer = Timer.periodic(const Duration(seconds: 10), (_) {
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

  Future<_SessionsSnapshot> _load() async {
    final repo = LogRepository.instance;
    return _SessionsSnapshot(
      recentTagSessions: await repo.recentTagSessions(limit: 6),
      recentMasterSessions: await repo.recentMasterBleSessions(limit: 6),
    );
  }

  Future<void> _startRefCapture() async {
    final cmd = [
      'python3 scripts/capture_tag_session.py',
      '760186115',
      '/dev/serial/by-id/usb-SEGGER_J-Link_000760186115-if00',
      '--duration 1200',
      '--skip-sweeps 2',
      r'--session-name flutter_ref115_capture_$(date +%Y%m%d_%H%M%S)',
    ].join(' ');
    await _runner.start('Ref115 capture', cmd);
    setState(() {});
  }

  Future<void> _startMotionCapture() async {
    final cmd = [
      'python3 scripts/capture_master_ble_session.py',
      '683234364',
      '/dev/serial/by-id/usb-SEGGER_J-Link_000683234364-if00',
      '--duration 1200',
      '--no-reset',
      r'--session-name flutter_motion_ble_capture_$(date +%Y%m%d_%H%M%S)',
    ].join(' ');
    await _runner.start('Motion BLE capture', cmd);
    setState(() {});
  }

  Future<void> _stopActive() async {
    await _runner.stop();
    setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    return StreamBuilder<void>(
      stream: _runner.changes,
      builder: (context, _) {
        return FutureBuilder<_SessionsSnapshot>(
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
                  _ControlCard(
                    activeName: _runner.activeName,
                    activeCommand: _runner.activeCommand,
                    logTail: _runner.logTail,
                    isRunning: _runner.isRunning,
                    onStartRefCapture: _startRefCapture,
                    onStartMotionCapture: _startMotionCapture,
                    onStop: _stopActive,
                  ),
                  const SizedBox(height: 16),
                  _SessionListCard(
                    title: 'Recent Tag Sessions',
                    sessions: data?.recentTagSessions ?? const [],
                  ),
                  const SizedBox(height: 16),
                  _SessionListCard(
                    title: 'Recent BLE Master Sessions',
                    sessions: data?.recentMasterSessions ?? const [],
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

class _SessionsSnapshot {
  final List<SessionEntry> recentTagSessions;
  final List<SessionEntry> recentMasterSessions;

  _SessionsSnapshot({
    required this.recentTagSessions,
    required this.recentMasterSessions,
  });
}

class _ControlCard extends StatelessWidget {
  final bool isRunning;
  final String? activeName;
  final String? activeCommand;
  final List<String> logTail;
  final VoidCallback onStartRefCapture;
  final VoidCallback onStartMotionCapture;
  final VoidCallback onStop;

  const _ControlCard({
    required this.isRunning,
    required this.activeName,
    required this.activeCommand,
    required this.logTail,
    required this.onStartRefCapture,
    required this.onStartMotionCapture,
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
            const Text('Capture Control', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            Text(isRunning ? 'Running: ${activeName ?? 'unknown'}' : 'No active script'),
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
                  onPressed: isRunning ? null : onStartRefCapture,
                  child: const Text('Start Ref115 Capture'),
                ),
                FilledButton(
                  onPressed: isRunning ? null : onStartMotionCapture,
                  child: const Text('Start Motion Capture'),
                ),
                OutlinedButton(
                  onPressed: isRunning ? onStop : null,
                  child: const Text('Stop Active'),
                ),
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
                logTail.isEmpty ? 'No script output yet.' : logTail.join('\n'),
                style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _SessionListCard extends StatelessWidget {
  final String title;
  final List<SessionEntry> sessions;

  const _SessionListCard({
    required this.title,
    required this.sessions,
  });

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
            if (sessions.isEmpty)
              const Text('No sessions found.')
            else
              ...sessions.map((session) => _SessionRow(session: session)),
          ],
        ),
      ),
    );
  }
}

class _SessionRow extends StatelessWidget {
  final SessionEntry session;

  const _SessionRow({required this.session});

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Theme.of(context).colorScheme.outlineVariant),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  session.name,
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
              ),
              IconButton(
                tooltip: 'Copy session path',
                onPressed: () => Clipboard.setData(ClipboardData(text: session.path)),
                icon: const Icon(Icons.copy, size: 18),
              ),
            ],
          ),
          Text(session.path, style: const TextStyle(fontSize: 12)),
          const SizedBox(height: 4),
          Text(session.summary == null
              ? 'No summary.json'
              : '${session.summary!.formatResidual()} | ${session.summary!.formatMean()}'),
        ],
      ),
    );
  }
}

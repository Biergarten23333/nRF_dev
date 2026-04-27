import 'dart:convert';
import 'dart:io';

import '../models/log_models.dart';
import 'repo_paths.dart';

class LogRepository {
  static final LogRepository instance = LogRepository._();

  LogRepository._();

  Future<List<SessionEntry>> recentTagSessions({int limit = 8}) async {
    return _recentSessions(RepoPaths.tagSessionsDir.path, limit: limit);
  }

  Future<List<SessionEntry>> recentMasterBleSessions({int limit = 8}) async {
    return _recentSessions(RepoPaths.masterBleSessionsDir.path, limit: limit);
  }

  Future<SessionSummary?> latestTagSummary() async {
    return _latestSummary(RepoPaths.tagSessionsDir.path);
  }

  Future<SessionSummary?> latestRef115Summary() async {
    return _latestSummaryMatching(
      RepoPaths.tagSessionsDir.path,
      (path) => _looksLikeRef115(path),
    );
  }

  Future<SessionSummary?> latestMasterBleSummary() async {
    return _latestSummary(RepoPaths.masterBleSessionsDir.path);
  }

  Future<String> readTail(String filePath, {int maxLines = 40}) async {
    final file = File(filePath);
    if (!file.existsSync()) return '';
    final lines = await file.readAsLines();
    final tail = lines.length <= maxLines ? lines : lines.sublist(lines.length - maxLines);
    return tail.join('\n');
  }

  Future<List<PositionSample>> parsePositionSamplesFromRaw(
    String filePath, {
    int limit = 100,
  }) async {
    final file = File(filePath);
    if (!file.existsSync()) return const [];
    final lines = await file.readAsLines();
    return parsePositionSamplesFromLines(lines, limit: limit);
  }

  List<PositionSample> parsePositionSamplesFromLines(
    Iterable<String> lines, {
    int limit = 100,
  }) {
    final samples = <PositionSample>[];
    for (final line in lines) {
      final match = _positionRegex.firstMatch(_stripLivePrefix(line));
      if (match == null) continue;
      samples.add(
        PositionSample(
          sweep: int.tryParse(match.group(1) ?? '') ?? samples.length,
          xMm: int.tryParse(match.group(2) ?? '') ?? 0,
          yMm: int.tryParse(match.group(3) ?? '') ?? 0,
          zMm: int.tryParse(match.group(4) ?? '') ?? 0,
          rmsMm: int.tryParse(match.group(5) ?? '') ?? 0,
          maxMm: int.tryParse(match.group(6) ?? '') ?? 0,
          anchors: match.group(7) ?? '',
        ),
      );
    }
    if (samples.length <= limit) return samples;
    return samples.sublist(samples.length - limit);
  }

  Future<AnchorLayoutSnapshot> loadRuntimeAnchorLayout() async {
    final file = File('${RepoPaths.dataDir.path}/anchor_layout_ah_runtime.json');
    if (!file.existsSync()) return const AnchorLayoutSnapshot([]);
    final decoded = jsonDecode(await file.readAsString()) as Map<String, dynamic>;
    final anchors = <AnchorLayoutEntry>[];
    final list = decoded['anchors'] as List<dynamic>? ?? const [];
    for (final item in list) {
      if (item is! Map<String, dynamic>) continue;
      anchors.add(AnchorLayoutEntry(
        id: item['id']?.toString() ?? '?',
        name: item['name']?.toString() ?? '?',
        x: (item['x'] as num?)?.toInt() ?? 0,
        y: (item['y'] as num?)?.toInt() ?? 0,
        z: (item['z'] as num?)?.toInt() ?? 0,
      ));
    }
    return AnchorLayoutSnapshot(anchors);
  }

  Future<List<String>> latestRef115Ranges({int limit = 12}) async {
    final sessions = await _recentSessionsMatching(
      RepoPaths.tagSessionsDir.path,
      (path) => _looksLikeRef115RangeSession(path),
      limit: 1,
    );
    if (sessions.isEmpty) return const [];
    final file = File('${sessions.first.path}/ranges.csv');
    if (!file.existsSync()) return const [];
    final lines = await file.readAsLines();
    if (lines.length <= 1) return const [];
    final tail = lines.sublist(1);
    return tail.length <= limit ? tail : tail.sublist(tail.length - limit);
  }

  Future<String?> latestTagSessionRawPath() async {
    final sessions = await _recentSessions(RepoPaths.tagSessionsDir.path, limit: 1);
    if (sessions.isEmpty) return null;
    return '${sessions.first.path}/raw.log';
  }

  Future<String?> latestRef115RawPath() async {
    final sessions = await _recentSessionsMatching(
      RepoPaths.tagSessionsDir.path,
      (path) => _looksLikeRef115(path),
      limit: 1,
    );
    if (sessions.isEmpty) return null;
    return '${sessions.first.path}/raw.log';
  }

  Future<String?> latestRef115ActiveRawPath() async {
    return _latestActiveRawPathMatching(
      RepoPaths.tagSessionsDir.path,
      (path) => _looksLikeRef115(path),
    );
  }

  Future<String?> latestMasterBleRawPath() async {
    final sessions = await _recentSessions(RepoPaths.masterBleSessionsDir.path, limit: 1);
    if (sessions.isEmpty) return null;
    return '${sessions.first.path}/raw.log';
  }

  Future<String?> latestMasterBleActiveRawPath() async {
    return _latestActiveRawPathMatching(
      RepoPaths.masterBleSessionsDir.path,
      (_) => true,
    );
  }

  Future<List<SessionEntry>> _recentSessions(String baseDir, {int limit = 8}) async {
    return _recentSessionsMatching(baseDir, (_) => true, limit: limit);
  }

  Future<List<SessionEntry>> _recentSessionsMatching(
    String baseDir,
    bool Function(String path) predicate, {
    int limit = 8,
  }) async {
    final root = Directory(baseDir);
    if (!root.existsSync()) return const [];
    final dirs = root
        .listSync()
        .whereType<Directory>()
        .where((dir) => predicate(dir.path))
        .map((dir) => _sessionEntryFor(dir))
        .whereType<SessionEntry>()
        .toList()
      ..sort((a, b) => b.modified.compareTo(a.modified));
    return dirs.take(limit).toList();
  }

  SessionEntry? _sessionEntryFor(Directory dir) {
    final summaryFile = File('${dir.path}/summary.json');
    SessionSummary? summary;
    if (summaryFile.existsSync()) {
      try {
        summary = SessionSummary.fromJsonFile(dir.path, summaryFile.readAsStringSync());
      } catch (_) {
        summary = null;
      }
    }
    return SessionEntry(
      path: dir.path,
      modified: dir.statSync().modified,
      summary: summary,
    );
  }

  Future<SessionSummary?> _latestSummary(String baseDir) async {
    final sessions = await _recentSessions(baseDir, limit: 1);
    if (sessions.isEmpty) return null;
    return sessions.first.summary;
  }

  Future<SessionSummary?> _latestSummaryMatching(
    String baseDir,
    bool Function(String path) predicate,
  ) async {
    final sessions = await _recentSessionsMatching(baseDir, predicate, limit: 1);
    if (sessions.isEmpty) return null;
    return sessions.first.summary;
  }

  Future<String?> _latestActiveRawPathMatching(
    String baseDir,
    bool Function(String path) predicate,
  ) async {
    final root = Directory(baseDir);
    if (!root.existsSync()) return null;

    final candidates = <({String path, DateTime modified})>[];
    for (final dir in root.listSync().whereType<Directory>().where((dir) => predicate(dir.path))) {
      final rawPath = '${dir.path}/raw.log';
      final rawFile = File(rawPath);
      if (!rawFile.existsSync()) continue;
      try {
        candidates.add((path: rawPath, modified: rawFile.statSync().modified));
      } catch (_) {
        candidates.add((path: rawPath, modified: dir.statSync().modified));
      }
    }
    if (candidates.isEmpty) return null;
    candidates.sort((a, b) => b.modified.compareTo(a.modified));
    return candidates.first.path;
  }

  bool _looksLikeRef115(String path) {
    final name = path.split('/').last.toLowerCase();
    return name.contains('ref115') ||
        name.contains('ref_tag_115') ||
        name.contains('continuity_opt_115') ||
        name.contains('ref115_');
  }

  bool _looksLikeRef115RangeSession(String path) {
    final name = path.split('/').last.toLowerCase();
    return name.contains('ref115') && name.contains('range');
  }

  String _stripLivePrefix(String line) {
    return line.replaceFirst(RegExp(r'^\[[^\]]+\]\s*'), '');
  }

  static final RegExp _positionRegex = RegExp(
    r'sweep=([-\d]+).*?xyz=\(([-\d]+),([-\d]+),([-\d]+)\).*?rms=([-\d]+).*?max=([-\d]+).*?anchors=\[([^\]]*)\]',
  );
}

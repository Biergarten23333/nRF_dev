import 'dart:convert';

class PositionSample {
  final int sweep;
  final int xMm;
  final int yMm;
  final int zMm;
  final int rmsMm;
  final int maxMm;
  final String anchors;

  const PositionSample({
    required this.sweep,
    required this.xMm,
    required this.yMm,
    required this.zMm,
    required this.rmsMm,
    required this.maxMm,
    required this.anchors,
  });
}

class SessionSummary {
  final String sessionDir;
  final Map<String, dynamic> data;

  SessionSummary({
    required this.sessionDir,
    required this.data,
  });

  static SessionSummary fromJsonFile(String sessionDir, String contents) {
    final decoded = jsonDecode(contents) as Map<String, dynamic>;
    return SessionSummary(sessionDir: sessionDir, data: decoded);
  }

  String? get snr => data['snr']?.toString();
  String? get port => data['port']?.toString();
  int? get uniquePositionSamples => _asInt(data['unique_position_samples']);
  int? get summarySamplesUsed => _asInt(data['summary_samples_used']);
  int? get rangeSamples => _asInt(data['range_samples']);

  Map<String, dynamic>? get positionMean =>
      data['position_mean_mm'] as Map<String, dynamic>?;
  Map<String, dynamic>? get positionStd =>
      data['position_std_mm'] as Map<String, dynamic>?;
  Map<String, dynamic>? get residualMean =>
      data['residual_mean_mm'] as Map<String, dynamic>?;
  Map<String, dynamic>? get residualStd =>
      data['residual_std_mm'] as Map<String, dynamic>?;
  Map<String, dynamic>? get anchorSubsetFrequencySummary =>
      data['anchor_subset_frequency_summary'] as Map<String, dynamic>?;
  Map<String, dynamic>? get anchorSubsetFrequencyAll =>
      data['anchor_subset_frequency_all'] as Map<String, dynamic>?;

  double? valueAsDouble(Map<String, dynamic>? map, String key) {
    final value = map?[key];
    if (value is num) return value.toDouble();
    return double.tryParse(value?.toString() ?? '');
  }

  int? _asInt(dynamic value) {
    if (value is int) return value;
    return int.tryParse(value?.toString() ?? '');
  }

  String formatMean() {
    final mean = positionMean;
    if (mean == null) return 'n/a';
    return '(${valueAsDouble(mean, 'x')?.toStringAsFixed(0) ?? 'n/a'}, '
        '${valueAsDouble(mean, 'y')?.toStringAsFixed(0) ?? 'n/a'}, '
        '${valueAsDouble(mean, 'z')?.toStringAsFixed(0) ?? 'n/a'}) mm';
  }

  String formatStd() {
    final std = positionStd;
    if (std == null) return 'n/a';
    return 'x=${valueAsDouble(std, 'x')?.toStringAsFixed(1) ?? 'n/a'} mm, '
        'y=${valueAsDouble(std, 'y')?.toStringAsFixed(1) ?? 'n/a'} mm, '
        'z=${valueAsDouble(std, 'z')?.toStringAsFixed(1) ?? 'n/a'} mm';
  }

  String formatResidual() {
    final residual = residualMean;
    if (residual == null) return 'n/a';
    return 'rms=${valueAsDouble(residual, 'rms')?.toStringAsFixed(1) ?? 'n/a'} mm, '
        'max=${valueAsDouble(residual, 'max')?.toStringAsFixed(1) ?? 'n/a'} mm';
  }
}

class AnchorLayoutEntry {
  final String id;
  final String name;
  final int x;
  final int y;
  final int z;

  const AnchorLayoutEntry({
    required this.id,
    required this.name,
    required this.x,
    required this.y,
    required this.z,
  });
}

class AnchorLayoutSnapshot {
  final List<AnchorLayoutEntry> anchors;

  const AnchorLayoutSnapshot(this.anchors);
}

class SessionEntry {
  final String path;
  final DateTime modified;
  final SessionSummary? summary;

  const SessionEntry({
    required this.path,
    required this.modified,
    required this.summary,
  });

  String get name => path.split('/').last;
}

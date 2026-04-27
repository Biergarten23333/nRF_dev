import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../shared/models/log_models.dart';
import '../shared/services/log_repository.dart';

class ThreeDViewPage extends StatefulWidget {
  const ThreeDViewPage({super.key});

  @override
  State<ThreeDViewPage> createState() => _ThreeDViewPageState();
}

class _ThreeDViewPageState extends State<ThreeDViewPage> {
  late final Timer _timer;
  late Future<_ThreeDSnapshot> _future;

  @override
  void initState() {
    super.initState();
    _future = _load();
    _timer = Timer.periodic(const Duration(seconds: 2), (_) {
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

  Future<_ThreeDSnapshot> _load() async {
    final repo = LogRepository.instance;
    final layout = await repo.loadRuntimeAnchorLayout();
    final refPath = await repo.latestRef115ActiveRawPath();
    final motionPath = await repo.latestMasterBleActiveRawPath();
    final refSamples = refPath == null
        ? const <PositionSample>[]
        : await repo.parsePositionSamplesFromRaw(refPath, limit: 120);
    final motionSamples = motionPath == null
        ? const <PositionSample>[]
        : await repo.parsePositionSamplesFromRaw(motionPath, limit: 120);
    return _ThreeDSnapshot(
      layout: layout,
      refSamples: refSamples,
      motionSamples: motionSamples,
    );
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<_ThreeDSnapshot>(
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
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: const [
                      Text('3D View', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
                      SizedBox(height: 8),
                      Text('Anchors are projected from runtime layout. Active tags come from latest live sessions.'),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 16),
              _ThreeDScene(
                layout: data?.layout ?? const AnchorLayoutSnapshot([]),
                refSamples: data?.refSamples ?? const [],
                motionSamples: data?.motionSamples ?? const [],
              ),
            ],
          ),
        );
      },
    );
  }
}

class _ThreeDSnapshot {
  final AnchorLayoutSnapshot layout;
  final List<PositionSample> refSamples;
  final List<PositionSample> motionSamples;

  _ThreeDSnapshot({
    required this.layout,
    required this.refSamples,
    required this.motionSamples,
  });
}

class _ThreeDScene extends StatelessWidget {
  final AnchorLayoutSnapshot layout;
  final List<PositionSample> refSamples;
  final List<PositionSample> motionSamples;

  const _ThreeDScene({
    required this.layout,
    required this.refSamples,
    required this.motionSamples,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: SizedBox(
          height: 620,
          width: double.infinity,
          child: CustomPaint(
            painter: _ThreeDPainter(
              anchors: layout.anchors,
              refPoint: _latestPoint(refSamples),
              motionPoint: _latestPoint(motionSamples),
            ),
          ),
        ),
      ),
    );
  }

  static _Point3D? _latestPoint(List<PositionSample> samples) {
    if (samples.isEmpty) return null;
    final last = samples.last;
    return _Point3D(last.xMm.toDouble(), last.yMm.toDouble(), last.zMm.toDouble());
  }
}

class _Point3D {
  final double x;
  final double y;
  final double z;

  const _Point3D(this.x, this.y, this.z);
}

class _ThreeDPainter extends CustomPainter {
  final List<AnchorLayoutEntry> anchors;
  final _Point3D? refPoint;
  final _Point3D? motionPoint;

  _ThreeDPainter({
    required this.anchors,
    required this.refPoint,
    required this.motionPoint,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final bgPaint = Paint()..color = const Color(0xFFF8FAFC);
    final gridPaint = Paint()
      ..color = const Color(0xFFD0D7DE)
      ..strokeWidth = 1.0;
    final axisPaint = Paint()
      ..color = const Color(0xFF94A3B8)
      ..strokeWidth = 2.0;
    final anchorPaint = Paint()..color = const Color(0xFF0F62FE);
    final refPaint = Paint()..color = const Color(0xFFEF4444);
    final motionPaint = Paint()..color = const Color(0xFF10B981);
    final textPainter = TextPainter(textDirection: TextDirection.ltr);

    canvas.drawRect(Offset.zero & size, bgPaint);
    final center = Offset(size.width * 0.5, size.height * 0.55);
    final scale = _scaleForSize(size);

    void drawGrid() {
      for (var i = -6; i <= 6; i++) {
        final dx = center.dx + i * 80;
        canvas.drawLine(Offset(dx, 0), Offset(dx, size.height), gridPaint);
      }
      for (var i = -4; i <= 6; i++) {
        final dy = center.dy + i * 60;
        canvas.drawLine(Offset(0, dy), Offset(size.width, dy), gridPaint);
      }
      canvas.drawLine(Offset(0, center.dy), Offset(size.width, center.dy), axisPaint);
      canvas.drawLine(Offset(center.dx, 0), Offset(center.dx, size.height), axisPaint);
    }

    Offset project(double x, double y, double z) {
      const angle = math.pi / 6.0;
      final isoX = (x - y) * math.cos(angle);
      final isoY = (x + y) * math.sin(angle) - z;
      return Offset(center.dx + isoX * scale, center.dy - isoY * scale);
    }

    drawGrid();

    for (final anchor in anchors) {
      final p = project(anchor.x.toDouble(), anchor.y.toDouble(), anchor.z.toDouble());
      canvas.drawCircle(p, 8, anchorPaint);
      textPainter.text = TextSpan(
        text: anchor.name,
        style: const TextStyle(color: Colors.black, fontSize: 12, fontWeight: FontWeight.w600),
      );
      textPainter.layout();
      textPainter.paint(canvas, p + const Offset(10, -12));
    }

    void drawTag(_Point3D? point, Paint paint, String label) {
      if (point == null) return;
      final p = project(point.x, point.y, point.z);
      canvas.drawCircle(p, 10, paint);
      canvas.drawCircle(
        p,
        18,
        Paint()
          ..color = paint.color.withValues(alpha: 0.2)
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2,
      );
      textPainter.text = TextSpan(
        text: label,
        style: TextStyle(color: paint.color, fontSize: 12, fontWeight: FontWeight.w600),
      );
      textPainter.layout();
      textPainter.paint(canvas, p + const Offset(12, -16));
    }

    drawTag(refPoint, refPaint, 'Ref115');
    drawTag(motionPoint, motionPaint, 'Motion');
  }

  double _scaleForSize(Size size) {
    final base = math.min(size.width, size.height);
    return base / 12000.0;
  }

  @override
  bool shouldRepaint(covariant _ThreeDPainter oldDelegate) {
    return oldDelegate.anchors != anchors ||
        oldDelegate.refPoint != refPoint ||
        oldDelegate.motionPoint != motionPoint;
  }
}

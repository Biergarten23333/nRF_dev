import 'dart:math' as math;

import 'package:flutter/material.dart';

class SeriesLine {
  final String label;
  final Color color;
  final List<double> values;

  const SeriesLine({
    required this.label,
    required this.color,
    required this.values,
  });
}

class SimpleSeriesChart extends StatelessWidget {
  final String title;
  final List<SeriesLine> series;
  final double height;

  const SimpleSeriesChart({
    super.key,
    required this.title,
    required this.series,
    this.height = 180,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
            const SizedBox(height: 12),
            SizedBox(
              height: height,
              width: double.infinity,
              child: CustomPaint(
                painter: _SeriesPainter(series),
                child: Container(),
              ),
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 12,
              runSpacing: 8,
              children: series
                  .map(
                    (s) => Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Container(width: 10, height: 10, color: s.color),
                        const SizedBox(width: 6),
                        Text(s.label),
                      ],
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

class _SeriesPainter extends CustomPainter {
  final List<SeriesLine> series;

  _SeriesPainter(this.series);

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.0
      ..strokeCap = StrokeCap.round;
    final fillPaint = Paint()
      ..color = Colors.white
      ..style = PaintingStyle.fill;
    final gridPaint = Paint()
      ..color = Colors.grey.withValues(alpha: 0.22)
      ..strokeWidth = 1.0;
    final textPainter = TextPainter(textDirection: TextDirection.ltr);

    const margin = EdgeInsets.fromLTRB(40, 12, 16, 28);
    final plot = Rect.fromLTWH(
      margin.left,
      margin.top,
      math.max(0.0, size.width - margin.horizontal),
      math.max(0.0, size.height - margin.vertical),
    );

    canvas.drawRect(plot, fillPaint);

    final allValues = <double>[];
    for (final s in series) {
      allValues.addAll(s.values);
    }
    if (allValues.isEmpty) {
      textPainter.text = const TextSpan(
        text: 'No data',
        style: TextStyle(color: Colors.grey),
      );
      textPainter.layout();
      textPainter.paint(canvas, Offset(plot.center.dx - 20, plot.center.dy - 10));
      return;
    }

    final minValue = allValues.reduce(math.min);
    final maxValue = allValues.reduce(math.max);
    final span = (maxValue - minValue).abs() < 1e-6 ? 1.0 : (maxValue - minValue);
    final lineCount = 4;
    for (var i = 0; i <= lineCount; i++) {
      final dy = plot.top + plot.height * (i / lineCount);
      canvas.drawLine(Offset(plot.left, dy), Offset(plot.right, dy), gridPaint);
      final value = maxValue - span * (i / lineCount);
      textPainter.text = TextSpan(
        text: value.toStringAsFixed(0),
        style: const TextStyle(fontSize: 10, color: Colors.grey),
      );
      textPainter.layout();
      textPainter.paint(canvas, Offset(4, dy - 6));
    }

    final pointCount = series.fold<int>(0, (count, s) => math.max(count, s.values.length));
    if (pointCount < 2) {
      return;
    }

    for (final s in series) {
      if (s.values.length < 2) continue;
      paint.color = s.color;
      final path = Path();
      for (var i = 0; i < s.values.length; i++) {
        final x = plot.left + plot.width * (i / (s.values.length - 1));
        final y = plot.bottom - plot.height * ((s.values[i] - minValue) / span);
        if (i == 0) {
          path.moveTo(x, y);
        } else {
          path.lineTo(x, y);
        }
      }
      canvas.drawPath(path, paint);
    }

    for (var i = 0; i <= 4; i++) {
      final dx = plot.left + plot.width * (i / 4);
      canvas.drawLine(Offset(dx, plot.top), Offset(dx, plot.bottom), gridPaint);
    }
  }

  @override
  bool shouldRepaint(covariant _SeriesPainter oldDelegate) {
    return oldDelegate.series != series;
  }
}

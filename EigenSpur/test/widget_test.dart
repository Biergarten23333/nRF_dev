import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:eigenspur_audiopos/main.dart';

void main() {
  testWidgets('field console smoke test', (tester) async {
    await tester.binding.setSurfaceSize(const Size(1280, 900));
    await tester.pumpWidget(const AutoPosFieldApp());
    expect(find.text('EigenSpur'), findsOneWidget);
    expect(find.text('AutoPos Sweep'), findsOneWidget);
    expect(find.text('EigenSpur Panning'), findsOneWidget);
    addTearDown(() => tester.binding.setSurfaceSize(null));
  });
}

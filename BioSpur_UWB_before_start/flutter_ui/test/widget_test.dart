import 'package:flutter_test/flutter_test.dart';

import 'package:biospur_uwb_ui/app.dart';

void main() {
  testWidgets('renders the BioSpur tab shell', (WidgetTester tester) async {
    await tester.pumpWidget(const BioSpurApp());

    expect(find.text('BioSpur UWB'), findsOneWidget);
    expect(find.text('Dashboard'), findsOneWidget);
    expect(find.text('Live View'), findsOneWidget);
    expect(find.text('Sessions'), findsOneWidget);
    expect(find.text('3D View'), findsOneWidget);
    expect(find.text('Autopositioning'), findsOneWidget);
  });
}

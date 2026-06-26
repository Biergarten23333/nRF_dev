import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_ui_ble/main.dart';

void main() {
  testWidgets('BioSpur BLE monitor renders', (tester) async {
    await tester.pumpWidget(const BioSpurBleApp(autoStart: false));
    expect(find.text('BioSpur BLE Monitor'), findsOneWidget);
    expect(find.text('Status Monitor'), findsOneWidget);
    expect(find.text('Raw Log'), findsOneWidget);
  });

  test('rejects malformed BADV rows', () {
    const malformed = 'BADV;1;14327615;BS955A;-70;TAG;BS955A;BS955A;tag90;-';
    expect(ParsedBleLine.parse(malformed), isNull);

    const valid =
        'BADV;1;14327615;D3:FD:93:FE:02:17 (random);-68;TAG;BS955A;BS955A;-;-;1';
    final parsed = ParsedBleLine.parse(valid);
    expect(parsed?.peer?.kind, 'TAG');
    expect(parsed?.peer?.id, 'BS955A');
    expect(parsed?.peer?.rssi, -68);
  });
}

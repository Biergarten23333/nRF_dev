enum BlePeerState { discovered, connected, disconnected }

class BlePeerInfo {
  final String key;
  final String address;
  final String displayName;
  final BlePeerState state;
  final String lastLine;
  final DateTime updatedAt;

  const BlePeerInfo({
    required this.key,
    required this.address,
    required this.displayName,
    required this.state,
    required this.lastLine,
    required this.updatedAt,
  });

  BlePeerInfo copyWith({
    String? address,
    String? displayName,
    BlePeerState? state,
    String? lastLine,
    DateTime? updatedAt,
  }) {
    return BlePeerInfo(
      key: key,
      address: address ?? this.address,
      displayName: displayName ?? this.displayName,
      state: state ?? this.state,
      lastLine: lastLine ?? this.lastLine,
      updatedAt: updatedAt ?? this.updatedAt,
    );
  }

  bool get isConnected => state == BlePeerState.connected;

  String get stateText {
    switch (state) {
      case BlePeerState.discovered:
        return 'discovered';
      case BlePeerState.connected:
        return 'connected';
      case BlePeerState.disconnected:
        return 'disconnected';
    }
  }
}

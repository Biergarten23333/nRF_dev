enum BackendMsgKind {
  log,
  raw,
  fuse,
  other,
}

class BackendMessage {
  final BackendMsgKind kind;
  final String line;

  const BackendMessage(this.kind, this.line);
}

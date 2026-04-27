import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'repo_paths.dart';

class LiveFeedRunner {
  LiveFeedRunner._();

  static final LiveFeedRunner instance = LiveFeedRunner._();

  Process? _process;
  final List<String> _logLines = [];
  final StreamController<void> _changes = StreamController<void>.broadcast();

  String? _activeName;
  String? _activeCommand;
  bool _starting = false;

  Stream<void> get changes => _changes.stream;
  bool get isRunning => _process != null;
  String? get activeName => _activeName;
  String? get activeCommand => _activeCommand;
  List<String> get logTail => _logLines.length <= 500
      ? List.unmodifiable(_logLines)
      : List.unmodifiable(_logLines.sublist(_logLines.length - 500));

  Future<void> start(String name, String command) async {
    if (_starting || _process != null) return;
    _starting = true;
    _activeName = name;
    _activeCommand = command;
    _logLines.add('[start] $name');
    _emit();
    try {
      final proc = await Process.start(
        'bash',
        ['-lc', command],
        workingDirectory: RepoPaths.root.path,
        runInShell: false,
      );
      _process = proc;
      _logLines.add('[pid] ${proc.pid}');
      _emit();
      unawaited(_pipe(proc.stdout, 'stdout'));
      unawaited(_pipe(proc.stderr, 'stderr'));
      proc.exitCode.then((code) {
        _logLines.add('[exit] $name code=$code');
        _process = null;
        _activeName = null;
        _activeCommand = null;
        _emit();
      });
    } catch (err) {
      _logLines.add('[error] $err');
      _process = null;
      _activeName = null;
      _activeCommand = null;
      _emit();
    } finally {
      _starting = false;
      _emit();
    }
  }

  Future<void> stop() async {
    final proc = _process;
    if (proc == null) return;
    _logLines.add('[stop] ${_activeName ?? 'process'}');
    _emit();
    proc.kill(ProcessSignal.sigterm);
    await Future<void>.delayed(const Duration(milliseconds: 200));
    if (_process != null) {
      _process?.kill(ProcessSignal.sigkill);
    }
  }

  Future<void> _pipe(Stream<List<int>> stream, String label) async {
    await for (final chunk in stream.transform(utf8.decoder)) {
      final lines = const LineSplitter().convert(chunk);
      for (final line in lines) {
        if (label == 'stdout') {
          _logLines.add(line);
        } else {
          _logLines.add('[$label] $line');
        }
      }
      _emit();
    }
  }

  void _emit() {
    if (!_changes.isClosed) {
      _changes.add(null);
    }
  }
}

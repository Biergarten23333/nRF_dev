import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

import '../../shared/services/repo_paths.dart';
import 'backend_message.dart';

class BackendClient {
  Process? _process;
  IOSink? _stdin;

  final StreamController<BackendMessage> _msgController =
      StreamController<BackendMessage>.broadcast();

  Stream<BackendMessage> get messages => _msgController.stream;

  bool get isRunning => _process != null;

  Future<void> start({
    required String exePath,
    required String serial1,
    String? serial2,
  }) async {
    if (isRunning) {
      debugPrint('Backend already running');
      return;
    }

    final args = <String>[serial1];
    if (serial2 != null && serial2.trim().isNotEmpty) {
      args.add(serial2);
    }

    final proc = await Process.start(
      exePath,
      args,
      workingDirectory: RepoPaths.root.path,
      runInShell: false,
    );

    _process = proc;
    _stdin = proc.stdin;

    unawaited(_pipe(proc.stdout, 'stdout'));
    unawaited(_pipe(proc.stderr, 'stderr'));

    proc.exitCode.then((code) {
      _msgController.add(BackendMessage(BackendMsgKind.log, '[exit] code=$code'));
      _process = null;
      _stdin = null;
    });
  }

  Future<void> stop() async {
    final proc = _process;
    if (proc == null) return;
    proc.kill(ProcessSignal.sigterm);
    await Future<void>.delayed(const Duration(milliseconds: 200));
    if (_process != null) {
      _process?.kill(ProcessSignal.sigkill);
    }
    _process = null;
    _stdin = null;
  }

  Future<void> sendCommand(String cmd) async {
    if (_stdin == null) return;
    _stdin!.writeln(cmd);
    await _stdin!.flush();
  }

  void dispose() {
    _msgController.close();
  }

  Future<void> _pipe(Stream<List<int>> stream, String label) async {
    await for (final chunk in stream.transform(utf8.decoder)) {
      final lines = const LineSplitter().convert(chunk);
      for (final line in lines) {
        final trimmed = line.trim();
        if (trimmed.isEmpty) continue;
        _msgController.add(_classifyLine(trimmed, label));
      }
    }
  }

  BackendMessage _classifyLine(String line, String label) {
    var kind = BackendMsgKind.log;
    if (line.contains('IMU_RAW') ||
        line.contains('UWB_RAW') ||
        line.contains('"dev"')) {
      kind = BackendMsgKind.raw;
    } else if (line.contains('POSE') || line.contains('FUSE')) {
      kind = BackendMsgKind.fuse;
    }
    return BackendMessage(kind, '[$label] $line');
  }
}

# Collection error resolution

The R2.6C-S broad retry stopped on seven import/module-name collection errors caused by duplicate test basenames, bare `conftest` imports, and the default import mode. R1 used `--import-mode=importlib` with the phase3r and phase3r2 test-helper directories explicit in `PYTHONPATH`. The guarded collect-only command exited 0 and collected 659 unique nodeids.

The production qualification is deliberately segmented into four recorded commands. It is not described as a full Fusion suite. `test_s2_terminal_audit.py` remains an expected environment skip and contributes zero heading-gauge coverage.

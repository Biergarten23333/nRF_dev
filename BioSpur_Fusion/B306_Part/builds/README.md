# B306 build trees

All generated B306 application and Fusion Master DK build trees belong here:

```text
B306_Part/builds/<target>-<purpose>/
```

Pass that path to `west build -d`. Do not create `build/` or `build-*` beside
`firmware/`, `host/`, or at the `B306_Part/` root. Generated contents are
ignored; this README is the only tracked file in the directory.

Build trees moved here on 2026-07-20 are artifact archives: Zephyr/CMake cache
files contain their original absolute paths and are not relocatable. Reuse a
moved name only with `west build --pristine=always`. The current
`b306-fast-ota-v3` and `dk-ota-v3` trees were recreated cleanly in this
directory after the layout change.

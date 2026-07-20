# B306 build trees

All generated B306 application and Fusion Master DK build trees belong here:

```text
B306_Part/builds/<target>-<purpose>/
```

Pass that path to `west build -d`. Do not create `build/` or `build-*` beside
`firmware/`, `host/`, or at the `B306_Part/` root. Generated contents are
ignored; this README is the only tracked file in the directory.

# UWB build trees

All generated UWB build trees belong here:

```text
UWB_Part/builds/<target>-<purpose>/
```

The writable `fusion-link/src/scripts/build_*.sh` wrappers enforce this root
and accept a short build name such as `tag-fusion-link`. The read-only FREEZE
is never edited; when invoking one of its scripts directly, pass an absolute
path under this directory. Generated contents are ignored; this README is the
only tracked file in the directory.

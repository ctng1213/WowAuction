"""The running AHSync's version (SPEC §3.7.11).

In source both stay as below; the release workflow writes the tag's version and the
build date into this file before PyInstaller runs, so a release needs no version-bump
commit. `EXPORT_VERSION` (the addon file format, SPEC §3.5) is separate.
"""

VERSION = "0.0.0"      # "MAJOR.MINOR.PATCH"; "0.0.0" is a dev build, which never checks for updates
BUILT = ""             # the build date, "YYYY-MM-DD"; "" from source

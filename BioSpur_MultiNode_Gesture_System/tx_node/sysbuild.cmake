# OTA-first sysbuild entry point for the TX role.
#
# MCUboot enablement is driven by the application Kconfig in this baseline.
# This file is intentionally minimal so `west build --sysbuild` can discover
# the app cleanly without introducing target-order side effects.

#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_NAME="biospur-ble"
DISPLAY_NAME="BioSpur BLE Monitor"
VERSION="$(awk '/^version:/ {print $2; exit}' "${ROOT}/pubspec.yaml" | cut -d+ -f1)"
ARCH="amd64"
BUILD_DIR="${ROOT}/build/linux/x64/release/bundle"
PKG_ROOT="${ROOT}/build/deb/${APP_NAME}_${VERSION}_${ARCH}"
DEB_OUT="${ROOT}/build/deb/${APP_NAME}_${VERSION}_${ARCH}.deb"

if [[ ! -x "${BUILD_DIR}/flutter_ui_ble" ]]; then
  echo "[deb] missing release bundle; run: flutter build linux --release" >&2
  exit 1
fi

rm -rf "${PKG_ROOT}"
mkdir -p \
  "${PKG_ROOT}/DEBIAN" \
  "${PKG_ROOT}/opt/${APP_NAME}" \
  "${PKG_ROOT}/usr/bin" \
  "${PKG_ROOT}/usr/share/applications" \
  "${PKG_ROOT}/usr/share/icons/hicolor/256x256/apps"

cp -a "${BUILD_DIR}/." "${PKG_ROOT}/opt/${APP_NAME}/"
cp -a "${ROOT}/scripts" "${PKG_ROOT}/opt/${APP_NAME}/scripts"
install -m 0644 "${ROOT}/assets/images/biospur_app_icon.png" \
  "${PKG_ROOT}/usr/share/icons/hicolor/256x256/apps/${APP_NAME}.png"

cat > "${PKG_ROOT}/usr/bin/${APP_NAME}" <<'EOF'
#!/usr/bin/env bash
exec /opt/biospur-ble/flutter_ui_ble "$@"
EOF
chmod 0755 "${PKG_ROOT}/usr/bin/${APP_NAME}"

cat > "${PKG_ROOT}/usr/share/applications/com.biospur.ble_monitor.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=${DISPLAY_NAME}
Comment=BioSpur BLE listener status monitor
Exec=${APP_NAME}
Icon=${APP_NAME}
Terminal=false
Categories=Science;Utility;
StartupNotify=true
StartupWMClass=com.biospur.ble_monitor
EOF

INSTALLED_SIZE="$(du -sk "${PKG_ROOT}" | awk '{print $1}')"
cat > "${PKG_ROOT}/DEBIAN/control" <<EOF
Package: ${APP_NAME}
Version: ${VERSION}
Section: science
Priority: optional
Architecture: ${ARCH}
Depends: libgtk-3-0, libstdc++6, libc6, python3, python3-serial
Installed-Size: ${INSTALLED_SIZE}
Maintainer: BioSpur <dev@biospur.local>
Description: BioSpur BLE Monitor
 Desktop UI for the nRF52840 Dongle BLE listener, with live status and raw logs.
EOF

dpkg-deb --build --root-owner-group "${PKG_ROOT}" "${DEB_OUT}"
echo "[deb] wrote ${DEB_OUT}"

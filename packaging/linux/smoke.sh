#!/bin/sh
set -eu
deb=$(find /packages -maxdepth 1 -name '*.deb' -print -quit)
archive=$(find /packages -maxdepth 1 -name '*.tar.gz' -print -quit)
if command -v python3 >/dev/null 2>&1; then echo 'Smoke image must not contain Python'; exit 1; fi
dpkg -i "$deb"
package_version=$(dpkg-query -W -f='${Version}' santhau-collector)
dpkg --compare-versions "$package_version" gt '0.0.1~rc3'
test "$(dpkg-query -W -f='${Installed-Size}' santhau-collector)" -gt 0
test ! -d /opt/santhau-collector/zstandard
santhau-collector --version
santhau-collector self-test --data-root '/tmp/data with spaces'
find '/tmp/data with spaces' -type f -exec sha256sum '{}' \; | sort > /tmp/before
dpkg -i "$deb"
find '/tmp/data with spaces' -type f -exec sha256sum '{}' \; | sort > /tmp/upgraded
cmp /tmp/before /tmp/upgraded
dpkg -r santhau-collector
test -f '/tmp/data with spaces/warehouse.sqlite'
find '/tmp/data with spaces' -type f -exec sha256sum '{}' \; | sort > /tmp/removed
cmp /tmp/before /tmp/removed
mkdir -p '/tmp/extracted bundle'
tar -xzf "$archive" -C '/tmp/extracted bundle'
bundle=$(find '/tmp/extracted bundle' -mindepth 1 -maxdepth 1 -type d -print -quit)
sh "$bundle/install.sh" '/tmp/custom install'
'/tmp/custom install/santhau-collector' self-test --data-root '/tmp/portable data'
sh '/tmp/custom install/uninstall.sh'
test -f '/tmp/portable data/warehouse.sqlite'
test ! -e '/tmp/custom install/app'
printf '%s\n' 'PASS: standalone without Python, deb install/upgrade/remove, tar install/remove, Data Root preserved'

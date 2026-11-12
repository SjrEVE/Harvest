#!/bin/sh
# Actual packaged user service on the disposable Ubuntu CI VM; fixture only.
set -eu
packages=${1:?package directory required}
deb=$(find "$packages" -maxdepth 1 -name '*.deb' -print -quit)
test -n "$deb"
sudo dpkg -i "$deb"
runtime_uid=$(id -u)
export XDG_RUNTIME_DIR="/run/user/$runtime_uid"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
sudo loginctl enable-linger "$(id -un)"
sudo systemctl start "user@$runtime_uid.service"
work=$(mktemp -d /tmp/santhau-systemd-smoke.XXXXXX)
dropin="$HOME/.config/systemd/user/santhau-collector.service.d"
mkdir -p "$dropin"
cleanup() {
    systemctl --user disable --now santhau-collector.service >/dev/null 2>&1 || true
    rm -f -- "$dropin/99-ci-offline.conf"
    systemctl --user daemon-reload || true
}
trap cleanup EXIT INT TERM
santhau-collector self-test-deep --data-root "$work/data"
cat > "$dropin/99-ci-offline.conf" <<EOF
[Service]
ExecStart=
ExecStart=/opt/santhau-collector/santhau-collector run --offline-test --data-root $work/data
EOF
systemctl --user daemon-reload
systemd-analyze --user verify /usr/lib/systemd/user/santhau-collector.service
systemctl --user enable --now santhau-collector.service
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if santhau-collector status --data-root "$work/data" > "$work/status.json" &&
       grep -q '"phase": "IDLE"' "$work/status.json"; then break; fi
    sleep 1
done
cat "$work/status.json"
grep -q '"phase": "IDLE"' "$work/status.json"
santhau-collector deep-status --data-root "$work/data" > "$work/deep.json"
grep -q '"FETCHED": 9' "$work/deep.json"
systemctl --user is-active santhau-collector.service
systemctl --user is-enabled santhau-collector.service
systemctl --user restart santhau-collector.service
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if santhau-collector status --data-root "$work/data" > "$work/restarted.json" &&
       grep -q '"phase": "IDLE"' "$work/restarted.json"; then break; fi
    sleep 1
done
grep -q '"phase": "IDLE"' "$work/restarted.json"
systemctl --user is-active santhau-collector.service
systemctl --user stop santhau-collector.service
test "$(systemctl --user show santhau-collector.service --property=ExecMainStatus --value)" = 0
test "$(systemctl --user show santhau-collector.service --property=Result --value)" = success
test -f "$work/data/warehouse.sqlite"
test ! -f "$work/data/serving.sqlite"
santhau-collector status --data-root "$work/data" > "$work/stopped.json"
grep -q '"running": false' "$work/stopped.json"
printf '%s\n' 'PASS: packaged systemd user service enable/start/status/restart/stop, offline data preserved'

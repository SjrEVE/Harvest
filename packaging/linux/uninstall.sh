#!/bin/sh
set -eu
prefix=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
case "$prefix" in /|/usr|/usr/local|/opt|/var|/home|"$HOME") echo 'Từ chối gỡ ở thư mục không an toàn.' >&2; exit 2;; esac
if [ "$(cat "$prefix/.santhau-collector-install" 2>/dev/null)" != santhau-collector ]; then
    echo 'Không tìm thấy dấu nhận diện bộ cài.' >&2; exit 2
fi
if find "$prefix" -name warehouse.sqlite -print -quit | grep -q .; then
    echo 'Có kho dữ liệu nằm trong thư mục cài; giữ nguyên và từ chối xóa.' >&2; exit 2
fi
rm -rf -- "$prefix/app"
rm -f -- "$prefix/santhau-collector" "$prefix/.santhau-collector-install" "$prefix/uninstall.sh"
rmdir -- "$prefix" 2>/dev/null || true
printf '%s\n' 'Đã gỡ chương trình. Kho dữ liệu bên ngoài được giữ nguyên.'

#!/bin/sh
set -eu
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
prefix=${1:-"$HOME/.local/opt/santhau-collector"}
case "$prefix" in /*) ;; *) echo 'Cần đường dẫn cài tuyệt đối.' >&2; exit 2;; esac
case "$prefix" in /|/usr|/usr/local|/opt|/var|/home|"$HOME") echo 'Thư mục cài không an toàn.' >&2; exit 2;; esac
if [ -e "$prefix" ] && [ ! -f "$prefix/.santhau-collector-install" ]; then
    echo 'Thư mục đã tồn tại và không thuộc bộ cài này.' >&2; exit 2
fi
if [ -d "$prefix" ] && find "$prefix" -name warehouse.sqlite -print -quit | grep -q .; then
    echo 'Không cài đè lên thư mục đang chứa kho dữ liệu.' >&2; exit 2
fi
mkdir -p -- "$prefix/app"
cp -a -- "$source_dir/app/." "$prefix/app/"
cp -- "$source_dir/uninstall.sh" "$prefix/uninstall.sh"
printf '%s\n' santhau-collector > "$prefix/.santhau-collector-install"
cat > "$prefix/santhau-collector" <<'SCRIPT'
#!/bin/sh
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
exec "$base/app/santhau-collector" "$@"
SCRIPT
chmod 755 "$prefix/santhau-collector" "$prefix/uninstall.sh"
printf 'Đã cài: %s/santhau-collector\nKhông tự khởi động hoặc gọi nguồn. Data Root phải nằm ngoài thư mục cài.\n' "$prefix"

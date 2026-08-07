#!/bin/sh
set -eu

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this script as the desktop user, not as root." >&2
    exit 1
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
binary_dir=${XDG_BIN_HOME:-"$HOME/.local/bin"}
binary_path="$binary_dir/computer-control-kwin-ei"

sudo dnf install -y \
    gcc \
    glib2-devel \
    kdotool \
    libei-devel \
    pkgconf-pkg-config \
    spectacle

mkdir -p "$binary_dir"
cc -std=c11 -O2 -Wall -Wextra -Werror \
    "$script_dir/native/kwin_ei_helper.c" \
    -o "$binary_path" \
    $(pkg-config --cflags --libs libei-1.0 gio-unix-2.0)

echo "KDE Wayland helper installed at $binary_path"

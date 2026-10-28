#!/bin/sh
# Manual research helper, deliberately absent from Glasshouse's deploy list.
set -eu
case "${1:-}" in
    --prepare|--enable) MODE=$1 ;;
    *) echo 'Usage: sh install.sh --prepare|--enable' >&2; exit 2 ;;
esac
[ "$(id -u)" = 0 ] || { echo 'Run on the rooted TV.' >&2; exit 1; }
[ ! -f /var/lib/c9-hdmi1-audio/enabled ] || { echo 'Another C9 EDID service is enabled; stop it first.' >&2; exit 1; }
SOURCE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
DEST=/var/lib/glasshouse-c9-edid
HOOK=/var/lib/webosbrew/init.d/60-glasshouse-c9-edid
[ ! -f "$DEST/enabled" ] || { echo 'Already enabled; disable and restore before replacing files.' >&2; exit 1; }
# state.py refuses to replace an existing per-TV backup.
if [ ! -e "$DEST" ]; then
    /usr/bin/python "$SOURCE/state.py"
fi
/usr/bin/python -c 'import sys; sys.path.insert(0,sys.argv[1]); from state import verify_firmware,load_backup; verify_firmware(); load_backup()' "$SOURCE"
if [ -e "$HOOK" ]; then
    cmp -s "$SOURCE/60-glasshouse-c9-edid" "$HOOK" || { echo 'Unexpected existing hook; refusing replacement.' >&2; exit 1; }
fi
for name in state.py edid.py transaction.py service.py control; do
    cp "$SOURCE/$name" "$DEST/$name"
    chmod 700 "$DEST/$name"
done
cp "$SOURCE/60-glasshouse-c9-edid" "$HOOK"
chmod 700 "$HOOK"
if [ "$MODE" = --enable ]; then
    : > "$DEST/enabled"
    chmod 600 "$DEST/enabled"
    "$HOOK"
    echo "Enabled. Status: $DEST/control status"
else
    echo 'Prepared without native writes or automatic startup. Copy the private backup off the TV.'
fi
echo "Disable and restore: $DEST/control disable"

#!/bin/sh
# Explicit preparation; never part of Glasshouse's own install/update.
set -eu
SOURCE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
exec /usr/bin/python -B "$SOURCE/control.py" install "$@"

"""Private atomic state with file and directory fsync before hardware writes."""

import contextlib
import fcntl
import json
import os
import stat
import uuid
from edid import require


def directory(path):
    if not os.path.lexists(path):
        os.mkdir(path, 0o700)
        sync_directory(os.path.dirname(path) or ".")
    info = os.lstat(path)
    require(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o700,
        "State directory must be privately owned, mode 0700",
    )
    return path


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_json(path):
    info = os.lstat(path)
    require(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o600,
        "State must be an owned regular file, mode 0600",
    )
    with open(path) as source:
        return json.load(source)


def atomic_bytes(path, data):
    parent = os.path.dirname(path)
    temporary = path + ".new-" + uuid.uuid4().hex
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.rename(temporary, path)
        sync_directory(parent)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    atomic_bytes(path, json.dumps(value, sort_keys=True).encode("utf8"))


@contextlib.contextmanager
def lock(path):
    descriptor = os.open(
        path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        info = os.fstat(descriptor)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o600,
            "Lock must be privately owned, mode 0600",
        )
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)

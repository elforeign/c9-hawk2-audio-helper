"""Pure per-input policy. HDMI 2 never has a writable profile."""

import binascii
import hashlib
import json
from edid import build_candidate, parse, require

PORTS = (1, 3, 4)
ALL_PORTS = ("1", "2", "3", "4")
PROFILE = "pcm51-48k"
try:
    STRING_TYPES = (basestring,)
except NameError:
    STRING_TYPES = (str,)


def parse_ports(value):
    if isinstance(value, STRING_TYPES):
        parts = [] if value == "none" else value.split(",")
    else:
        parts = list(value)
    result = []
    for part in parts:
        require(
            str(part) in ("1", "3", "4"),
            "Only HDMI 1, 3 and 4 are writable; HDMI 2 is reserved for eARC",
        )
        port = int(part)
        require(port not in result, "Duplicate HDMI input")
        result.append(port)
    return sorted(result)


def decode(value):
    require(isinstance(value, STRING_TYPES), "EDID must be hexadecimal text")
    require(len(value) == 512, "Expected a 256-byte EDID")
    data = binascii.unhexlify(value)
    parse(data)
    return data


def hexbytes(data):
    return binascii.hexlify(data).decode("ascii")


def candidates(backup):
    return {
        str(port): hexbytes(build_candidate(decode(backup["edids"][str(port)])))
        for port in PORTS
    }


def normalize_backup(backup):
    require(
        set(backup["edids"]) == set(ALL_PORTS), "Backup must contain all four inputs"
    )
    for port, value in backup["edids"].items():
        data = decode(value)
        addresses = [
            ((bytearray(block)[4] << 8) | bytearray(block)[5])
            for block in parse(data)
            if len(block) >= 6
            and bytearray(block)[0] >> 5 == 3
            and bytearray(block)[1:4] == bytearray([3, 12, 0])
        ]
        require(
            addresses == [int(port) << 12],
            "Unexpected CEC physical address for HDMI " + port,
        )
    built = candidates(backup)
    hashes = {
        port: hashlib.sha256(decode(value)).hexdigest() for port, value in built.items()
    }
    version = backup.get("format_version", 1)
    require(version in (1, 2), "Unknown backup format")
    if version == 1:
        require(
            hashes["1"] == backup["candidate_sha256"], "Candidate checksum mismatch"
        )
    else:
        require(hashes == backup["candidate_sha256s"], "Candidate checksum mismatch")
    result = dict(backup)
    result["format_version"] = 2
    result["candidate_sha256s"] = hashes
    result["candidate_sha256"] = hashes["1"]
    return result


def backup_id(backup):
    value = {
        key: backup[key]
        for key in (
            "profile",
            "binary_hashes",
            "edids",
            "preservation",
            "candidate_sha256s",
        )
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf8")).hexdigest()


def validate_map(backup, current):
    require(set(current) == set(ALL_PORTS), "Expected all four input readbacks")
    built = candidates(backup)
    for port in ALL_PORTS:
        require(
            current[port]
            in [backup["edids"][port]] + ([built[port]] if port in built else []),
            "Unrecognized HDMI %s EDID; no write permitted" % port,
        )
    return current


def selected_map(backup, ports):
    selected = parse_ports(ports)
    result = dict(backup["edids"])
    built = candidates(backup)
    for port in selected:
        result[str(port)] = built[str(port)]
    return result


def native_action(backup, port, value):
    require(port in PORTS, "HDMI 2 is reserved for eARC")
    if value == backup["edids"][str(port)]:
        return "restore"
    require(value == candidates(backup)[str(port)], "Unrecognized native target")
    return "apply"

"""Pure per-input policy. HDMI 2 never has a writable profile."""

import binascii
import hashlib
import json
from edid import build_candidate, parse, require

PORTS = (1, 3, 4)
ALL_PORTS = ("1", "2", "3", "4")
PROFILE = "pcm51-48k"
TRIAL_PROFILE = "pcm71-48k"
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


def experimental_policy(backup, operation):
    """Only a bounded HDMI 1 trial journal can authorize eight-channel bytes."""
    if operation is None or "trial_profile" not in operation:
        require(operation is None or "trial_port" not in operation,
                "Trial input requires an explicit profile")
        return None
    require(operation.get("trial_profile") == TRIAL_PROFILE
            and operation.get("trial_port") == 1
            and operation.get("action") == "trial"
            and operation.get("decision") == "rollback",
            "Eight-channel profile is an HDMI 1 rollback-only trial")
    validate_map(backup, operation["before"])
    require(operation["config_before"] == operation["config_after"],
            "A trial cannot change the persistent selection")
    expected = dict(operation["before"])
    expected["1"] = trial_candidate(backup, 1, TRIAL_PROFILE)
    require(operation["after"] == expected, "Unexpected eight-channel trial target")
    return TRIAL_PROFILE


def trial_candidate(backup, port, profile):
    require(port in PORTS, "HDMI 2 is reserved for eARC")
    require(profile == PROFILE or (profile == TRIAL_PROFILE and port == 1),
            "Experimental PCM 7.1 is trial-only on HDMI 1")
    return hexbytes(build_candidate(decode(backup["edids"][str(port)]),
                                    channels=8 if profile == TRIAL_PROFILE else 6))


def validate_map(backup, current, experimental=None):
    require(set(current) == set(ALL_PORTS), "Expected all four input readbacks")
    require(experimental in (None, TRIAL_PROFILE), "Unknown trial policy")
    built = candidates(backup)
    for port in ALL_PORTS:
        allowed = [backup["edids"][port]] + ([built[port]] if port in built else [])
        if experimental == TRIAL_PROFILE and port == "1":
            allowed.append(trial_candidate(backup, 1, TRIAL_PROFILE))
        require(
            current[port] in allowed,
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


def native_action(backup, port, value, experimental=None):
    require(port in PORTS, "HDMI 2 is reserved for eARC")
    if value == backup["edids"][str(port)]:
        return "restore"
    if experimental == TRIAL_PROFILE and port == 1 and value == trial_candidate(backup, 1, TRIAL_PROFILE):
        return "trial71"
    require(value == candidates(backup)[str(port)], "Unrecognized native target")
    return "apply"

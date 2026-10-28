"""Private per-TV backup and read-only firmware/preservation checks."""

from __future__ import print_function
import glob
import hashlib
import json
import os
import stat
import signal
import subprocess
import time
from edid import as_bytes, build_candidate, parse, require

BASE = "/var/lib/glasshouse-c9-edid"
PROFILE = "c9-hawk2-05.50.00"
PINS = {
    "/mnt/lg/tvservice/lgapp/tvservice": "e78009fe1a270f8a61eec3a1fe72ab709a73b4dce028cad8cc2fb6dddaf187af",
    "/usr/lib/libdbgfrwk.so.1": "973a206abba9c6ef2dc5560072add71e1b211231cacaadb0af1a654c3a4fafe7",
}
READ_METHODS = [
    "com.webos.service.tv.systemproperty/getEdidData",
    "com.webos.service.tv.systemproperty/getWhiteBalance",
    "com.webos.service.tv.systemproperty/getProperties",
    "com.webos.settingsservice/getSystemSettings",
    "com.webos.service.config/getConfigs",
    "com.webos.service.tvpower/power/getPowerState",
]


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while True:
            chunk = source.read(1048576)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_firmware():
    require(os.geteuid() == 0, "Run on the rooted TV")
    require(__debug__, "Do not run with Python optimization")
    for path, digest in PINS.items():
        require(file_hash(path) == digest, "Unvalidated firmware: " + path)


def luna(uri, payload):
    require(uri in READ_METHODS, "Read method not allowed")
    process = subprocess.Popen(
        [
            "timeout",
            "-t",
            "4",
            "luna-send",
            "-w",
            "2500",
            "-n",
            "1",
            "-f",
            "luna://" + uri,
            json.dumps(payload),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    raw = process.stdout.read()
    process.stderr.read()
    process.wait()
    process.stdin.close()
    require(process.returncode == 0, "Read failed: " + uri)
    result = json.loads(raw)
    require(result.get("returnValue") is True, "Read refused: " + uri)
    return result


def read_edids():
    result = {}
    for port in range(1, 5):
        response = luna(READ_METHODS[0], {"inputType": 0, "portNumber": port})[
            "edidData"
        ]
        require(response["length"] == 256, "Unexpected EDID length")
        data = as_bytes(response["data"])
        parse(data)
        result[str(port)] = data.hex() if hasattr(data, "hex") else data.encode("hex")
    return result


def preserved():
    paths = sorted(
        glob.glob("/mnt/lg/cmn_data/external_pqldb/*")
        + glob.glob("/mnt/lg/cmn_data/dolby/*")
    )
    hashes = {path: file_hash(path) for path in paths if os.path.isfile(path)}
    require(
        len(hashes) == 29,
        "Unvalidated calibration file layout; expected the measured 29 files",
    )
    balance = {
        temperature: luna(READ_METHODS[1], {"colorTemp": temperature})["setting"]
        for temperature in ["cool", "medium", "warm"]
    }
    oled = luna(READ_METHODS[2], {"keys": ["OledTPC", "OledGSR"]})
    picture = luna(
        READ_METHODS[3],
        {"category": "picture", "keys": ["screenShift", "logoLuminanceAdjust"]},
    )["settings"]
    return {
        "calibration_hashes": hashes,
        "white_balance": balance,
        "oled": {key: oled[key] for key in ["OledTPC", "OledGSR"]},
        "picture_protections": picture,
    }


def load_backup():
    path = BASE + "/backup.json"
    info = os.lstat(path)
    require(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == 0
        and stat.S_IMODE(info.st_mode) == 0o600,
        "Backup must be a root-owned regular file with mode 0600",
    )
    with open(path) as source:
        backup = json.load(source)
    require(
        backup.get("profile") == PROFILE and backup.get("binary_hashes") == PINS,
        "Backup profile mismatch",
    )
    original = __import__("binascii").unhexlify(backup["edids"]["1"])
    candidate = build_candidate(original)
    require(
        hashlib.sha256(candidate).hexdigest() == backup["candidate_sha256"],
        "Candidate checksum mismatch",
    )
    return backup


def stop_unfinished_controller(pid, started):
    # Only the fork's own still-running parent is eligible. Stop it before
    # recovery so a delayed controller cannot load the candidate afterward.
    if os.getppid() != pid:
        return
    require(pid > 1 and pid != os.getpid(), "Invalid controller PID")
    with open("/proc/%d/stat" % pid) as source:
        current = source.read().rsplit(")", 1)[1].split()[19]
    require(current == started, "Controller identity changed")
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + 0.5
    while os.getppid() == pid and time.time() < deadline:
        time.sleep(0.01)
    if os.getppid() == pid:
        os.kill(pid, signal.SIGKILL)


def capture():
    verify_firmware()
    require(
        not os.path.lexists(BASE),
        "Backup directory already exists; never overwrite the original backup",
    )
    config = luna(
        READ_METHODS[4],
        {"configNames": ["tv.model.hdmiSwitchType", "tv.model.edidType"]},
    )["configs"]
    require(
        config == {"tv.model.hdmiSwitchType": "HAWK2", "tv.model.edidType": "TrueHD"},
        "Unvalidated HDMI hardware",
    )
    require(luna(READ_METHODS[5], {})["state"] == "Active", "TV must be active")
    first = read_edids()
    snapshot = preserved()
    require(
        first == read_edids() and snapshot == preserved(),
        "Backup changed during capture",
    )
    original = __import__("binascii").unhexlify(first["1"])
    candidate = build_candidate(original)
    backup = {
        "profile": PROFILE,
        "binary_hashes": PINS,
        "edids": first,
        "preservation": snapshot,
        "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
        "captured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    os.mkdir(BASE, 0o700)
    for name, data in [
        ("original-hdmi1.bin", original),
        ("candidate-hdmi1.bin", candidate),
    ]:
        with open(BASE + "/" + name, "wb") as output:
            output.write(data)
        os.chmod(BASE + "/" + name, 0o600)
    with open(BASE + "/backup.json", "w") as output:
        json.dump(backup, output, sort_keys=True)
    os.chmod(BASE + "/backup.json", 0o600)
    print(
        json.dumps(
            {
                "backup_directory": BASE,
                "profile": PROFILE,
                "candidate_sha256": backup["candidate_sha256"],
            }
        )
    )


if __name__ == "__main__":
    capture()

from __future__ import print_function
import os, sys, json, time, subprocess, hashlib, fcntl
from state import BASE, verify_firmware, load_backup
import binascii

verify_firmware()
backup = load_backup()
RUN = "/tmp/glasshouse-c9-edid"
ORIGINAL = hashlib.sha256(binascii.unhexlify(backup["edids"]["1"])).hexdigest()
CANDIDATE = backup["candidate_sha256"]


def utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def writejson(path, value):
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w") as f:
        json.dump(value, f, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.rename(tmp, path)


def report(state, **kw):
    r = {
        "state": state,
        "utc": utc(),
        "boot_id": open("/proc/sys/kernel/random/boot_id").read().strip(),
        "pid": os.getpid(),
    }
    r.update(kw)
    writejson(RUN + "/status.json", r)
    print(json.dumps(r))
    sys.stdout.flush()


def luna(uri, payload):
    assert uri in [
        "com.webos.service.tvpower/power/getPowerState",
        "com.webos.service.tv.systemproperty/getEdidData",
    ]
    p = subprocess.Popen(
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
    raw = p.stdout.read()
    err = p.stderr.read()
    p.wait()
    p.stdin.close()
    assert p.returncode == 0, err
    r = json.loads(raw)
    assert r.get("returnValue") is True, r
    return r


def current():
    d = luna(
        "com.webos.service.tv.systemproperty/getEdidData",
        {"inputType": 0, "portNumber": 1},
    )["edidData"]
    assert d["length"] == 256
    b = "".join(chr(x) for x in d["data"])
    assert sum(bytearray(b[:128])) % 256 == sum(bytearray(b[128:])) % 256 == 0
    return hashlib.sha256(b).hexdigest()


def transaction(action):
    p = subprocess.Popen(
        ["/usr/bin/python", BASE + "/transaction.py", action],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out, err = p.communicate()
    assert p.returncode == 0, (p.returncode, err[-1000:], out[-1000:])
    r = json.loads(out)
    if r.get("no_hardware_write"):
        assert r.get("verified")
        return r
    guard = r["guard_directory"]
    assert guard.startswith("/tmp/glasshouse-c9-edid-txn-")
    deadline = time.time() + 65
    while time.time() < deadline:
        if os.path.isfile(guard + "/result.json"):
            result = json.load(open(guard + "/result.json"))
            assert result.get("committed") and result.get("verified"), result
            result["guard_directory"] = guard
            return result
        time.sleep(0.2)
    raise RuntimeError("Native transaction recovery result timed out; no retries")


if not os.path.isdir(RUN):
    os.mkdir(RUN, 0o700)
assert len(sys.argv) == 2 and sys.argv[1] in ["daemon", "disable", "status"]
action = sys.argv[1]
if action == "status":
    print(
        open(RUN + "/status.json").read()
        if os.path.exists(RUN + "/status.json")
        else '{"state":"not started"}'
    )
    sys.exit(0)
if action == "disable":
    if os.path.exists(BASE + "/enabled"):
        os.unlink(BASE + "/enabled")
    # The daemon releases this lock after seeing the marker disappear.
    lock = os.open(RUN + "/daemon.lock", os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.time() + 40
    locked = False
    while time.time() < deadline:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            break
        except IOError:
            time.sleep(0.2)
    assert locked, "Service still busy; wait and run disable again"
    verify_firmware()
    result = transaction("restore")
    assert current() == ORIGINAL
    report("disabled; original verified", result=result)
    sys.exit(0)
lock = os.open(RUN + "/daemon.lock", os.O_CREAT | os.O_RDWR, 0o600)
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except IOError:
    sys.exit(0)
report("waiting for active TV")
attempts = 0
stable_original = 0
last_attempt = 0
last_state = None
# Read-only polling: writes are allowed only for the exact original HDMI1 EDID.
# At most three applications per boot and at least five minutes apart.
while os.path.isfile(BASE + "/enabled"):
    in_transaction = False
    try:
        power = luna("com.webos.service.tvpower/power/getPowerState", {})["state"]
        if power != "Active":
            stable_original = 0
            if last_state != "inactive":
                report("waiting; TV is not active", power=power)
                last_state = "inactive"
        else:
            digest = current()
            if digest == CANDIDATE:
                stable_original = 0
                if last_state != "active":
                    report(
                        "active; verified candidate",
                        edid_sha256=digest,
                        applications_this_boot=attempts,
                    )
                    last_state = "active"
            elif digest == ORIGINAL:
                stable_original += 1
                if stable_original >= 2 and time.time() - last_attempt >= 300:
                    assert attempts < 3, (
                        "Reapplication limit reached; refusing repeated interruptions"
                    )
                    attempts += 1
                    last_attempt = time.time()
                    report("applying", attempt=attempts)
                    in_transaction = True
                    result = transaction("apply")
                    assert current() == CANDIDATE
                    in_transaction = False
                    report(
                        "active; verified candidate",
                        edid_sha256=CANDIDATE,
                        applications_this_boot=attempts,
                        result=result,
                    )
                    last_state = "active"
                    stable_original = 0
            else:
                raise RuntimeError(
                    "Unexpected HDMI1 EDID; refusing to overwrite " + digest
                )
    except Exception as e:
        # Startup/readiness errors may settle; a native transaction failure must not loop.
        if in_transaction or isinstance(e, RuntimeError):
            if os.path.exists(BASE + "/enabled"):
                os.unlink(BASE + "/enabled")
            report(
                "disabled after error; inspect result before re-enabling", error=str(e)
            )
            break
        if last_state != "not-ready":
            report("waiting for services", error=str(e))
            last_state = "not-ready"
    # Short waits allow disable to stop the daemon promptly, even in standby.
    for unused in range(150):
        if not os.path.isfile(BASE + "/enabled"):
            break
        time.sleep(0.2)

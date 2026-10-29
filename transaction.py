from __future__ import print_function
import os, sys, json, hashlib, fcntl, time, binascii
import socket, struct, select, glob, ctypes, re, signal, subprocess
from state import (
    verify_firmware,
    load_backup,
    luna as read_state,
    stop_unfinished_controller,
)
from edid import build_candidate, require
import profiles
import storage
import uuid
from coordinator import native_quiescent, boot_id

# Reject HDMI 2 before firmware, process or console access.
require(
    len(sys.argv) == 3 and sys.argv[1] in ["apply", "restore", "trial71"],
    "Internal usage: transaction.py apply|restore|trial71 PORT",
)
action = sys.argv[1]
PORT = int(sys.argv[2])
require(
    PORT in profiles.PORTS, "HDMI 2 is reserved for eARC; only 1, 3 and 4 are writable"
)
verify_firmware()
__import__("state").no_competing_service()
backup = load_backup()
operation = storage.read_json(__import__("state").BASE + "/operation.json")
require(
    operation["id"] == os.environ.get("C9_EDID_OPERATION")
    and operation["backup_id"] == profiles.backup_id(backup)
    and operation.get("inflight_port") == PORT
    and operation["owner_pid"] == os.getppid()
    and operation["owner_started"]
    == open("/proc/%d/stat" % os.getppid()).read().rsplit(")", 1)[1].split()[19]
    and operation["phase"] in ["applying", "recovering"],
    "Native write requires the active coordinated journal",
)
BASE = __import__("state").BASE
policy = profiles.experimental_policy(backup, operation)
profiles.validate_map(backup, operation["before"])
profiles.validate_map(backup, operation["after"], policy)
require(
    native_quiescent(BASE, backup, boot_id()), "Previous console session needs recovery"
)
lockfd = os.open("/tmp/c9-hdmi1-audio.transaction.lock", os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)

manager = int(open("/tmp/dbgfrwk/tmgr_pid").read())
assert open("/proc/%d/comm" % manager).read().strip() == "lginit_app2"
master, slave = os.openpty()
term = os.ttyname(slave)
assert term.startswith("/dev/pts/")
seq = 0


def send(cmd):
    global seq
    assert cmd in [
        "tmgr elogin " + term,
        "tmgr elogout",
        "tmgr ejobs",
        "tmgr efg 1",
        "tmgr stdout unlock",
        "tmgr stdout lock",
    ]
    seq += 1
    name = "%d_to_%d_attach%d" % (os.getpid(), manager, seq)
    path = "/tmp/dbgfrwk/msg/" + name
    assert not os.path.exists(path)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    reply = b""
    output = b""
    peer = None
    try:
        listener.bind(path)
        listener.listen(1)
        fields = [str(manager), term, name, cmd]
        packet = (
            fields[0].ljust(256, "\0")
            + struct.pack("<I", 0)
            + fields[1].ljust(256, "\0")
            + fields[2].ljust(256, "\0")
            + struct.pack("<I", 0)
            + fields[3].ljust(256, "\0")
        )
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(2)
        client.connect("/tmp/dbgfrwk/msg/%d" % manager)
        client.sendall(packet)
        client.close()
        deadline = time.time() + 4
        while time.time() < deadline:
            ready, _, _ = select.select(
                [master, listener] + ([peer] if peer else []), [], [], 0.05
            )
            if master in ready:
                chunk = os.read(master, 8192)
                if len(output) < 60000:
                    output += chunk
            if listener in ready and peer is None:
                peer, _ = listener.accept()
            if peer and peer in ready:
                chunk = peer.recv(1032 - len(reply))
                if not chunk:
                    break
                reply += chunk
                if len(reply) == 1032:
                    break
        return {
            "command": cmd,
            "reply_bytes": len(reply),
            "return_code": struct.unpack_from("<i", reply, 772)[0]
            if len(reply) >= 776
            else None,
            "console": output.decode("utf-8", "replace"),
        }
    finally:
        if peer:
            peer.close()
        listener.close()
        if os.path.exists(path):
            os.unlink(path)


out = {"read_only_hardware": True, "terminal": term, "queries": []}


class IOVec(ctypes.Structure):
    _fields_ = [("base", ctypes.c_void_p), ("length", ctypes.c_size_t)]


clib = ctypes.CDLL(None, use_errno=True)
for fn in ["process_vm_readv", "process_vm_writev"]:
    f = getattr(clib, fn)
    f.argtypes = [
        ctypes.c_int,
        ctypes.POINTER(IOVec),
        ctypes.c_ulong,
        ctypes.POINTER(IOVec),
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    f.restype = ctypes.c_ssize_t


def identity(pid):
    return open("/proc/%d/stat" % pid).read().rsplit(")", 1)[1].split()[19]


def readword(pid, address):
    value = ctypes.c_uint32()
    local = IOVec(ctypes.addressof(value), 4)
    remote = IOVec(address, 4)
    assert (
        clib.process_vm_readv(pid, ctypes.byref(local), 1, ctypes.byref(remote), 1, 0)
        == 4
    )
    return value.value


def librarybase(pid):
    maps = [
        l.split()
        for l in open("/proc/%d/maps" % pid)
        if "libdbgfrwk.so." in l and l.split()[2] == "00000000"
    ]
    assert len(maps) == 1
    return int(maps[0][0].split("-")[0], 16)


ram_records = []


def record(pid, address, label):
    assert any(
        "w" in s[1]
        and int(s[0].split("-")[0], 16) <= address
        and address + 4 <= int(s[0].split("-")[1], 16)
        for s in [l.split() for l in open("/proc/%d/maps" % pid)]
    )
    r = {
        "pid": pid,
        "identity": identity(pid),
        "address": address,
        "before": readword(pid, address),
        "label": label,
    }
    ram_records.append(r)
    return r


def writeword(r, value):
    assert r in ram_records and identity(r["pid"]) == r["identity"]
    assert value == r["before"] or (r["label"] == "console_mode" and value == 4)
    v = ctypes.c_uint32(value)
    local = IOVec(ctypes.addressof(v), 4)
    remote = IOVec(r["address"], 4)
    assert (
        clib.process_vm_writev(
            r["pid"], ctypes.byref(local), 1, ctypes.byref(remote), 1, 0
        )
        == 4
    )
    assert readword(r["pid"], r["address"]) == value


def restore_ram():
    result = []
    for r in ram_records:
        try:
            writeword(r, r["before"])
            result.append({"label": r["label"], "pid": r["pid"], "restored": True})
        except Exception as e:
            result.append(
                {
                    "label": r["label"],
                    "pid": r["pid"],
                    "restored": False,
                    "error": str(e),
                }
            )
    return result


def inject_complete_function_key(fd, data, tvpid):
    # The native escape parser only consumes bytes already buffered by libc.
    # Briefly stop only the blocked keyboard reader, enqueue the entire F10 key,
    # then detach. All other TV threads continue; no registers/code are changed.
    assert data == b"\x1b[21~"
    candidates = []
    for path in glob.glob("/proc/%d/task/*/syscall" % tvpid):
        try:
            values = open(path).read().split()
            if values[:2] == ["3", "0x0"]:
                candidates.append(int(path.split("/")[-2]))
        except IOError:
            pass
    assert len(candidates) == 1, candidates
    tid = candidates[0]
    assert "TracerPid:\t0" in open("/proc/%d/status" % tid).read()
    pt = clib.ptrace
    pt.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
    pt.restype = ctypes.c_long
    attached = False
    stopped = False
    start = time.time()

    def call(op):
        result = pt(op, tid, 0, 0)
        assert result != -1, (op, ctypes.get_errno())

    try:
        call(0x4206)
        attached = True  # PTRACE_SEIZE, no EXITKILL
        call(0x4207)  # PTRACE_INTERRUPT
        deadline = time.time() + 1
        while time.time() < deadline:
            p, status = os.waitpid(tid, 0x40000001)
            if p:
                assert os.WIFSTOPPED(status)
                stopped = True
                break
            time.sleep(0.001)
        assert stopped, "keyboard stop timeout"
        for c in data:
            fcntl.ioctl(fd, 0x5412, c)
    finally:
        if attached:
            if not stopped:
                call(0x4207)
                os.waitpid(tid, 0x40000000)
            call(17)  # PTRACE_DETACH, original registers and signal unchanged
    assert "TracerPid:\t0" in open("/proc/%d/status" % tid).read()
    out.setdefault("complete_key_injections", []).append(
        {
            "tid": tid,
            "detached": True,
            "duration_seconds": time.time() - start,
            "register_or_memory_writes": False,
        }
    )


def key(data):
    pids = []
    for p in glob.glob("/proc/[0-9]*/comm"):
        try:
            if open(p).read().strip() == "tvservice":
                pids.append(int(p.split("/")[2]))
        except IOError:
            pass
    assert len(pids) == 1
    path = "/proc/%d/fd/0" % pids[0]
    assert os.readlink(path) == "/dev/pts/1"
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
    try:
        if data == b"\x1b[21~":
            inject_complete_function_key(fd, data, pids[0])
        else:
            for c in data:
                fcntl.ioctl(fd, 0x5412, c)
    finally:
        os.close(fd)


def collect(seconds):
    data = b""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if select.select([master], [], [], 0.05)[0]:
            chunk = os.read(master, 8192)
            if len(data) < 100000:
                data += chunk
    return data.decode("utf-8", "replace")


ORIGINAL = binascii.unhexlify(backup["edids"][str(PORT)])
CANDIDATE = build_candidate(ORIGINAL)
ALLOWED = [ORIGINAL, CANDIDATE]
if policy == profiles.TRIAL_PROFILE and PORT == 1:
    ALLOWED.append(build_candidate(ORIGINAL, channels=8))
EXPECTED_PRESERVATION = backup["preservation"]
for data in ALLOWED:
    assert (
        len(data) == 256
        and sum(bytearray(data[:128])) % 256 == 0
        and sum(bytearray(data[128:])) % 256 == 0
    )
out["read_only_hardware"] = False
parent_pid = os.getpid()
parent_started = identity(parent_pid)
transaction_id = uuid.uuid4().hex
guard = BASE + "/transactions/" + transaction_id
storage.directory(BASE + "/transactions")
assert not os.path.exists(("/tmp/edid_%d.bin" % PORT)), (
    "Existing native EDID file; refusing to overwrite"
)
os.mkdir(guard, 0o700)


def putjson(name, value):
    storage.write_json(guard + "/" + name, value)


def luna(uri, payload):
    assert uri in [
        "com.webos.service.tv.systemproperty/getEdidData",
        "com.webos.service.tv.systemproperty/getWhiteBalance",
        "com.webos.service.tv.systemproperty/getProperties",
        "com.webos.settingsservice/getSystemSettings",
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


def edid(port):
    r = luna(
        "com.webos.service.tv.systemproperty/getEdidData",
        {"inputType": 0, "portNumber": port},
    )["edidData"]
    assert r["length"] == 256
    return "".join(chr(b) for b in r["data"])


def all_edids():
    return {str(p): edid(p).encode("hex") for p in range(1, 5)}


def preserved():
    hashes = {
        p: hashlib.sha256(open(p, "rb").read()).hexdigest()
        for p in sorted(
            glob.glob("/mnt/lg/cmn_data/external_pqldb/*")
            + glob.glob("/mnt/lg/cmn_data/dolby/*")
        )
        if os.path.isfile(p)
    }
    assert len(hashes) == 29
    wb = {
        t: luna(
            "com.webos.service.tv.systemproperty/getWhiteBalance", {"colorTemp": t}
        )["setting"]
        for t in ["cool", "medium", "warm"]
    }
    oled = luna(
        "com.webos.service.tv.systemproperty/getProperties",
        {"keys": ["OledTPC", "OledGSR"]},
    )
    pic = luna(
        "com.webos.settingsservice/getSystemSettings",
        {"category": "picture", "keys": ["screenShift", "logoLuminanceAdjust"]},
    )["settings"]
    return {
        "calibration_hashes": hashes,
        "white_balance": wb,
        "oled": {k: oled[k] for k in ["OledTPC", "OledGSR"]},
        "picture_protections": pic,
    }


before_edids = all_edids()
profiles.validate_map(backup, before_edids, policy)
BEFORE_TARGET = binascii.unhexlify(before_edids[str(PORT)])
before_preserved = preserved()
assert before_preserved == EXPECTED_PRESERVATION, (
    "Calibration/protection baseline changed; no write permitted"
)
putjson("before.json", {"edids": before_edids, "preservation": before_preserved})
with open(guard + "/original.bin", "wb") as f:
    f.write(ORIGINAL)
with open(guard + "/candidate.bin", "wb") as f:
    f.write(CANDIDATE)


def file_for_load(data):
    assert data in ALLOWED
    path = "/tmp/edid_%d.bin" % PORT
    # The journal is persistent but /tmp is a different filesystem on the TV.
    # Stage beside the destination; renaming out of guard would fail with EXDEV.
    storage.atomic_bytes(path, data)
    assert open(path, "rb").read() == data


def menu_load(data, label):
    # Only menu 5 and an allowlisted logical input; no HDMI2 file or command.
    assert readword(mode["pid"], mode["address"]) == 5
    file_for_load(data)
    key(b"5\n")
    prompt = collect(0.4)
    assert "Enter HDMI port(1~4)" in prompt, (label, prompt[-600:])
    key(("%d\n" % PORT).encode("ascii"))
    reply = collect(1)
    actual = edid(PORT)
    tail = collect(0.2)
    log = {
        "label": label,
        "prompt": prompt,
        "reply": reply + tail,
        "expected_sha256": hashlib.sha256(data).hexdigest(),
        "readback_sha256": hashlib.sha256(actual).hexdigest(),
        "match": actual == data,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    putjson(label + ".json", log)
    assert actual == data, log
    return log


def enter_edid():
    # Console eligibility changes in RAM only, restored before any EDID load.
    writeword(mode, 4)
    key(b"\n")
    collect(0.5)
    key(b"debug\n")
    a = collect(1)
    key(b"\x1b[21~")
    b = collect(2)
    assert "debugMain" in b, (a[-300:], b[-300:])
    writeword(mode, mode["before"])
    key(b"orgm f1\n")
    m = collect(0.6)
    assert "[EDID DEBUG MENU]" in m, m[-600:]
    return {"product_exit": a, "debug_menu": b, "edid_menu": m}


def leave_console():
    writeword(mode, mode["before"])
    # Two ff exits also cover a pending port prompt (255 has no input file).
    key(b"ff\nff\nexit\nexit\nx\n")
    collect(0.5)
    status = restore_ram()
    detach = send("tmgr elogout")
    if os.path.exists(("/tmp/edid_%d.bin" % PORT)):
        assert open(("/tmp/edid_%d.bin" % PORT), "rb").read() in ALLOWED
        os.unlink(("/tmp/edid_%d.bin" % PORT))
    return {
        "ram_restoration": status,
        "detach": detach,
        "runtime_mode_after": readword(mode["pid"], mode["address"]),
    }


TARGET = profiles.decode(operation[
    "after" if operation["phase"] == "applying" or operation.get("decision") == "commit"
    else "before"][str(PORT)])
require(TARGET in ALLOWED and action == profiles.native_action(backup, PORT, profiles.hexbytes(TARGET), policy),
        "Native action differs from coordinated target")
expected_after = dict(before_edids)
expected_after[str(PORT)] = TARGET.encode("hex")
require(
    operation[
        "after"
        if operation["phase"] == "applying" or operation.get("decision") == "commit"
        else "before"
    ][str(PORT)]
    == TARGET.encode("hex"),
    "Target differs from coordinated intent",
)
if before_edids == expected_after:
    print(
        json.dumps(
            {
                "status": "Already in requested state",
                "action": action,
                "verified": True,
                "guard_directory": guard,
                "edid_sha256": hashlib.sha256(TARGET).hexdigest(),
                "port": PORT,
                "no_hardware_write": True,
            }
        )
    )
    os.close(master)
    os.close(slave)
    os.close(lockfd)
    sys.exit(0)
worker_started = False
mode = None
storage.write_json(
    BASE + "/native-pending.json",
    {
        "backup_id": profiles.backup_id(backup),
        "transaction_id": transaction_id,
        "boot_id": boot_id(),
        "port": PORT,
        "controller_pid": parent_pid,
        "controller_started": parent_started,
    },
)
try:
    out["queries"].append(send("tmgr elogin " + term))
    assert out["queries"][-1]["reply_bytes"] == 1032
    jobs_reply = send("tmgr ejobs")
    out["queries"].append(jobs_reply)
    jobs = re.findall(r"name=([^ ]+) pid=([0-9]+)", jobs_reply["console"])
    tvs = [int(p) for n, p in jobs if n == "tvservice"]
    assert len(tvs) == 1
    mode = record(tvs[0], 0x018ACE0C, "console_mode")
    assert mode["before"] == 5
    for name, p in jobs:
        p = int(p)
        ptr = readword(p, librarybase(p) + 0x55FDC)
        rec = record(p, ptr, "print_" + name)
        assert rec["before"] in [0, 1]
    record(manager, librarybase(manager) + 0x58D98, "manager_output")
    putjson("console-original.json", ram_records)
    worker = os.fork()
    if worker == 0:
        os.setsid()
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        null = os.open("/dev/null", os.O_RDWR)
        for d in [0, 1, 2]:
            os.dup2(null, d)
        result = {
            "independent_tv_local_worker": True,
            "action": action,
            "parent_pid": parent_pid,
            "boot_id": open("/proc/sys/kernel/random/boot_id").read().strip(),
        }
        received = None
        try:
            deadline = time.time() + 45
            while time.time() < deadline:
                if os.path.exists(guard + "/handoff.json"):
                    received = json.load(open(guard + "/handoff.json"))
                    break
                time.sleep(0.1)
            time.sleep(0.2)
            result["parent_exited"] = os.getppid() != parent_pid
            if not received or not received.get("target_verified"):
                stop_unfinished_controller(parent_pid, parent_started)
            assert received and received.get("target_verified"), (
                "No verified controller handoff"
            )
            assert all_edids() == expected_after and all_edids() == expected_after, (
                "EDID changed before commit"
            )
            assert preserved() == before_preserved, (
                "Calibration/protection changed before commit"
            )
            result["console_cleanup"] = leave_console()
            cleanup = result["console_cleanup"]
            assert (
                cleanup["runtime_mode_after"] == 5
                and cleanup["detach"]["reply_bytes"] == 1032
                and all(x["restored"] for x in cleanup["ram_restoration"])
            )
            assert all_edids() == expected_after and preserved() == before_preserved, (
                "Post-cleanup verification failed"
            )
            result.update(
                {
                    "committed": True,
                    "verified": True,
                    "all_other_inputs_unchanged": True,
                    "calibration_and_OLED_unchanged": True,
                    "edid_sha256": hashlib.sha256(TARGET).hexdigest(),
                }
            )
        except Exception as e:
            result["error"] = str(e)
            result["committed"] = False
            # Restore this input's preimage, including an already working override.
            try:
                send("tmgr elogin " + term)
                send("tmgr efg 1")
                send("tmgr stdout unlock")
                key(b"ff\nff\nexit\nexit\nx\n")
                collect(0.6)
                enter_edid()
                result["rollback_load"] = menu_load(
                    BEFORE_TARGET, "failed-transaction-restore-preimage"
                )
                result["before_restored"] = (
                    all_edids() == before_edids and all_edids() == before_edids
                )
            except Exception as rollback_error:
                result["rollback_error"] = str(rollback_error)
            finally:
                try:
                    result["rollback_console_cleanup"] = leave_console()
                except Exception as cleanup_error:
                    result["cleanup_error"] = str(cleanup_error)
                try:
                    result["calibration_and_OLED_unchanged"] = (
                        preserved() == before_preserved
                    )
                except Exception as preserve_error:
                    result["preservation_error"] = str(preserve_error)
        finally:
            result["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            try:
                putjson("result.json", result)
            finally:
                os.close(master)
                os.close(slave)
                os.close(lockfd)
        os._exit(0)
    worker_started = True
    out["watchdog"] = {"pid": worker, "timeout_seconds": 45, "guard_directory": guard}
    send("tmgr efg 1")
    send("tmgr stdout unlock")
    out["menu_entry"] = enter_edid()
    if action in ("apply", "trial71"):
        assert (
            read_state("com.webos.service.tvpower/power/getPowerState", {})["state"]
            == "Active"
        ), "TV is no longer active"
    out["target"] = menu_load(TARGET, "target")
    assert all_edids() == expected_after
    assert preserved() == before_preserved
    # Leave this input's preimage in place until the worker commits/cleans up.
    file_for_load(BEFORE_TARGET)
    putjson("controller.json", out)
    putjson(
        "handoff.json",
        {"target_verified": True, "target_sha256": hashlib.sha256(TARGET).hexdigest()},
    )
    print(
        json.dumps(
            {
                "status": "Target verified; independent commit pending",
                "action": action,
                "guard_directory": guard,
                "worker_pid": worker,
                "transaction_id": transaction_id,
                "port": PORT,
            }
        )
    )
except Exception as e:
    try:
        putjson("controller-error.json", {"error": str(e), "details": out})
    except Exception:
        pass
    if worker_started:
        putjson("handoff.json", {"target_verified": False, "error": str(e)})
    else:
        if mode:
            leave_console()
        else:
            send("tmgr elogout")
    print(
        json.dumps(
            {
                "status": "Failed; preimage recovery requested",
                "guard_directory": guard,
                "error": str(e),
            }
        )
    )
finally:
    os.close(master)
    os.close(slave)
    os.close(lockfd)

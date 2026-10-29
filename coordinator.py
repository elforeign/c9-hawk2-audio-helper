"""One serialized manager, durable preimages and bounded per-port trials."""

from __future__ import print_function
import json
import errno
import os
import re
import subprocess
import signal
import time
import uuid
import profiles
import state
import storage
from edid import require

PHASES = (
    "prepared",
    "applying",
    "trial-active",
    "recovering",
    "committed",
    "rolled-back",
    "recovery-required",
)


def verify_owner():
    state.verify_firmware()
    state.no_competing_service()


def boot_id():
    with open("/proc/sys/kernel/random/boot_id") as source:
        return source.read().strip()


def process_identity():
    with open("/proc/%d/stat" % os.getpid()) as source:
        return source.read().rsplit(")", 1)[1].split()[19]


def monotonic():
    with open("/proc/uptime") as source:
        return float(source.read().split()[0])


def run_controller(command, environment, seconds=65):
    # The C9's minimal Python has signal and subprocess, but no threading.
    # The controller remains a direct child, preserving journal ownership.
    previous_handler = signal.getsignal(signal.SIGALRM)
    pending = signal.alarm(0)
    if pending:
        signal.alarm(pending)
        raise ValueError("An existing alarm owns the controller timeout")
    process = [None]

    def expired(signum, frame):
        try:
            process[0].kill()
        except OSError as error:
            if error.errno != errno.ESRCH:
                raise

    signal.signal(signal.SIGALRM, expired)
    try:
        process[0] = subprocess.Popen(command, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
        signal.alarm(seconds)
        output, error = process[0].communicate()
        return process[0].returncode, output, error
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def configuration(ports):
    return {
        "format_version": 1,
        "profiles": {str(p): profiles.PROFILE for p in profiles.parse_ports(ports)},
    }


def validate_configuration(value):
    require(
        value.get("format_version") == 1 and isinstance(value.get("profiles"), dict),
        "Unknown selection format",
    )
    ports = profiles.parse_ports(value["profiles"].keys())
    require(
        all(v == profiles.PROFILE for v in value["profiles"].values()),
        "Unqualified audio profile",
    )
    return configuration(ports)


def clean_console(result):
    return bool(
        result
        and result.get("runtime_mode_after") == 5
        and result.get("detach", {}).get("reply_bytes") == 1032
        and result.get("ram_restoration")
        and all(r.get("restored") for r in result["ram_restoration"])
    )


def native_quiescent(root, backup, current_boot):
    path = root + "/native-pending.json"
    if not os.path.exists(path):
        return True
    pending = storage.read_json(path)
    require(
        pending.get("backup_id") == profiles.backup_id(backup),
        "Native recovery belongs to another backup",
    )
    require(pending.get("port") in profiles.PORTS, "Invalid native recovery port")
    token = pending.get("transaction_id", "")
    require(re.match(r"^[0-9a-f]{32}\Z", token), "Invalid native recovery identifier")
    # Old PIDs, addresses and terminal state are never replayed after reboot.
    if pending.get("boot_id") != current_boot:
        return True
    result_path = root + "/transactions/" + token + "/result.json"
    if not os.path.isfile(result_path):
        return False
    result = storage.read_json(result_path)
    return bool(
        (
            result.get("committed")
            and result.get("verified")
            and clean_console(result.get("console_cleanup"))
        )
        or (
            result.get("before_restored")
            and result.get("calibration_and_OLED_unchanged")
            and clean_console(result.get("rollback_console_cleanup"))
        )
    )


class Engine(object):
    def __init__(
        self,
        backup=None,
        root=None,
        read=None,
        preserved=None,
        backend=None,
        verify=None,
        active=None,
        boot=None,
        identity=None,
        clock=None,
        sleep=None,
        fault=None,
        recovery=None,
    ):
        self.root = root or state.BASE
        self.backup = profiles.normalize_backup(backup or state.load_backup())
        self.read = read or state.read_edids
        self.preserved = preserved or state.preserved
        self.backend = backend or self._native
        self.verify = verify or verify_owner
        self.active = active or (
            lambda: state.luna(state.READ_METHODS[5], {})["state"] == "Active"
        )
        self.boot = boot or boot_id
        self.identity = identity or process_identity
        self.clock = clock or monotonic
        self.sleep = sleep or time.sleep
        self.fault = fault or (lambda point: None)
        self.ensure_recovery = recovery or self._ensure_recovery
        storage.directory(self.root)
        storage.directory(self.root + "/history")
        storage.directory(self.root + "/transactions")
        self.path = self.root + "/operation.json"

    def _ensure_recovery(self):
        # Import after coordinator initialization: the monitor uses Engine too.
        __import__("service").ensure_recovery(self.root, self.backup)

    def selection(self):
        path = self.root + "/selection.json"
        return (
            validate_configuration(storage.read_json(path))
            if os.path.exists(path)
            else configuration([1])
        )

    def pending(self):
        try:
            return storage.read_json(self.path)
        except (IOError, OSError) as error:
            if error.errno == errno.ENOENT:
                return None
            raise

    def _check(self, operation=None):
        self.verify()
        require(
            self.preserved() == self.backup["preservation"],
            "Calibration/OLED baseline changed; no write permitted",
        )
        return profiles.validate_map(self.backup, self.read(),
                                     profiles.experimental_policy(self.backup, operation))

    def _save(self, operation, phase):
        operation["phase"] = phase
        storage.write_json(self.path, operation)
        self.fault(phase)

    def _archive(self, operation):
        destination = self.root + "/history/" + operation["id"] + ".json"
        require(not os.path.lexists(destination), "Recovery history already exists")
        os.rename(self.path, destination)
        storage.sync_directory(self.root + "/history")
        storage.sync_directory(self.root)

    def _validated(self, operation):
        require(
            operation.get("format_version") == 1
            and operation.get("backup_id") == profiles.backup_id(self.backup),
            "Recovery journal does not match immutable backup",
        )
        require(
            re.match(r"^[0-9a-f]{32}\Z", operation.get("id", "")),
            "Invalid operation identifier",
        )
        require(operation.get("phase") in PHASES, "Unknown recovery phase")
        require(
            operation.get("decision") in ("commit", "rollback"),
            "Unknown recovery decision",
        )
        profiles.validate_map(self.backup, operation["before"])
        policy = profiles.experimental_policy(self.backup, operation)
        profiles.validate_map(self.backup, operation["after"], policy)
        validate_configuration(operation["config_before"])
        validate_configuration(operation["config_after"])
        return operation

    def _native_safe(self):
        require(
            native_quiescent(self.root, self.backup, self.boot()),
            "Prior native console session is unfinished on this boot; wait for its result or restart and recover",
        )

    def _write_map(self, target, operation):
        self._validated(operation)
        policy = profiles.experimental_policy(self.backup, operation)
        profiles.validate_map(self.backup, target, policy)
        expected = self._check(operation)
        if expected != target:
            self.ensure_recovery()
        for port in profiles.PORTS:
            key = str(port)
            if expected[key] == target[key]:
                continue
            require(self.active(), "TV must be Active; recovery remains journaled")
            self._native_safe()
            require(self._check(operation) == expected, "An input changed during the operation")
            operation["inflight_port"] = port
            operation["owner_pid"] = os.getpid()
            operation["owner_started"] = self.identity()
            storage.write_json(self.path, operation)
            self.fault("before-write-%d" % port)
            self.backend(
                profiles.native_action(self.backup, port, target[key], policy),
                port,
                operation["id"],
            )
            expected = dict(expected)
            expected[key] = target[key]
            require(
                self._check(operation) == expected and self.read() == expected,
                "Native write/readback mismatch",
            )
            operation["inflight_port"] = None
            storage.write_json(self.path, operation)
            self.fault("after-write-%d" % port)
        require(
            self._check(operation) == target and self.read() == target,
            "Final input preservation check failed",
        )

    def _recover(self):
        operation = self.pending()
        if not operation:
            self._native_safe()
            return None
        self._validated(operation)
        self._native_safe()
        self._check(operation)
        committed = operation["decision"] == "commit"
        if (
            not committed
            and operation["action"] == "apply"
            and os.path.exists(self.root + "/enabled")
        ):
            os.unlink(self.root + "/enabled")
            storage.sync_directory(self.root)
        target = operation["after"] if committed else operation["before"]
        config = operation["config_after"] if committed else operation["config_before"]
        self._save(operation, "recovering")
        try:
            self._write_map(target, operation)
            storage.write_json(self.root + "/selection.json", config)
            self._save(operation, "committed" if committed else "rolled-back")
            self._archive(operation)
            return {
                "state": "committed inputs restored"
                if committed
                else "previous working inputs restored",
                "verified": True,
            }
        except Exception as error:
            operation["error"] = str(error)
            self._save(operation, "recovery-required")
            raise

    def recover(self):
        with storage.lock(self.root + "/operation.lock"):
            return self._recover()

    def select(self, ports):
        ports = profiles.parse_ports(ports)
        with storage.lock(self.root + "/operation.lock"):
            return self._select(ports)

    def _select(self, ports):
        # Caller owns operation.lock, including the installer during code replacement.
        require(
            not os.path.exists(self.root + "/enabled"),
            "Disable before changing the selection; use restore for a single active input",
        )
        require(not self.pending(), "Recover pending work before changing selection")
        self._native_safe()
        current = self._check()
        built = profiles.candidates(self.backup)
        # An explicitly adopted HDMI1 profile can remain the working baseline.
        for port in profiles.PORTS:
            require(
                current[str(port)] == self.backup["edids"][str(port)]
                or (port in ports and current[str(port)] == built[str(port)]),
                "Restore an input before removing it from the selection",
            )
        storage.write_json(self.root + "/selection.json", configuration(ports))
        return {"state": "selection saved; no hardware write", "ports": ports}

    def run(self, action, ports=None, seconds=180, trial_profile=profiles.PROFILE):
        require(action in ("apply", "restore", "trial", "disable"), "Unknown operation")
        chosen = profiles.parse_ports(ports if ports is not None else profiles.PORTS)
        require(trial_profile == profiles.PROFILE or
                (action == "trial" and chosen == [1] and trial_profile == profiles.TRIAL_PROFILE),
                "Experimental PCM 7.1 is trial-only on HDMI 1")
        require(
            action != "trial"
            or (len(chosen) == 1 and type(seconds) is int and 1 <= seconds <= 180),
            "Trial needs one input and 1..180 seconds",
        )
        with storage.lock(self.root + "/operation.lock"):
            self._recover()
            before = self._check()
            config_before = self.selection()
            config_after = config_before
            after = dict(before)
            if action == "apply":
                after = profiles.selected_map(
                    self.backup, config_before["profiles"].keys()
                )
            elif action in ("restore", "disable"):
                for port in chosen:
                    after[str(port)] = self.backup["edids"][str(port)]
                if action == "restore":
                    config_after = configuration(
                        [
                            p
                            for p in profiles.parse_ports(
                                config_before["profiles"].keys()
                            )
                            if p not in chosen
                        ]
                    )
            else:
                after[str(chosen[0])] = profiles.trial_candidate(self.backup, chosen[0], trial_profile)
            if action == "trial":
                require(
                    not os.path.exists(self.root + "/disable-requested"),
                    "Disable is pending",
                )
                if os.path.exists(self.root + "/trial-stop"):
                    os.unlink(self.root + "/trial-stop")
                    storage.sync_directory(self.root)
            operation = {
                "format_version": 1,
                "id": uuid.uuid4().hex,
                "backup_id": profiles.backup_id(self.backup),
                "action": action,
                "before": before,
                "after": after,
                "config_before": config_before,
                "config_after": config_after,
                "boot_id": self.boot(),
                "owner_pid": os.getpid(),
                "owner_started": self.identity(),
                "inflight_port": None,
                "decision": "rollback",
            }
            if trial_profile == profiles.TRIAL_PROFILE:
                operation.update(trial_profile=trial_profile, trial_port=1)
            self._validated(dict(operation, phase="prepared"))
            self._save(operation, "prepared")
            try:
                # Even a no-op trial needs independent recovery if its owner
                # dies while holding the trial timer with startup disabled.
                self.ensure_recovery()
                self._save(operation, "applying")
                self._write_map(after, operation)
                if action == "trial":
                    self._save(operation, "trial-active")
                    deadline = self.clock() + seconds
                    while self.clock() < deadline:
                        if os.path.exists(self.root + "/trial-stop") or os.path.exists(
                            self.root + "/disable-requested"
                        ):
                            break
                        self.sleep(min(0.2, max(0, deadline - self.clock())))
                    return self._recover()
                operation["decision"] = "commit"
                self._save(operation, "committed")
                storage.write_json(self.root + "/selection.json", config_after)
                self._archive(operation)
                return {
                    "state": "inputs verified",
                    "ports": profiles.parse_ports(config_after["profiles"].keys()),
                    "calibration_and_OLED_unchanged": True,
                    "hdmi2_unchanged": True,
                }
            except Exception:
                # A durable commit is not undone by a later archive/config I/O failure.
                pending = self.pending()
                if pending and pending.get("decision") != "commit":
                    self._recover()
                raise

    def _native(self, action, port, operation_id):
        require(port in profiles.PORTS, "HDMI 2 is reserved for eARC")
        env = dict(os.environ)
        env["C9_EDID_OPERATION"] = operation_id
        code, output, error = run_controller(
            ["/usr/bin/python", "-B", self.root + "/transaction.py", action, str(port)],
            env,
        )
        require(
            code == 0,
            "Native controller refused: "
            + (error or output).decode("utf8", "replace")[-1000:],
        )
        reply = json.loads(output)
        if reply.get("no_hardware_write"):
            require(reply.get("verified"), "Unverified native no-op")
            return
        token = reply["transaction_id"]
        require(re.match(r"^[0-9a-f]{32}\Z", token), "Invalid native transaction")
        result_path = self.root + "/transactions/" + token + "/result.json"
        deadline = self.clock() + 65
        while self.clock() < deadline:
            if os.path.isfile(result_path):
                result = storage.read_json(result_path)
                require(
                    result.get("committed")
                    and result.get("verified")
                    and clean_console(result.get("console_cleanup")),
                    "Native transaction failed; inspect persistent result",
                )
                return
            self.sleep(0.2)
        raise RuntimeError(
            "Native result not ready; do not start another console session"
        )

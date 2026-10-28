"""Explicit preparation, selection and bounded trials; no import-time TV access."""

from __future__ import print_function
import argparse
import json
import os
import signal
import subprocess
import sys
import coordinator
import profiles
import state
import storage
from edid import require

HOOK = "/var/lib/webosbrew/init.d/60-glasshouse-c9-edid"
FILES = (
    "state.py",
    "profiles.py",
    "storage.py",
    "coordinator.py",
    "edid.py",
    "transaction.py",
    "service.py",
    "control.py",
    "control",
)


def ports_argument(value):
    try:
        return profiles.parse_ports(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error))


def start_monitor():
    subprocess.check_call([HOOK])


def install(args):
    state.verify_firmware()
    state.no_competing_service()
    source = os.path.dirname(os.path.abspath(__file__))
    require(source != state.BASE, "Run installation from the separate source folder")
    require(
        not os.path.exists(state.BASE + "/enabled")
        and not os.path.exists(state.BASE + "/operation.json"),
        "Disable and recover before replacing installed code",
    )
    if not os.path.lexists(state.BASE):
        state.capture(args.adopt_hdmi1_original)
    else:
        require(
            not args.adopt_hdmi1_original,
            "Existing backup is immutable; adoption is capture-only",
        )
    engine = coordinator.Engine()
    with storage.lock(state.BASE + "/operation.lock"):
        require(
            not os.path.exists(state.BASE + "/enabled")
            and not engine.pending()
            and not os.path.exists(state.BASE + "/disable-requested"),
            "Disable and finish recovery before replacing installed code",
        )
        # Validate selection and native quiescence before replacing any code.
        result = engine._select(args.ports)
        for name in FILES:
            with open(source + "/" + name, "rb") as value:
                storage.atomic_bytes(state.BASE + "/" + name, value.read())
            os.chmod(state.BASE + "/" + name, 0o700)
        # Recovery is installed even for --prepare, not gated only on enabled.
        with open(source + "/60-glasshouse-c9-edid", "rb") as value:
            storage.atomic_bytes(HOOK, value.read())
        os.chmod(HOOK, 0o700)
        if args.enable:
            storage.atomic_bytes(state.BASE + "/enabled", b"")
    if args.enable:
        start_monitor()
        result = {
            "state": "startup enabled; verify status before playback",
            "ports": args.ports,
        }
    return result


def status():
    root = state.BASE
    result = {
        "enabled": os.path.isfile(root + "/enabled"),
        "pending_recovery": os.path.isfile(root + "/operation.json"),
        "disable_requested": os.path.isfile(root + "/disable-requested"),
    }
    if os.path.isfile(root + "/selection.json"):
        result["selection"] = coordinator.validate_configuration(
            storage.read_json(root + "/selection.json")
        )
    if result["pending_recovery"]:
        pending = storage.read_json(root + "/operation.json")
        result["operation"] = {
            key: pending.get(key) for key in ("id", "action", "phase", "inflight_port")
        }
    path = __import__("service").RUN + "/status.json"
    if os.path.isfile(path):
        result["monitor"] = storage.read_json(path)
    return result


def trial(engine, port, seconds, foreground=False):
    if foreground:
        return engine.run("trial", [port], seconds)
    pid = os.fork()
    if pid:
        return {
            "state": "independent trial worker started; wait for trial-active status",
            "pid": pid,
            "port": port,
            "seconds": seconds,
        }
    os.setsid()
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    null = os.open("/dev/null", os.O_RDWR)
    log = os.open(
        engine.root + "/trial-worker.log",
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    os.dup2(null, 0)
    os.dup2(log, 1)
    os.dup2(log, 2)
    os.close(null)
    os.close(log)
    try:
        result = engine.run("trial", [port], seconds)
        print(json.dumps(result))
        sys.stdout.flush()
        os._exit(0)
    except Exception as error:
        print(
            json.dumps(
                {
                    "error": str(error),
                    "recovery_journal_retained": bool(engine.pending()),
                }
            )
        )
        sys.stdout.flush()
        os._exit(1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action")
    prepare = commands.add_parser("install")
    mode = prepare.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--enable", dest="enable", action="store_true")
    prepare.add_argument("--ports", type=ports_argument, default=[1])
    prepare.add_argument("--adopt-hdmi1-original")
    select = commands.add_parser("select")
    select.add_argument("ports", type=ports_argument)
    restore = commands.add_parser("restore")
    restore.add_argument("ports", type=ports_argument)
    check = commands.add_parser("trial")
    check.add_argument("port", type=ports_argument)
    check.add_argument("--seconds", type=int, default=180)
    check.add_argument("--foreground", action="store_true")
    for name in ("enable", "disable", "recover", "status", "apply", "stop-trial"):
        commands.add_parser(name)
    args = parser.parse_args(argv)
    require(args.action, "A command is required")
    if args.action == "trial":
        require(
            len(args.port) == 1 and 1 <= args.seconds <= 180,
            "Trial needs one input and 1..180 seconds",
        )
    require(
        os.geteuid() == 0 and __debug__,
        "Run on the rooted TV without Python optimization",
    )
    if args.action == "install":
        return install(args)
    if args.action == "status":
        return status()
    if args.action == "disable":
        storage.directory(state.BASE)
        if os.path.isfile(state.BASE + "/enabled"):
            os.unlink(state.BASE + "/enabled")
        storage.write_json(state.BASE + "/disable-requested", {"requested": True})
        start_monitor()
        return {"state": "disable requested; wait for originals verified status"}
    engine = coordinator.Engine()
    if args.action == "select":
        return engine.select(args.ports)
    if args.action == "enable":
        state.verify_firmware()
        state.no_competing_service()
        require(
            not engine.pending()
            and not os.path.exists(state.BASE + "/disable-requested"),
            "Recover pending work before enabling",
        )
        storage.atomic_bytes(state.BASE + "/enabled", b"")
        start_monitor()
        return {"state": "startup enabled; wait for verified profiles"}
    if args.action == "stop-trial":
        storage.write_json(state.BASE + "/trial-stop", {"requested": True})
        return {"state": "trial restoration requested; inspect status"}
    if args.action == "recover":
        return engine.recover()
    if args.action == "restore":
        return engine.run("restore", args.ports)
    if args.action == "trial":
        return trial(engine, args.port[0], args.seconds, args.foreground)
    return engine.run("apply")


if __name__ == "__main__":
    try:
        print(json.dumps(main(), sort_keys=True))
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        sys.exit(1)

"""Explicit preparation, selection and bounded trials; no import-time TV access."""

from __future__ import print_function
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
    return profiles.parse_ports(value)


class Arguments(object):
    pass


def parse_arguments(argv):
    # C9 Python 2.7 omits argparse. Keep a small, strict command grammar;
    # reject flags/duplicates and HDMI 2 before any TV ownership check.
    require(argv, "A command is required")
    if argv[0] in ("-h", "--help"):
        print("control install --prepare|--enable [--ports 1,3,4] [--adopt-hdmi1-original FILE]\n"
              "control select|restore PORTS\ncontrol trial PORT [--seconds 1..180] [--foreground] [--profile pcm71-48k]\n"
              "control enable|disable|recover|status|apply|stop-trial")
        raise SystemExit(0)
    result = Arguments()
    result.action = argv[0]
    require(result.action in ("install", "select", "restore", "trial", "enable",
            "disable", "recover", "status", "apply", "stop-trial"), "Unknown command")
    result.ports, result.port = [1], []
    result.enable, result.prepare, result.foreground = False, False, False
    result.adopt_hdmi1_original, result.seconds = None, 180
    result.trial_profile = profiles.PROFILE
    allowed = {"install": ("--prepare", "--enable", "--ports", "--adopt-hdmi1-original"),
               "trial": ("--seconds", "--foreground", "--profile")}.get(result.action, ())
    seen, positional = set(), []
    tokens = list(argv[1:])
    while tokens:
        token = tokens.pop(0)
        if not token.startswith("--"):
            positional.append(token)
            continue
        pair = token.split("=", 1)
        flag = pair[0]
        require(flag in allowed and flag not in seen, "Unknown or duplicate option: " + flag)
        seen.add(flag)
        if flag in ("--prepare", "--enable", "--foreground"):
            require(len(pair) == 1, "Boolean option takes no value")
            setattr(result, flag[2:], True)
            continue
        if len(pair) == 2:
            value = pair[1]
        else:
            require(tokens and not tokens[0].startswith("--"), "Option value required: " + flag)
            value = tokens.pop(0)
        require(value, "Empty option value")
        if flag == "--ports":
            result.ports = ports_argument(value)
        elif flag == "--seconds":
            result.seconds = int(value)
        elif flag == "--profile":
            result.trial_profile = value
        else:
            result.adopt_hdmi1_original = value
    if result.action == "install":
        require(result.enable != result.prepare and not positional,
                "Choose exactly one of --prepare or --enable")
    elif result.action in ("select", "restore", "trial"):
        require(len(positional) == 1, "Exactly one input selection is required")
        if result.action == "trial":
            result.port = ports_argument(positional[0])
            require(len(result.port) == 1 and 1 <= result.seconds <= 180,
                    "Trial needs one input and 1..180 seconds")
            require(result.trial_profile == profiles.PROFILE or
                    (result.trial_profile == profiles.TRIAL_PROFILE and result.port == [1]),
                    "Experimental PCM 7.1 is trial-only on HDMI 1")
        else:
            result.ports = ports_argument(positional[0])
    else:
        require(not positional, "Unexpected command argument")
    return result


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


def trial(engine, port, seconds, foreground=False, trial_profile=profiles.PROFILE):
    if foreground:
        return engine.run("trial", [port], seconds, trial_profile=trial_profile)
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
        result = engine.run("trial", [port], seconds, trial_profile=trial_profile)
        print(json.dumps(result))
        sys.stdout.flush()
        os._exit(0)
    except Exception as error:
        trace = []
        frame = sys.exc_info()[2]
        while frame is not None and len(trace) < 16:
            trace.append({"file": os.path.basename(frame.tb_frame.f_code.co_filename),
                          "line": frame.tb_lineno})
            frame = frame.tb_next
        print(
            json.dumps(
                {
                    "error": str(error),
                    "trace": trace,
                    "recovery_journal_retained": bool(engine.pending()),
                }
            )
        )
        sys.stdout.flush()
        os._exit(1)


def main(argv=None):
    args = parse_arguments(sys.argv[1:] if argv is None else argv)
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
        return trial(engine, args.port[0], args.seconds, args.foreground, args.trial_profile)
    return engine.run("apply")


if __name__ == "__main__":
    try:
        print(json.dumps(main(), sort_keys=True))
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        sys.exit(1)

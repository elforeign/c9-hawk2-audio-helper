"""One monitor for selected inputs; recovery runs even with startup disabled."""

from __future__ import print_function
import errno
import os
import sys
import coordinator
import profiles
import state
import storage

RUN = "/tmp/glasshouse-c9-edid"


def report(value):
    storage.directory(RUN)
    value = dict(value, boot_id=coordinator.boot_id(), pid=os.getpid())
    storage.write_json(RUN + "/status.json", value)


def daemon():
    storage.directory(RUN)
    try:
        with storage.lock(RUN + "/daemon.lock"):
            return monitor(coordinator.Engine())
    except IOError as error:
        if error.errno in (errno.EACCES, errno.EAGAIN):
            return 0
        raise


def monitor(engine):
    attempts = 0
    last_attempt = -1000
    prior = None
    stable = 0
    last_status = None
    while (
        os.path.exists(engine.root + "/enabled")
        or os.path.isfile(engine.root + "/operation.json")
        or os.path.exists(engine.root + "/disable-requested")
    ):
        mutating = False
        try:
            if not engine.active():
                status = {"state": "waiting for Active TV; recovery retained"}
                prior, stable = None, 0
            elif os.path.isfile(engine.root + "/operation.json"):
                mutating = True
                result = engine.recover()
                status = {"state": "recovery verified", "result": result}
                prior, stable = None, 0
            elif os.path.exists(engine.root + "/disable-requested"):
                mutating = True
                result = engine.run("disable")
                os.unlink(engine.root + "/disable-requested")
                storage.sync_directory(engine.root)
                status = {"state": "disabled; originals verified", "result": result}
            elif os.path.exists(engine.root + "/enabled"):
                current = profiles.validate_map(engine.backup, engine.read())
                desired = profiles.selected_map(
                    engine.backup, engine.selection()["profiles"].keys()
                )
                if current == desired:
                    status = {
                        "state": "selected profiles verified",
                        "profiles": engine.selection()["profiles"],
                    }
                    prior, stable = None, 0
                else:
                    stable = stable + 1 if prior == current else 1
                    prior = current
                    status = {
                        "state": "waiting for two stable input reads",
                        "stable_reads": stable,
                    }
                    if stable >= 2 and engine.clock() - last_attempt >= 300:
                        if attempts >= 3:
                            raise RuntimeError(
                                "Three application cycles reached; manual review required"
                            )
                        attempts += 1
                        last_attempt = engine.clock()
                        mutating = True
                        result = engine.run("apply")
                        status = {
                            "state": "selected profiles verified",
                            "profiles": engine.selection()["profiles"],
                            "application_cycles_this_run": attempts,
                            "result": result,
                        }
                        prior, stable = None, 0
            else:
                break
            if status != last_status:
                report(status)
                last_status = status
        except IOError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            # A detached trial owns the operation lock. Do not disable it or
            # reconcile its temporary EDID back to the persistent selection.
            if last_status != "busy":
                report({"state": "coordinated operation in progress"})
                last_status = "busy"
        except Exception as error:
            if (
                mutating
                or "Unrecognized" in str(error)
                or isinstance(error, RuntimeError)
            ):
                if os.path.exists(engine.root + "/enabled"):
                    os.unlink(engine.root + "/enabled")
                    storage.sync_directory(engine.root)
                report(
                    {
                        "state": "manual recovery required; automatic apply stopped",
                        "error": str(error),
                    }
                )
                return 1
            if last_status != "not-ready":
                report({"state": "waiting for services", "error": str(error)})
                last_status = "not-ready"
        # This process exists in standby only to retain a pending recovery.
        for unused in range(150):
            if not (
                os.path.exists(engine.root + "/enabled")
                or os.path.isfile(engine.root + "/operation.json")
                or os.path.exists(engine.root + "/disable-requested")
            ):
                break
            engine.sleep(0.2)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != "daemon":
        raise SystemExit("Usage: service.py daemon")
    state.verify_firmware()
    raise SystemExit(daemon())

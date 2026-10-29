"""Offline state-machine and restart fault tests. No real native backend is used."""

import contextlib
import ast
import copy
import hashlib
import io
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import coordinator
import control
import edid
import profiles
import state
import storage
import service


def block_checksum(data):
    data[-1] = (-sum(data[:-1])) & 255
    return bytes(data)


def original(port):
    base = bytearray(128)
    base[:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    base[12] = port
    base[18:20] = b"\x01\x03"
    base[126] = 1
    audio = bytes.fromhex("0f57071507505707016704033d1ec05f7e01")
    collection = bytes.fromhex("43616010") + bytes([0x20 | len(audio)]) + audio
    collection += bytes([0x65, 3, 12, 0, port << 4, 0])
    collection += bytes.fromhex("6546d0000102e3060d01")
    extension = bytearray([2, 3, 4 + len(collection), 0xF1]) + collection
    extension += bytes(range(1, 19))
    extension += bytes(128 - len(extension))
    return block_checksum(base) + block_checksum(extension)


def backup():
    data = {
        "profile": state.PROFILE,
        "binary_hashes": state.PINS,
        "edids": {str(p): original(p).hex() for p in range(1, 5)},
        "preservation": {
            "calibration_hashes": {"synthetic": "unchanged"},
            "white_balance": {"synthetic": [1, 2, 3]},
            "oled": {"OledTPC": "true", "OledGSR": "true"},
            "picture_protections": {"screenShift": "on"},
        },
    }
    data["candidate_sha256"] = hashlib.sha256(
        edid.build_candidate(original(1))
    ).hexdigest()
    return profiles.normalize_backup(data)


class PowerLoss(BaseException):
    pass


class FakeTV:
    def __init__(self):
        self.backup = backup()
        self.values = dict(self.backup["edids"])
        self.preservation = copy.deepcopy(self.backup["preservation"])
        self.writes = []
        self.boot = "boot-A"
        self.seconds = 1000
        self.failure = None
        self.active = True

    def native(self, action, port, operation_id):
        if port == 2:
            raise AssertionError("HDMI2 must never reach a writer")
        self.writes.append((action, port))
        self.values[str(port)] = (
            profiles.trial_candidate(self.backup, port, profiles.TRIAL_PROFILE)
            if action == "trial71" else profiles.candidates(self.backup)[str(port)]
            if action == "apply"
            else self.backup["edids"][str(port)]
        )
        if self.failure:
            self.failure(action, port)

    def sleep(self, seconds):
        self.seconds += seconds

    def reboot(self):
        self.boot = "boot-B"
        self.values = dict(self.backup["edids"])
        self.seconds = 0

    def engine(self, root, fault=None):
        return coordinator.Engine(
            backup=self.backup,
            root=str(root),
            read=lambda: dict(self.values),
            preserved=lambda: copy.deepcopy(self.preservation),
            backend=self.native,
            verify=lambda: None,
            active=lambda: self.active,
            boot=lambda: self.boot,
            identity=lambda: "synthetic-start",
            clock=lambda: self.seconds,
            sleep=self.sleep,
            fault=fault,
            recovery=lambda: None,
        )


class MultiportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.tv = FakeTV()
        self.engine = self.tv.engine(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def selected(self, ports):
        storage.write_json(
            str(self.root / "selection.json"), coordinator.configuration(ports)
        )

    def test_small_cli_rejects_invalid_flags_before_tv_access(self):
        for argv in [[], ['unknown'], ['install'], ['install', '--prepare', '--enable'],
                     ['trial', '3', '--seconds', '181'], ['trial', '2'],
                     ['trial', '3', '--seconds', '5', '--seconds', '6'],
                     ['apply', '--ports', '3'], ['status', 'unexpected'],
                     ['install', '--prepare', '--adopt-hdmi1-original']]:
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                control.parse_arguments(argv)
        args = control.parse_arguments(['install', '--prepare', '--ports=1,3'])
        self.assertEqual(args.ports, [1, 3])
        self.assertFalse(args.enable)
        args = control.parse_arguments(['trial', '4', '--seconds', '90', '--foreground'])
        self.assertEqual((args.port, args.seconds, args.foreground), ([4], 90, True))

    def test_posix_controller_timeout_is_bounded_and_restores_handler(self):
        before = signal.getsignal(signal.SIGALRM)
        started = time.monotonic()
        code, output, error = coordinator.run_controller(
            [sys.executable, '-c', 'import time;time.sleep(10)'], dict(os.environ), 1)
        self.assertNotEqual(code, 0)
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(signal.getsignal(signal.SIGALRM), before)
        self.assertEqual(signal.alarm(0), 0)

    def test_controller_does_not_steal_an_existing_alarm(self):
        signal.alarm(10)
        try:
            with mock.patch.object(subprocess, 'Popen') as launch:
                with self.assertRaisesRegex(ValueError, 'existing alarm'):
                    coordinator.run_controller(['unused'], dict(os.environ))
                launch.assert_not_called()
            self.assertGreater(signal.alarm(0), 0)
        finally:
            signal.alarm(0)

    def test_python2_style_descriptors_do_not_leak_locks_to_exec_children(self):
        original_open = os.open

        def inheritable_open(*args, **kwargs):
            descriptor = original_open(*args, **kwargs)
            os.set_inheritable(descriptor, True)
            return descriptor

        path = str(self.root / 'close-on-exec.lock')
        with mock.patch.object(os, 'open', side_effect=inheritable_open):
            with storage.lock(path):
                # Re-open a probe only to find the inode; it owns no lock.
                value = os.stat(path)
                code = 'import os,sys\nfound=False\nfor fd in range(3,128):\n try:\n  s=os.fstat(fd);found=found or (s.st_dev,s.st_ino)==%r\n except OSError:pass\nsys.exit(1 if found else 0)' % ((value.st_dev,value.st_ino),)
                result = subprocess.run([sys.executable, '-c', code], close_fds=False)
                self.assertEqual(result.returncode, 0)

    def test_port_two_invalid_empty_and_duplicate_selection(self):
        for invalid in ("2", "1,2,3", "0", "5", "1,1", "3.0", [True], [8]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                profiles.parse_ports(invalid)
        self.assertEqual(profiles.parse_ports("none"), [])
        self.assertEqual(profiles.parse_ports("4,1,3"), [1, 3, 4])
        with self.assertRaisesRegex(ValueError, "HDMI 2"):
            profiles.native_action(self.tv.backup, 2, original(2).hex())

    def test_each_candidate_keeps_its_own_identity_cec_video_dv_and_timing(self):
        candidates = profiles.candidates(self.tv.backup)
        self.assertEqual(set(candidates), {"1", "3", "4"})
        for port in profiles.PORTS:
            before = original(port)
            after = bytes.fromhex(candidates[str(port)])
            self.assertEqual(after[:128], before[:128])
            non_audio = lambda b: [
                item for item in edid.parse(b) if item[0] >> 5 not in (1, 4)
            ]
            self.assertEqual(non_audio(after), non_audio(before))
            self.assertIn(bytes([0x65, 3, 12, 0, port << 4, 0]), edid.parse(after))
            self.assertEqual(
                after[128 + after[130] : 128 + after[130] + 18],
                before[128 + before[130] : 128 + before[130] + 18],
            )
            self.assertEqual([sum(after[p : p + 128]) % 256 for p in (0, 128)], [0, 0])
        self.assertNotEqual(candidates["1"], candidates["3"])

    def test_default_apply_changes_only_one(self):
        self.engine.run("apply")
        self.assertEqual(self.tv.writes, [("apply", 1)])
        self.assertEqual(self.tv.values["2"], original(2).hex())
        self.assertEqual(self.tv.values["3"], original(3).hex())
        self.assertIsNone(self.engine.pending())

    def test_none_selection_performs_no_native_write(self):
        self.engine.select([])
        self.engine.run("apply")
        self.assertEqual(self.tv.writes, [])

    def test_selection_is_opt_in_and_does_not_write(self):
        self.engine.select([1, 3, 4])
        self.assertEqual(self.tv.writes, [])
        self.assertEqual(set(self.engine.selection()["profiles"]), {"1", "3", "4"})
        (self.root / "enabled").touch(mode=0o600)
        with self.assertRaisesRegex(ValueError, "Disable"):
            self.engine.select([1])

    def test_cannot_deselect_an_existing_override_without_restoring_it(self):
        self.tv.values["1"] = profiles.candidates(self.tv.backup)["1"]
        with self.assertRaisesRegex(ValueError, "Restore"):
            self.engine.select([3])
        self.assertEqual(self.tv.writes, [])

    def test_apply_three_is_serial_and_keeps_earc(self):
        self.engine.select([1, 3, 4])
        self.engine.run("apply")
        self.assertEqual(self.tv.writes, [("apply", 1), ("apply", 3), ("apply", 4)])
        self.assertEqual(self.tv.values["2"], original(2).hex())

    def test_single_restore_keeps_other_working_overrides_and_updates_selection(self):
        self.selected([1, 3, 4])
        self.tv.values.update(profiles.candidates(self.tv.backup))
        self.engine.run("restore", [3])
        self.assertEqual(self.tv.writes, [("restore", 3)])
        self.assertEqual(set(self.engine.selection()["profiles"]), {"1", "4"})
        self.assertEqual(self.tv.values["1"], profiles.candidates(self.tv.backup)["1"])
        self.assertEqual(self.tv.values["4"], profiles.candidates(self.tv.backup)["4"])

    def test_disable_restores_every_port_but_retains_the_selection(self):
        self.selected([1, 3, 4])
        self.tv.values.update(profiles.candidates(self.tv.backup))
        self.engine.run("disable")
        self.assertEqual(self.tv.values, self.tv.backup["edids"])
        self.assertEqual(set(self.engine.selection()["profiles"]), {"1", "3", "4"})

    def test_trial_restores_working_one_instead_of_factory_reset(self):
        self.tv.values["1"] = profiles.candidates(self.tv.backup)["1"]
        before = dict(self.tv.values)
        self.engine.run("trial", [3], seconds=1)
        self.assertEqual(self.tv.values, before)
        self.assertEqual(self.tv.writes, [("apply", 3), ("restore", 3)])
        self.assertEqual(self.engine.selection(), coordinator.configuration([1]))
        self.assertIsNone(self.engine.pending())

    def test_trial_of_already_modified_input_is_a_preserving_noop(self):
        self.tv.values["1"] = profiles.candidates(self.tv.backup)["1"]
        self.engine.run("trial", [1], seconds=1)
        self.assertEqual(self.tv.writes, [])
        self.assertEqual(self.tv.values["1"], profiles.candidates(self.tv.backup)["1"])

    def test_experimental_trial_restores_working_pcm51_and_other_inputs(self):
        self.tv.values["1"] = profiles.candidates(self.tv.backup)["1"]
        self.tv.values["3"] = profiles.candidates(self.tv.backup)["3"]
        before = dict(self.tv.values)
        seen = []
        def observe(point):
            if point == "trial-active":
                seen.append(dict(self.tv.values))
        self.tv.engine(self.root, observe).run("trial", [1], 1, profiles.TRIAL_PROFILE)
        self.assertEqual(seen[0]["1"], profiles.trial_candidate(self.tv.backup, 1, profiles.TRIAL_PROFILE))
        self.assertEqual(self.tv.values, before)
        self.assertEqual(self.tv.writes, [("trial71", 1), ("apply", 1)])
        self.assertEqual(self.engine.selection(), coordinator.configuration([1]))

    def test_experimental_native_failure_restores_exact_working_preimage(self):
        self.tv.values["1"] = profiles.candidates(self.tv.backup)["1"]
        before = dict(self.tv.values)
        failed = []
        def refuse(action, port):
            if action == "trial71" and not failed:
                failed.append(True)
                raise RuntimeError("synthetic experimental native failure")
        self.tv.failure = refuse
        with self.assertRaisesRegex(RuntimeError, "experimental native failure"):
            self.engine.run("trial", [1], 1, profiles.TRIAL_PROFILE)
        self.assertEqual(self.tv.values, before)
        self.assertIsNone(self.engine.pending())
        self.assertEqual(self.tv.writes, [("trial71", 1), ("apply", 1)])

    def test_experimental_worker_death_recovers_on_same_boot_or_after_reboot(self):
        for reboot in (False, True):
            with self.subTest(reboot=reboot):
                self.tv.values["1"] = profiles.candidates(self.tv.backup)["1"]
                before = dict(self.tv.values)
                def crash(point):
                    if point == "trial-active":
                        raise PowerLoss()
                with self.assertRaises(PowerLoss):
                    self.tv.engine(self.root, crash).run("trial", [1], 1, profiles.TRIAL_PROFILE)
                if reboot:
                    self.tv.reboot()
                self.tv.engine(self.root).recover()
                self.assertEqual(self.tv.values, before)
                self.assertIsNone(self.engine.pending())

    def test_eight_channel_bytes_require_the_specific_pending_trial(self):
        self.tv.values["1"] = profiles.trial_candidate(self.tv.backup, 1, profiles.TRIAL_PROFILE)
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            self.engine.run("apply")
        self.assertEqual(self.tv.writes, [])

    def test_experimental_profile_cannot_be_selected_applied_or_used_on_other_ports(self):
        for action, ports in [("apply", [1]), ("restore", [1]), ("disable", [1]),
                              ("trial", [3]), ("trial", [4]), ("trial", [2])]:
            with self.subTest(action=action, ports=ports), self.assertRaises(ValueError):
                self.engine.run(action, ports, 1, profiles.TRIAL_PROFILE)
        with self.assertRaises(ValueError):
            coordinator.validate_configuration({"format_version": 1, "profiles": {"1": profiles.TRIAL_PROFILE}})
        for args in [["apply", "--profile", profiles.TRIAL_PROFILE],
                     ["trial", "3", "--profile", profiles.TRIAL_PROFILE],
                     ["trial", "1", "--profile", "unknown"]]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                control.parse_arguments(args)
        parsed = control.parse_arguments(["trial", "1", "--profile", profiles.TRIAL_PROFILE])
        self.assertEqual(parsed.trial_profile, profiles.TRIAL_PROFILE)
        self.assertEqual(self.tv.writes, [])

    def test_experimental_journal_cannot_commit_or_change_another_input(self):
        def crash(point):
            if point == "prepared":
                raise PowerLoss()
        with self.assertRaises(PowerLoss):
            self.tv.engine(self.root, crash).run("trial", [1], 1, profiles.TRIAL_PROFILE)
        original_operation = self.engine.pending()
        for mutate in (lambda op: op.update(decision="commit"),
                       lambda op: op.update(action="apply"),
                       lambda op: op.update(trial_port=3),
                       lambda op: op["after"].update({"3": profiles.candidates(self.tv.backup)["3"]}),
                       lambda op: op["config_after"].update(profiles={"3": profiles.PROFILE})):
            value = copy.deepcopy(original_operation)
            mutate(value)
            storage.write_json(str(self.root / "operation.json"), value)
            with self.assertRaises(ValueError):
                self.engine.recover()
        storage.write_json(str(self.root / "operation.json"), original_operation)
        self.engine.recover()
        self.assertEqual(self.tv.writes, [])

    def test_experimental_native_staging_accepts_exact_preimage_and_target_only(self):
        source = (pathlib.Path(control.__file__).parent / "transaction.py").read_text()
        function = next(node for node in ast.parse(source).body
                        if isinstance(node, ast.FunctionDef) and node.name == "file_for_load")
        allowed = [original(1), edid.build_candidate(original(1)), edid.build_candidate(original(1), 8)]
        namespace = {"PORT": 1, "ALLOWED": allowed, "storage": storage,
                     "open": mock.mock_open(read_data=allowed[2])}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "native-stage", "exec"), namespace)
        with mock.patch.object(storage, "atomic_bytes") as stage:
            namespace["file_for_load"](allowed[2])
            stage.assert_called_once_with("/tmp/edid_1.bin", allowed[2])
            with self.assertRaises(AssertionError):
                namespace["file_for_load"](b"untrusted")

    def test_trial_bound_and_single_port_validation(self):
        for ports, seconds in [([3, 4], 1), ([3], 181), ([3], 0), ([3], 1.5)]:
            with self.subTest(ports=ports, seconds=seconds), self.assertRaises(
                ValueError
            ):
                self.engine.run("trial", ports, seconds)
        self.assertFalse((self.root / "operation.json").exists())

    def test_cancel_requests_rollback_before_hold_deadline(self):
        def stop(point):
            if point == "trial-active":
                (self.root / "trial-stop").touch(mode=0o600)

        engine = self.tv.engine(self.root, stop)
        engine.run("trial", [4], 180)
        self.assertLess(self.tv.seconds, 1001)
        self.assertEqual(self.tv.values, self.tv.backup["edids"])

    def test_partial_failure_rolls_back_every_changed_port(self):
        self.engine.select([1, 3, 4])
        failed = []

        def failure(action, port):
            if port == 3 and action == "apply" and not failed:
                failed.append(True)
                raise RuntimeError("simulated write refusal")

        self.tv.failure = failure
        with self.assertRaisesRegex(RuntimeError, "refusal"):
            self.engine.run("apply")
        self.assertEqual(self.tv.values, self.tv.backup["edids"])
        self.assertIsNone(self.engine.pending())
        self.assertNotIn(("apply", 4), self.tv.writes)

    def test_power_loss_after_partial_apply_restores_preimage_after_tmp_is_lost(self):
        self.tv.values["1"] = profiles.candidates(self.tv.backup)["1"]
        before = dict(self.tv.values)
        self.engine.select([1, 3, 4])
        (self.root / "enabled").touch(mode=0o600)

        def crash(point):
            if point == "after-write-3":
                raise PowerLoss()

        with self.assertRaises(PowerLoss):
            self.tv.engine(self.root, crash).run("apply")
        self.assertTrue((self.root / "operation.json").exists())
        self.tv.reboot()
        # There is no temporary-directory dependency in the replay.
        self.tv.engine(self.root).recover()
        self.assertEqual(self.tv.values, before)
        self.assertEqual(self.tv.writes[-1], ("apply", 1))
        self.assertFalse(
            (self.root / "enabled").exists(),
            "failed apply cannot auto-loop after recovery",
        )
        self.assertIsNone(self.engine.pending())

    def test_independent_recovery_is_ready_before_any_native_write(self):
        ready = []
        self.engine.ensure_recovery = lambda: ready.append(True)
        original = self.tv.native

        def native(action, port, operation):
            self.assertTrue(ready, 'No native write without recovery readiness')
            original(action, port, operation)

        self.engine.backend = native
        self.engine.run('trial', [3], 1)
        self.assertEqual(self.tv.values, self.tv.backup['edids'])

    def test_recovery_readiness_failure_prevents_native_write(self):
        def refused():
            raise RuntimeError('Recovery not ready')

        self.engine.ensure_recovery = refused
        with self.assertRaisesRegex(RuntimeError, 'Recovery not ready'):
            self.engine.run('trial', [3], 1)
        self.assertEqual(self.tv.writes, [])
        self.assertIsNone(self.engine.pending())

    def test_noop_trial_also_requires_an_independent_monitor(self):
        self.tv.values['1'] = profiles.candidates(self.tv.backup)['1']
        ready = mock.Mock()
        self.engine.ensure_recovery = ready
        self.engine.run('trial', [1], 1)
        ready.assert_called()
        self.assertEqual(self.tv.writes, [])

    def test_monitor_bootstrap_waits_for_verified_readiness(self):
        ready = {'pid': 100}
        with mock.patch.object(service, 'recovery_ready', side_effect=[None, None, ready]), mock.patch.object(
            service.subprocess, 'check_call'
        ) as hook, mock.patch.object(coordinator, 'monotonic', side_effect=[0, 0, 1]), mock.patch('time.sleep'):
            service.ensure_recovery(str(self.root), self.tv.backup)
        hook.assert_called_once()

    def test_monitor_bootstrap_fails_closed_when_readiness_never_arrives(self):
        with mock.patch.object(service, 'recovery_ready', return_value=None), mock.patch.object(
            service.subprocess, 'check_call'
        ), mock.patch.object(coordinator, 'monotonic', side_effect=[0, 9]):
            with self.assertRaisesRegex(RuntimeError, 'did not become ready'):
                service.ensure_recovery(str(self.root), self.tv.backup)

    def test_monitor_readiness_rejects_a_wrong_backup(self):
        value = {'boot_id': 'test-boot', 'pid': 123, 'started': '456', 'protocol': 1,
                 'root': str(self.root), 'backup_id': 'wrong'}
        process_stat = '123 (python) ' + ' '.join(['0'] * 19 + ['456'])
        command = (str(self.root) + '/service.py\0daemon\0').encode()
        with mock.patch.object(storage, 'read_json', return_value=value), mock.patch.object(
            coordinator, 'boot_id', return_value='test-boot'
        ), mock.patch('builtins.open', side_effect=[io.StringIO(process_stat), io.BytesIO(command)]):
            with self.assertRaisesRegex(ValueError, 'does not match'):
                service.recovery_ready(str(self.root), self.tv.backup)
    def test_power_loss_in_trial_recovers_working_one_without_enabling_three(self):
        self.tv.values["1"] = profiles.candidates(self.tv.backup)["1"]
        before = dict(self.tv.values)

        def crash(point):
            if point == "trial-active":
                raise PowerLoss()

        with self.assertRaises(PowerLoss):
            self.tv.engine(self.root, crash).run("trial", [3], 1)
        self.tv.reboot()
        self.tv.engine(self.root).recover()
        self.assertEqual(self.tv.values, before)
        self.assertEqual(self.engine.selection(), coordinator.configuration([1]))

    def test_durable_commit_is_finished_instead_of_rolled_back_after_restart(self):
        self.selected([3])

        def crash(point):
            if point == "committed":
                raise PowerLoss()

        with self.assertRaises(PowerLoss):
            self.tv.engine(self.root, crash).run("apply")
        self.tv.reboot()
        self.tv.engine(self.root).recover()
        self.assertEqual(self.tv.values, profiles.selected_map(self.tv.backup, [3]))
        self.assertIsNone(self.engine.pending())

    def test_console_session_without_cleanup_proof_refuses_same_boot_recovery(self):
        def crash(point):
            if point == "prepared":
                raise PowerLoss()

        with self.assertRaises(PowerLoss):
            self.tv.engine(self.root, crash).run("trial", [3], 1)
        storage.write_json(
            str(self.root / "native-pending.json"),
            {
                "backup_id": profiles.backup_id(self.tv.backup),
                "port": 3,
                "transaction_id": "a" * 32,
                "boot_id": self.tv.boot,
            },
        )
        with self.assertRaisesRegex(ValueError, "console session"):
            self.engine.recover()
        self.assertEqual(self.tv.writes, [])
        self.tv.reboot()
        self.engine.recover()
        self.assertIsNone(self.engine.pending())

    def test_unknown_edid_and_changed_calibration_stop_before_any_write(self):
        self.tv.values["4"] = original(1).hex()
        with self.assertRaisesRegex(ValueError, "Unrecognized HDMI 4"):
            self.engine.run("apply")
        self.assertEqual(self.tv.writes, [])
        self.tv.values = dict(self.tv.backup["edids"])
        self.tv.preservation["oled"]["OledTPC"] = "false"
        with self.assertRaisesRegex(ValueError, "Calibration/OLED"):
            self.engine.run("apply")
        self.assertFalse((self.root / "operation.json").exists())

    def test_backup_cannot_copy_another_ports_cec_identity(self):
        value = backup()
        value["edids"]["3"] = value["edids"]["1"]
        with self.assertRaisesRegex(ValueError, "CEC physical address for HDMI 3"):
            profiles.normalize_backup(value)

    def test_sync_failure_prevents_native_write(self):
        with mock.patch.object(storage.os, "fsync", side_effect=OSError("sync failed")):
            with self.assertRaisesRegex(OSError, "sync failed"):
                self.engine.run("apply")
        self.assertEqual(self.tv.writes, [])

    def test_selection_cannot_silently_recover_hardware(self):
        def crash(point):
            if point == "trial-active":
                raise PowerLoss()

        with self.assertRaises(PowerLoss):
            self.tv.engine(self.root, crash).run("trial", [3], 1)
        self.tv.writes[:] = []
        with self.assertRaisesRegex(ValueError, "Recover pending"):
            self.engine.select([1, 3])
        self.assertEqual(self.tv.writes, [])
        self.assertTrue(self.engine.pending())

    def test_installer_refuses_busy_or_pending_disable_before_replacing_code(self):
        storage.write_json(str(self.root / "backup.json"), self.tv.backup)
        args = SimpleNamespace(adopt_hdmi1_original=None, ports=[1], enable=False)
        with mock.patch.object(state, "BASE", str(self.root)), mock.patch.object(
            state, "verify_firmware"
        ), mock.patch.object(state, "no_competing_service"), mock.patch.object(
            storage, "atomic_bytes", wraps=storage.atomic_bytes
        ) as write:
            with storage.lock(str(self.root / "operation.lock")):
                with self.assertRaises(IOError):
                    control.install(args)
            (self.root / "disable-requested").touch(mode=0o600)
            with self.assertRaisesRegex(ValueError, "finish recovery"):
                control.install(args)
            write.assert_not_called()

    def test_failed_rollback_remains_recoverable(self):
        def crash(point):
            if point == "trial-active":
                raise PowerLoss()

        with self.assertRaises(PowerLoss):
            self.tv.engine(self.root, crash).run("trial", [3], 1)

        def fail(action, port):
            raise RuntimeError("restore result unavailable")

        self.tv.failure = fail
        with self.assertRaisesRegex(RuntimeError, "restore result unavailable"):
            self.engine.recover()
        self.assertEqual(self.engine.pending()["phase"], "recovery-required")
        self.tv.failure = None
        self.engine.recover()
        self.assertEqual(self.tv.values, self.tv.backup["edids"])
        self.assertIsNone(self.engine.pending())

    def test_monitor_does_not_disable_a_busy_trial(self):
        def crash(point):
            if point == "trial-active":
                raise PowerLoss()

        with self.assertRaises(PowerLoss):
            self.tv.engine(self.root, crash).run("trial", [3], 1)
        (self.root / "enabled").touch(mode=0o600)
        with storage.lock(str(self.root / "operation.lock")), mock.patch.object(
            service, "report"
        ) as report, mock.patch.object(self.engine, "sleep", side_effect=PowerLoss):
            with self.assertRaises(PowerLoss):
                service.monitor(self.engine)
        self.assertTrue((self.root / "enabled").exists())
        self.assertEqual(self.engine.pending()["phase"], "trial-active")
        report.assert_called_with({"state": "coordinated operation in progress"})

    def test_monitor_cannot_ignore_a_malformed_disabled_recovery(self):
        storage.write_json(str(self.root / "operation.json"), {"invalid": True})
        with mock.patch.object(service, "report") as report:
            self.assertEqual(service.monitor(self.engine), 1)
        self.assertIn("manual recovery required", report.call_args[0][0]["state"])
        self.assertTrue((self.root / "operation.json").exists())
        self.assertEqual(self.tv.writes, [])

    def test_native_load_stages_on_the_runtime_filesystem(self):
        source = (pathlib.Path(control.__file__).parent / "transaction.py").read_text()
        function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "file_for_load"
        )
        # Exercise the actual staging function without importing native console code.
        namespace = {
            "PORT": 3,
            "ORIGINAL": b"original",
            "CANDIDATE": b"candidate",
            "ALLOWED": [b"original", b"candidate"],
            "storage": storage,
            "open": mock.mock_open(read_data=b"candidate"),
        }
        exec(
            compile(
                ast.Module(body=[function], type_ignores=[]), "native-stage", "exec"
            ),
            namespace,
        )
        with mock.patch.object(storage, "atomic_bytes") as stage:
            namespace["file_for_load"](b"candidate")
        stage.assert_called_once_with("/tmp/edid_3.bin", b"candidate")

    def test_tampered_hdmi2_journal_cannot_authorize_an_earc_write(self):
        def crash(point):
            if point == "prepared":
                raise PowerLoss()

        with self.assertRaises(PowerLoss):
            self.tv.engine(self.root, crash).run("apply")
        journal = storage.read_json(str(self.root / "operation.json"))
        journal["before"]["2"] = original(3).hex()
        storage.write_json(str(self.root / "operation.json"), journal)
        with self.assertRaisesRegex(ValueError, "HDMI 2"):
            self.engine.recover()
        self.assertEqual(self.tv.writes, [])

    def test_non_active_tv_leaves_durable_recovery_without_writing(self):
        self.tv.active = False
        with self.assertRaises(ValueError):
            self.engine.run("apply")
        self.assertEqual(self.tv.writes, [])
        # The failed operation needed no hardware rollback and can finalize.
        self.assertIsNone(self.engine.pending())

    def test_operation_lock_prevents_a_second_writer(self):
        with storage.lock(str(self.root / "operation.lock")):
            with self.assertRaises(IOError):
                self.engine.run("apply")
        self.assertEqual(self.tv.writes, [])

    def test_state_permissions_and_symlinks_fail_closed(self):
        path = self.root / "selection.json"
        storage.write_json(str(path), coordinator.configuration([1]))
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.engine.selection()
        path.unlink()
        other = self.root / "other.json"
        storage.write_json(str(other), coordinator.configuration([1]))
        path.symlink_to(other)
        with self.assertRaises(ValueError):
            self.engine.selection()

    def test_schema_one_load_is_a_read_only_upgrade(self):
        value = backup()
        value.pop("format_version")
        value.pop("candidate_sha256s")
        path = self.root / "backup.json"
        storage.write_json(str(path), value)
        before = path.read_bytes()
        with mock.patch.object(state, "BASE", str(self.root)):
            loaded = state.load_backup()
        self.assertEqual(loaded["format_version"], 2)
        self.assertEqual(set(loaded["candidate_sha256s"]), {"1", "3", "4"})
        self.assertEqual(path.read_bytes(), before)

    def test_explicit_adoption_requires_the_exact_working_hdmi1_candidate(self):
        adopted = self.root / "original.bin"
        adopted.write_bytes(original(1))
        observed = dict(self.tv.backup["edids"])
        observed["1"] = profiles.candidates(self.tv.backup)["1"]

        def luna(uri, payload):
            return {
                "returnValue": True,
                "state": "Active",
                "configs": {
                    "tv.model.hdmiSwitchType": "HAWK2",
                    "tv.model.edidType": "TrueHD",
                },
            }

        destination = self.root / "captured"
        with mock.patch.object(state, "BASE", str(destination)), mock.patch.object(
            state, "verify_firmware"
        ), mock.patch.object(state, "luna", side_effect=luna), mock.patch.object(
            state, "read_edids", return_value=observed
        ), mock.patch.object(
            state, "preserved", return_value=self.tv.backup["preservation"]
        ), contextlib.redirect_stdout(io.StringIO()):
            state.capture(str(adopted))
        saved = storage.read_json(str(destination / "backup.json"))
        self.assertEqual(saved["edids"]["1"], original(1).hex())
        self.assertEqual(saved["observed_at_capture"]["1"], observed["1"])
        self.assertEqual(saved["edids"]["2"], original(2).hex())
        self.assertEqual(
            saved["candidate_sha256s"]["3"], self.tv.backup["candidate_sha256s"]["3"]
        )

    def test_entrypoints_reject_two_before_firmware_or_console_access(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        for command in [
            ["control.py", "select", "2"],
            ["control.py", "trial", "1,2"],
            ["transaction.py", "apply", "2"],
        ]:
            result = subprocess.run(
                [sys.executable] + [str(root / command[0])] + command[1:],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("reserved", result.stdout + result.stderr)
            self.assertNotIn("Run on the rooted TV", result.stdout + result.stderr)

    def test_native_writer_is_only_entered_with_coordinated_intent(self):
        # Parse the actual backend with a fake native API environment on the
        # qualified Python grammar; the CLI checks happen before RAM access.
        source = (
            pathlib.Path(__file__).resolve().parents[1] / "transaction.py"
        ).read_text()
        self.assertLess(
            source.index("PORT in profiles.PORTS"), source.index("verify_firmware()")
        )
        self.assertLess(
            source.index("C9_EDID_OPERATION"), source.index("process_vm_readv")
        )
        self.assertIn('key(("%d\\n" % PORT).encode("ascii"))', source)
        self.assertNotIn("/tmp/edid_1.bin", source)

    def test_recovery_hook_runs_even_with_enabled_marker_absent(self):
        source = (
            pathlib.Path(__file__).resolve().parents[1] / "60-glasshouse-c9-edid"
        ).read_text()
        run = self.root / "runtime"
        called = self.root / "called"
        fake = self.root / "fakepython"
        fake.write_text('#!/bin/sh\n: > "' + str(called) + '"\n')
        fake.chmod(0o700)
        source = source.replace(
            "D=/var/lib/glasshouse-c9-edid", 'D="' + str(self.root) + '"'
        )
        source = source.replace("/tmp/glasshouse-c9-edid", str(run)).replace(
            "/usr/bin/python", str(fake)
        )
        hook = self.root / "hook"
        hook.write_text(source)
        subprocess.run(["sh", str(hook)], check=True)
        self.assertFalse(called.exists())
        storage.write_json(str(self.root / "operation.json"), {"pending": True})
        subprocess.run(["sh", str(hook)], check=True)
        end = time.monotonic() + 2
        while not called.exists() and time.monotonic() < end:
            time.sleep(0.01)
        self.assertTrue(called.exists())
        self.assertFalse((self.root / "enabled").exists())

    def test_detached_trial_worker_finishes_after_cli_parent_exits(self):
        code = """
import control,time,json,pathlib
class Engine:
 root=ROOT
 def run(self,*args,**kwargs):
  time.sleep(.1)
  pathlib.Path(self.root,'detached-finished').write_text('restored')
  return {'verified':True}
print(json.dumps(control.trial(Engine(),3,1)))
""".replace("ROOT", repr(str(self.root)))
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(pathlib.Path(control.__file__).parent),
            capture_output=True,
            text=True,
            timeout=3,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("independent", result.stdout)
        end = time.monotonic() + 2
        while not (self.root / "detached-finished").exists() and time.monotonic() < end:
            time.sleep(0.01)
        self.assertEqual((self.root / "detached-finished").read_text(), "restored")


if __name__ == "__main__":
    unittest.main()

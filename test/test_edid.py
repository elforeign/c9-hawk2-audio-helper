"""No TV access: malformed EDIDs and immutable video/Dolby declarations."""

import pathlib
import json
import os
import subprocess
import tempfile
import sys
import unittest
from unittest import mock

MODULES = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULES))
import edid
import state


def checksum(values):
    values[-1] = (-sum(values[:-1])) & 255
    return bytes(values)


def fixture():
    # Synthetic identity and timings; no owner's captured EDID is distributed.
    base = bytearray(128)
    base[:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    base[18:20] = b"\x01\x03"
    base[126] = 1
    audio = bytes.fromhex("0f57071507505707016704033d1ec05f7e01")
    video = bytes.fromhex("43616010")
    vendor = bytes.fromhex("63030c00")
    hdr = bytes.fromhex("e3060d01")
    collection = video + bytes([0x20 | len(audio)]) + audio + vendor + hdr
    extension = bytearray([2, 3, 4 + len(collection), 0xF1])
    extension.extend(collection)
    extension.extend(bytes(range(1, 19)))
    extension.extend(bytes(128 - len(extension)))
    return checksum(base) + checksum(extension)


class CandidateTests(unittest.TestCase):
    def test_preserves_base_video_dolby_and_timing(self):
        original = fixture()
        candidate = edid.build_candidate(original)
        self.assertEqual(len(candidate), 256)
        self.assertEqual(candidate[:128], original[:128])
        self.assertEqual(
            [sum(candidate[start : start + 128]) % 256 for start in (0, 128)], [0, 0]
        )
        original_blocks = edid.parse(original)
        candidate_blocks = edid.parse(candidate)
        self.assertIn(bytes.fromhex("830f0000"), candidate_blocks)
        self.assertIn(
            bytes.fromhex("2f0d07071507505707016704033d1ec0"), candidate_blocks
        )
        self.assertEqual(
            [block for block in original_blocks if block[0] >> 5 != 1],
            [block for block in candidate_blocks if block[0] >> 5 not in (1, 4)],
        )
        self.assertEqual(
            candidate[128 + candidate[130] : 128 + candidate[130] + 18],
            original[128 + original[130] : 128 + original[130] + 18],
        )
        # AC3, EAC3 and MAT stay byte-for-byte, independent of the PCM edit.
        for descriptor in ("150750", "570701", "670403"):
            self.assertIn(bytes.fromhex(descriptor), candidate)

    def test_eight_channel_trial_retains_48khz_and_default_is_unchanged(self):
        original = fixture()
        six = edid.build_candidate(original)
        eight = edid.build_candidate(original, channels=8)
        self.assertNotEqual(six, eight)
        self.assertIn(bytes.fromhex("830f0000"), edid.parse(six))
        self.assertIn(bytes.fromhex("834f0000"), edid.parse(eight))
        self.assertIn(
            bytes.fromhex("2f0f07071507505707016704033d1ec0"), edid.parse(eight)
        )
        self.assertEqual(eight[:128], original[:128])
        for candidate in (six, eight):
            self.assertEqual(
                [sum(candidate[i : i + 128]) % 256 for i in (0, 128)], [0, 0]
            )
            for descriptor in ("150750", "570701", "670403"):
                self.assertIn(bytes.fromhex(descriptor), candidate)

    def test_undefined_trial_channel_counts_are_refused(self):
        for channels in (0, 2, 7, 9, "8"):
            with self.subTest(channels=channels), self.assertRaises(ValueError):
                edid.build_candidate(fixture(), channels=channels)

    def test_offline_trial_preparation_never_overwrites_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            original = root / "original.bin"
            original.write_bytes(fixture())
            output = root / "trial"
            command = [
                sys.executable,
                str(MODULES / "prepare-trial.py"),
                "--original",
                str(original),
                "--output-directory",
                str(output),
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (output / "fallback-pcm51.bin").read_bytes(),
                edid.build_candidate(fixture()),
            )
            self.assertEqual(
                (output / "TRIAL-NOT-INSTALLED-pcm71.bin").read_bytes(),
                edid.build_candidate(fixture(), channels=8),
            )
            self.assertFalse(
                json.loads((output / "manifest.json").read_text())[
                    "automatic_installation"
                ]
            )
            if os.name == "posix":
                self.assertEqual(output.stat().st_mode & 0o777, 0o700)
                for child in output.iterdir():
                    self.assertEqual(child.stat().st_mode & 0o777, 0o600)
            saved = {path.name: path.read_bytes() for path in output.iterdir()}
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                saved, {path.name: path.read_bytes() for path in output.iterdir()}
            )

    def test_rejects_corrupt_or_truncated_data(self):
        original = fixture()
        for broken in (
            original[:-1],
            original + b"\0",
            original[:150] + b"\xff" + original[151:],
        ):
            with self.subTest(length=len(broken)), self.assertRaises(ValueError):
                edid.build_candidate(broken)

    def test_rejects_other_audio_profiles(self):
        changed = bytearray(fixture())
        start = changed.index(bytes.fromhex("0f5707"))
        changed[start] = 0x09
        changed[128:] = checksum(changed[128:])
        with self.assertRaisesRegex(ValueError, "Unvalidated audio"):
            edid.build_candidate(bytes(changed))

    def test_rejects_repeat_patch_and_existing_speakers(self):
        candidate = edid.build_candidate(fixture())
        with self.assertRaisesRegex(ValueError, "speaker allocation"):
            edid.build_candidate(candidate)

    def test_rejects_extra_timings_without_truncating_them(self):
        changed = bytearray(fixture())
        changed[128 + changed[130] + 18] = 1
        changed[128:] = checksum(changed[128:])
        with self.assertRaisesRegex(ValueError, "timings"):
            edid.build_candidate(bytes(changed))

    def test_rejects_data_block_overrun(self):
        changed = bytearray(fixture())
        changed[132] = 0x7F
        changed[130] = 10
        changed[128:] = checksum(changed[128:])
        with self.assertRaises(ValueError):
            edid.parse(bytes(changed))

    def test_wrong_firmware_stops_before_process_or_device_access(self):
        with mock.patch.object(state.os, "geteuid", return_value=0), mock.patch.object(
            state, "file_hash", return_value="incorrect"
        ), mock.patch.object(state.subprocess, "Popen") as process:
            with self.assertRaisesRegex(ValueError, "Unvalidated firmware"):
                state.verify_firmware()
            process.assert_not_called()

    def test_backup_directory_is_never_overwritten(self):
        with mock.patch.object(state, "verify_firmware"), mock.patch.object(
            state.os.path, "lexists", return_value=True
        ), mock.patch.object(state, "luna") as luna:
            with self.assertRaisesRegex(ValueError, "never overwrite"):
                state.capture()
            luna.assert_not_called()

    def test_read_method_allowlist(self):
        with mock.patch.object(state.subprocess, "Popen") as process:
            with self.assertRaisesRegex(ValueError, "not allowed"):
                state.luna("com.webos.service.tv.systemproperty/downloadEdid", {})
            process.assert_not_called()

    def test_recovery_does_not_signal_an_exited_controller(self):
        with mock.patch.object(state.os, "getppid", return_value=1), mock.patch.object(
            state.os, "kill"
        ) as kill:
            state.stop_unfinished_controller(5001, "42")
            kill.assert_not_called()

    def test_recovery_checks_parent_identity_before_signal(self):
        record = "5001 (python) " + " ".join(["0"] * 19 + ["43"])
        with mock.patch.object(
            state.os, "getppid", return_value=5001
        ), mock.patch.object(state.os, "getpid", return_value=5002), mock.patch(
            "builtins.open", mock.mock_open(read_data=record)
        ), mock.patch.object(state.os, "kill") as kill:
            with self.assertRaisesRegex(ValueError, "identity changed"):
                state.stop_unfinished_controller(5001, "42")
            kill.assert_not_called()

    def test_recovery_stops_unfinished_parent_before_rollback(self):
        record = "5001 (python) " + " ".join(["0"] * 19 + ["42"])
        with mock.patch.object(
            state.os, "getppid", side_effect=[5001, 1, 1]
        ), mock.patch.object(state.os, "getpid", return_value=5002), mock.patch(
            "builtins.open", mock.mock_open(read_data=record)
        ), mock.patch.object(state.os, "kill") as kill:
            state.stop_unfinished_controller(5001, "42")
            kill.assert_called_once_with(5001, state.signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()

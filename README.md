# C9 HAWK2 audio EDID helper

Experimental, opt-in runtime EDID helper for the C9 → Sonos eARC case where a
Panasonic UB820 sends stereo PCM directly, but PCM 5.1 through a fixed-EDID
splitter. This is a separate project from Glasshouse; installing or updating
Glasshouse does not install this helper.

This helper's backup is **HDMI EDID rollback data**, not a TV or calibration
backup. It cannot restore a professional calibration after a factory reset.
Glasshouse's Settings backup is separate and also does not include professional
picture calibration. See [backup scope](docs/BACKUP-SCOPE.md).

On an OLED65C9PUA, webOS 4.10.2, firmware 05.50.00, the owner confirmed
audible DTS-HD-to-PCM 5.1 and Dolby Atmos with the Panasonic UB820 connected
directly to HDMI 1. Separate bounded runtime trials on HDMI 3 and 4 also
produced audible Multichannel PCM 5.1 and retained Dolby Atmos. Each trial
restored the exact four-input preimage, verified native console cleanup and
preservation checks, and resumed the existing HDMI 1 monitor.

**The generalized helper has passed C9 preparation, Python 2.7 synthetic
fixtures and no-op worker-termination recovery. Combined multi-input application,
single-input restoration while other overrides are active, and generalized
startup/standby/reboot behavior still need hardware qualification.** HDMI 3 and
4 remain opt-in; only HDMI 1 uses the existing persistent implementation.
Discrete channel routing was not independently tested.
Only the two exact firmware fingerprints in `state.py`, HAWK2/TrueHD hardware
declarations and the measured 29-file calibration layout are accepted.

## Per-input profiles

The default selection is HDMI 1 only. HDMI 3 and 4 can be selected separately.
HDMI 2 is reserved for the eARC soundbar and is rejected before native console
access. Every operation verifies all four inputs against their own saved
original or known candidate; HDMI 2 must always match its original.

Each candidate is generated from that input's own immutable original. The
builder changes PCM maximum channels 8 → 6, limits PCM rates to 32/44.1/48 kHz,
and adds a standard 5.1 speaker-allocation block. It removes the DTS-HD
bitstream descriptor to fit 256 bytes. Core DTS, Dolby declarations, each
port's CEC physical address, base EDID, video/HDR/Dolby Vision blocks and
detailed timings are preserved. Copying HDMI 1's EDID to HDMI 3 or 4 is refused.

This advertises capabilities; it adds no decoder. The player must decode
DTS/DTS-HD to PCM. The combined edit solved the observed UB820 case; the
individual cause of its stereo choice has not been isolated. The six-channel
profile can restrict a source that already supports PCM 7.1, so enable it only
on inputs that need it. Native TV apps use a separate playback path.

## Safety and recovery

One coordinator serializes all selected inputs. A durable journal stores the
exact before/after state of all four EDIDs, the persistent selection, and the
immutable backup identity before any native write. A trial restores its exact
preimage, including an already working HDMI 1 override. Restoring one input
leaves other selected inputs intact. Interrupted uncommitted operations roll
back; a durable commit is completed on recovery. Failed apply recovery also
disables automatic application to avoid a retry loop.

The root-only backup, operation journal, native worker results and completed
history are under `/var/lib/glasshouse-c9-edid`, with atomic file replacement
and file/directory fsync. Originals are never regenerated or overwritten.
Schema-one backups are validated and adapted in memory without rewriting them.
The startup hook runs for pending recovery even if automatic application is
disabled. Recovery waits for an Active TV; it never wakes it.

Every application and timed trial requires a matching independent monitor
readiness record before native access. With startup disabled, the monitor
still restores a trial's preimage if its worker dies. The C9 runtime uses a
scoped POSIX controller timeout and a strict internal CLI parser because its
minimal Python omits `threading` and `argparse`.

The native writer uses diagnostic-console **runtime menu 5** and the selected
logical port's `/tmp/edid_N.bin`. It does not use flash-download or raw I2C.
Its independent worker verifies console detachment, restores console RAM
flags, and restores the per-port preimage if the controller fails. An
unfinished same-boot console session blocks all further writes until cleanup
is proven; old PIDs or RAM snapshots are never replayed after reboot. See
[recovery instructions](docs/RECOVERY.md).

Runtime loads renegotiate HDMI and briefly interrupt playback. Software
recovery cannot execute while power is off or guarantee recovery from every
hardware fault. Firmware changes fail the binary checks before console access;
never bypass these checks to force a restore.

## Prepare without applying

Use root SSH with a second authenticated session available. Keep a separate,
verified calibration backup: this helper records preservation hashes and
white-balance values, **not calibration/LUT files or EEPROM**. Calibration and
OLED protections are read and compared, never changed. Do not run another EDID
service or diagnostic-console session alongside this helper.

No calibration archive is imported, created or restored by the audio workflow.
Its read-only integrity checks do not depend on a calibration-backup tool.

Copy this folder alone to `/tmp/c9-hawk2-edid` using your existing authenticated
SSH/SCP setup. No TV address or credentials are included. On an Active TV with
unmodified input EDIDs:

```sh
sh /tmp/c9-hawk2-edid/install.sh --prepare
/var/lib/glasshouse-c9-edid/control status
```

Preparation installs the recovery hook and captures all four originals in
the private **EDID rollback record** `backup.json`, plus separate
original/candidate binary files for HDMI 1, 3 and
4. It performs no native EDID write and does not enable automatic application.
Copy the entire backup privately off the TV before a trial. Never post its
contents, logs, full EDIDs or calibration data in a public issue.

For an existing working HDMI 1 service, use the explicit migration procedure
in [RECOVERY.md](docs/RECOVERY.md). Do not install a second writer or capture a
modified EDID as its factory original.

## Explicit controls

All commands below run on the TV after preparation. HDMI 2 is never accepted.

```sh
# Save a selection while automatic application is disabled; no EDID write.
/var/lib/glasshouse-c9-edid/control select 1,3,4

# Trial one input for at most 180 seconds, then restore its exact preimage.
# The detached worker continues after the SSH command exits.
/var/lib/glasshouse-c9-edid/control trial 3 --seconds 180
/var/lib/glasshouse-c9-edid/control status
/var/lib/glasshouse-c9-edid/control stop-trial

# Apply the saved selection once; this alone does not enable startup.
/var/lib/glasshouse-c9-edid/control apply

# Restore only HDMI 3 and remove it from the saved selection.
/var/lib/glasshouse-c9-edid/control restore 3

# Enable automatic reapplication of the saved selection.
/var/lib/glasshouse-c9-edid/control enable

# Asynchronously stop automatic application and restore all writable inputs.
# Wait for "disabled; originals verified" and no pending recovery.
/var/lib/glasshouse-c9-edid/control disable
/var/lib/glasshouse-c9-edid/control status
```

`select none` saves an empty selection. `disable` retains the selection for a
later deliberate `enable`. Selection changes require automatic application to
be disabled; an active override must be restored before deselecting that input.
A trial does not change the selection. Do not invoke `transaction.py` directly:
it requires an active coordinated intent and owner identity.

The monitor checks every 30 seconds while enabled. It applies after two stable
input reads, at least five minutes between application cycles, with at most
three cycles per daemon run. A busy trial is left alone. Unknown EDIDs,
calibration/OLED changes or mutation failures stop automatic application and
retain pending recovery. Status/logs in `/tmp/glasshouse-c9-edid` are
supplementary; persistent `operation.json` and transaction results are the
recovery authority. No network listener is added. Python 2.7 is required on the
TV; never use `-O`.

## Qualification and tests

Follow the [at-home test checklist](docs/TESTING.md) before enabling another
input. The public tests use synthetic EDIDs and an injected fake TV backend;
they exercise coordination and faults without opening a console or connecting
to a TV:

```sh
python3 -m unittest discover -s test -p 'test_*.py'
```

Offline tests do not qualify firmware console behavior, HDMI 3/4 routing,
audible channel mapping or standby/reboot reliability on hardware.

## Experimental HDMI 1 PCM 7.1 trial

`prepare-trial.py` can build a private **offline-only** eight-channel candidate:

```sh
python3 prepare-trial.py \
  --original /private/path/original-hdmi1.bin \
  --output-directory /private/path/new-pcm71-trial
```

It connects to no TV. The output directory must not exist. It contains a
manifest, the six-channel fallback and `TRIAL-NOT-INSTALLED-pcm71.bin`.
After qualifying recovery and the working six-channel preimage, the live
coordinator can run an explicit HDMI 1 trial:

```sh
/var/lib/glasshouse-c9-edid/control trial 1 --seconds 180 --profile pcm71-48k
```

This profile cannot be selected, applied persistently or used on HDMI 2, 3 or
4. The durable trial journal admits its exact derived bytes only for HDMI 1,
requires a rollback decision and unchanged persistent selection, and restores
the exact pretrial map. Without that journal, eight-channel candidate bytes
are rejected. The independent monitor and native preimage watchdog remain
required. Pause any legacy writer through the migration procedure first.

On the measured C9/UB820/Sonos Arc setup, the owner tested the German DTS-HD
High Resolution 7.1 track on Tron: Legacy UHD: Panasonic reported PCM 7.1 at
48 kHz, Sonos reported Multichannel PCM 7.1, and sound was normal. Exact working 5.1 preimage restoration, all-port readback, native console cleanup and calibration/OLED preservation passed afterward; the original monitor resumed. This is a
playback/negotiation result, not proof of discrete channel routing, DTS-HD
Master Audio lossless output, or native-app decoding. The default and normal
startup profile remain PCM 5.1. Separate trials retained Dolby Atmos and
normal sound from the English Tron UHD track, and normal sound from I, Robot's
DTS-HD Master Audio 5.1 track. For I, Robot the player and Sonos both reported
PCM 7.1 at 48 kHz despite the 5.1 source; the extra-channel content is not
established. Each trial restored and verified the exact working preimage.
An eight-channel InfoFrame or upmixed 5.1 track
does not establish correct 7.1 routing.

Glasshouse's read-only Audio diagnostics are in
[PR 1081](https://github.com/rorygallagher2024/glasshouse/pull/1081).
The G5 compressed-DTS forwarding work in
[PR 1007](https://github.com/rorygallagher2024/glasshouse/pull/1007)
is independent and supplies no C9 or Sonos DTS-HD/DTS:X decoder.

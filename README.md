# C9 HAWK2 audio EDID helper

Manual prototype for the C9 → Sonos eARC case where a Panasonic UB820 sends
stereo PCM directly, but PCM 5.1 through a fixed-EDID splitter. This is not a
Glasshouse dashboard feature and is not installed by `deploy.sh` or the IPK.

The underlying transaction and startup service passed trials on an
OLED65C9PUA, webOS 4.10.2, firmware 05.50.00. This per-TV backup adaptation
has not yet been run on a TV. It remains an experimental prototype.
It supports only the exact two firmware fingerprints in `state.py`, the
measured HAWK2/TrueHD declaration, and the measured 29-file calibration layout.
Other C9 firmware or regions are not implicitly supported.

## What it changes

HDMI 1 only: PCM maximum channels 8 → 6; sample rates through 48 kHz; a
standard 5.1 speaker-allocation block. DTS-HD's bitstream descriptor is removed
to make room in 256 bytes. The Panasonic decodes DTS/DTS-HD to PCM. AC3, EAC3,
MAT, the base EDID, video/HDR/Dolby Vision blocks and detailed timing stay
byte-identical. Core DTS remains declared. Other HDMI ports are verified
against their own originals.

This advertises capabilities; it adds no decoder to the TV or Sonos. The
combined edit solved the measured case; which individual field causes the
UB820's stereo choice was not isolated.

The existing native TV-service runtime writer is used through its diagnostic
console. Entry briefly changes one firmware-specific console-mode RAM word;
it is restored before the EDID load. F10 is queued while only the blocked
keyboard thread is briefly paused with ptrace, then detached with no register
or code changes. Console print/output flags are restored afterward. The
independent worker restores the original EDID if the controller does not hand
off verified success, or if post-load preservation/cleanup checks fail. An unfinished controlling
helper is stopped before recovery so it cannot issue a delayed write.
No flash-download or raw I2C route is used. A runtime load briefly renegotiates
HDMI and can interrupt playback. Software recovery cannot run through power
loss or guarantee recovery from every hardware fault.

## Preconditions

Use root SSH on the TV and keep a separate SSH session available. Obtain a
calibration backup separately: this helper captures preservation hashes and
white-balance values, **not the calibration/LUT files or EEPROM**. Do not run
it alongside another EDID tool or a diagnostic-console session.

Use the UB820 directly on HDMI 1 and Sonos on HDMI 2. Player DTS/DTS-HD is PCM,
Sound Effects Off; Dolby settings remain as tested. TV HDMI 1 input audio is
Bitstream, Digital Sound Output Pass Through, eARC On.

## Explicit installation

Copy this folder alone to `/tmp/c9-hawk2-edid` using the existing authenticated
SSH/SCP setup. No account, key or TV address is supplied by this repository.
On the active TV:

```sh
sh /tmp/c9-hawk2-edid/install.sh --prepare
```

The installer captures each TV's own original EDIDs and preservation baseline
under `/var/lib/glasshouse-c9-edid`, root-only. It never replaces an existing
backup and refuses unvalidated firmware, hardware, audio declarations or
calibration layout. Files include `backup.json`, `original-hdmi1.bin` and
`candidate-hdmi1.bin`; copy them privately off the TV before further testing.
Do not submit them in a public issue or PR.

`--prepare` installs files without a native EDID write or automatic startup.
After copying the backup off the TV, an explicit manual apply can be tested:

```sh
/usr/bin/python /var/lib/glasshouse-c9-edid/transaction.py apply
/var/lib/glasshouse-c9-edid/control disable
```

Wait for the transaction result under its printed guard directory before
accepting either outcome. After rollback is verified, explicitly enable
startup with `sh /tmp/c9-hawk2-edid/install.sh --enable`.

The opt-in hook `/var/lib/webosbrew/init.d/60-glasshouse-c9-edid` starts a
small Python 2.7 service, separate from Glasshouse. Every 30 seconds it reads
power state and, only while Active, HDMI 1. It applies only after two reads
of the exact original EDID. Applications are at least five minutes apart,
with three allowed per service run. Unknown EDIDs or a transaction failure
stop application. Firmware changes fail the binary checks before console
access. Existing Glasshouse startup hooks are not edited.

```sh
/var/lib/glasshouse-c9-edid/control status
/var/lib/glasshouse-c9-edid/control disable
```

Disable removes the enable marker, waits for the service and restores/verifies
the saved original. After successful disable, the hook can be removed; retain
the private backup. If the firmware changed, first remove the enable marker
manually to prevent startup attempts; do not bypass the fingerprint check to
force a restore through unvalidated addresses.

Each transaction has a private `/tmp/glasshouse-c9-edid-txn-*` result. Current
service state/logs are in `/tmp/glasshouse-c9-edid`. Logs may contain TV data;
share a redacted outcome rather than the complete files. No network listener
is added. Do not run either Python entry point with `-O`.

## Test sequence

1. Compare native HDMI 1 readback with the generated candidate, and HDMI 2–4
   with their originals. Confirm console detachment, factory white balance,
   calibration hashes and OLED-protection readbacks before accepting a commit.
2. Play the same DTS-HD track: UB820 HDMI info should show PCM 5.1, and Sonos
   should report audible Multichannel PCM 5.1. Confirm Atmos with a known
   Atmos disc under the same candidate. Record its transport subtype if shown.
3. Disable and verify exact original restoration, then re-enable deliberately.
4. Reboot, confirm a changed boot ID and automatic candidate readback, then
   repeat listening checks. Test standby/wake and HDMI/input changes separately.

The original single-TV implementation passed PCM 5.1 and Atmos playback,
independent rollback after controller exit, disable/restore and autonomous
reapplication after a reboot. Its final listening check after reboot was
not performed. Other sources, LPCM 7.1, native high-rate PCM, DTS:X, other
firmware and other models were not validated. No claim is made about HAWK2
runtime state surviving loss of power; startup reapplication supplies persistence.

## Offline preparation for a later 7.1 test

On a computer, starting with a private factory-original HDMI 1 EDID:

```sh
python3 prepare-trial.py \
  --original /private/path/original-hdmi1.bin \
  --output-directory /private/path/new-pcm71-trial
```

The output directory must not exist. It contains a manifest, the unchanged
six-channel fallback and `TRIAL-NOT-INSTALLED-pcm71.bin`, all private. The
trial retains the same PCM rate limit and Dolby declarations but advertises
eight PCM channels and an additional rear speaker pair. It has not been
playback-tested. This command connects to no TV and does not install a
transaction or change the startup profile.

Do not pass the trial to the six-channel installer or run a second EDID
service. When a working six-channel override is already installed, a future
bounded trial needs its own independent rollback to that exact working EDID,
plus restoration of the original service enable state. Verify this recovery
before listening tests. The factory-original restore command above deliberately
restores the factory EDID; it is not that trial's fallback.

Once recovery is verified, record a true 7.1 channel-identification track at
the Panasonic and receiver, then repeat the known DTS-HD 5.1 and Atmos tests.
Keep Sound Effects Off. An eight-channel InfoFrame or upmixed 5.1 track alone
does not verify the seven discrete speaker channels or LFE. No 7.1 profile
should be made persistent before audible routing and the 5.1 fallback pass.

## Status and offline checks

This standalone project contains the helper extracted from Glasshouse PR #1078.
The generalized
per-TV installer remains experimental and has not been qualified on hardware.
It must not replace an existing C9 audio service during an ongoing trial.

```sh
python3 test/test_edid.py
```

The helper is maintained separately from Glasshouse because it uses LG's
firmware-specific diagnostic console to alter runtime HDMI capabilities.
Glasshouse's read-only Audio diagnostics are tracked in
[PR 1081](https://github.com/rorygallagher2024/glasshouse/pull/1081).
The G5 compressed-DTS forwarding work in
[PR 1007](https://github.com/rorygallagher2024/glasshouse/pull/1007)
is an independent implementation and supplies no C9 or Sonos DTS-HD decoder.

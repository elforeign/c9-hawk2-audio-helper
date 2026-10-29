# Recovery and migration

These instructions describe the new helper. They are not authorization to
replace an existing working implementation unattended. Keep original backups
and the previous service available privately until qualification is complete.

Recovery here means **HDMI EDID recovery**. No command in this document
restores picture calibration, calibration files or EEPROM. Integrity readings
are used only to check that an audio operation left them unchanged. See
[backup scope](BACKUP-SCOPE.md) before planning recovery from a factory reset.

## Pending recovery

```sh
/var/lib/glasshouse-c9-edid/control status
/var/lib/glasshouse-c9-edid/control stop-trial
/var/lib/glasshouse-c9-edid/control recover
```

A detached trial's parent return does not prove it applied. The journal phase
`trial-active` means application checks passed and the timer is running.
`stop-trial` requests early preimage restoration. Wait for `operation.json` to
be archived to `history` and verify all input/preservation readbacks. A busy
operation lock means another owner is still working; do not bypass it.

A native worker can need up to 45 seconds for handoff/recovery. Its private
result is under `transactions/<transaction_id>/result.json`. If same-boot
console cleanup is not proven, the coordinator refuses another native session.
Wait for the independent worker and inspect its result. If no safe completion
is possible, restart the TV normally while physically present; leave the
backup, journal and recovery hook in place. The next boot uses fresh native
reads and firmware checks to restore the journal's target. It never reuses old
PIDs, RAM addresses captured from a process, or console terminal state.

Before application or even a no-op timed trial, the coordinator starts an
independent recovery monitor and requires its readiness record to match the
backup, boot, PID/start time and installed command. This also works with
automatic application disabled. If the timed worker exits unexpectedly, the
monitor retries recovery at two-second intervals after its operation lock is
released. A missing or mismatched readiness record prevents native writes.
The CLI worker surviving the SSH caller is a separate check from recovery
after that worker itself dies; qualification must exercise both.
Lock descriptors are close-on-exec, and child commands close unrelated
descriptors. A recovery daemon must never inherit its controller's operation
lock: Python 2 would otherwise keep it locked after that controller dies.

The minimal C9 Python omits `threading` and `argparse`. Controller deadlines
use a scoped POSIX alarm while retaining direct-child ownership. An existing
alarm causes refusal rather than being replaced. The runtime CLI has a strict
internal parser; the offline candidate-generation tool runs on a computer.

The startup hook runs when a journal or disable request exists even without
`enabled`. Recovery waits until the TV is Active. If a firmware fingerprint,
EDID or preservation check fails, it stops. Do not edit the journal, erase its
native-pending record, bypass the firmware pins or force an unknown EDID into a
known state. Retain the private evidence for manual review. A diagnostic read
failure is not proof of restoration.

## Disable and uninstall

```sh
/var/lib/glasshouse-c9-edid/control disable
/var/lib/glasshouse-c9-edid/control status
```

Disable is asynchronous: it cancels a trial, restores every writable input to
its own original, and retains the saved selection. Wait for `disabled;
originals verified`, no pending recovery and exact all-port readback before
removing the hook or code. Retain the immutable backup and calibration backup.
If firmware has changed, remove only the enable marker to prevent automatic
application and seek manual review; never force the unqualified native writer.

## Adopting the earlier private HDMI 1 implementation

The legacy service under `/var/lib/c9-hdmi1-audio` must not run alongside this
helper. An enabled legacy marker causes refusal, and both writers share a
native lock. Before migration, while home:

1. Preserve the old service, its immutable HDMI 1 original, existing candidate
   and verified calibration backup off the TV. Confirm the working native
   HDMI 1 readback and all other originals. Verify no legacy transaction or
   console session is pending.
2. Pause the legacy automatic service without running its factory restore:
   remove its `enabled` marker and wait for that daemon to exit. Verify its
   exact implementation's shutdown behavior before doing this; do not guess a
   stop command or kill an active native worker. Keep HDMI 1's working candidate
   in place and retain the old hook/code for rollback.
3. Copy the saved original to a private file on the TV, then explicitly adopt:

   ```sh
   sh /tmp/c9-hawk2-edid/install.sh --prepare --ports 1 \
     --adopt-hdmi1-original /private/path/original-hdmi1.bin
   ```

   This is capture-only. Adoption requires current HDMI 1 to equal the exact
   six-channel candidate rebuilt from the supplied original. The new backup
   stores the supplied original and records the observed working candidate
   separately. A mismatch is refused. Existing backups cannot be replaced by
   adoption; do not delete one to get around this check.
4. Copy the new backup off the TV and verify originals, preservation and
   current HDMI 1 candidate. Qualify the new coordinator with the checklist
   before enabling it. Never enable both startup services.

For rollback to the old implementation, first disable the new coordinator,
wait for all originals to be verified and all journals resolved, then use the
previous implementation's already verified apply/enable procedure. Never
switch ownership by deleting a recovery journal. Glasshouse updates are
separate: preserve any local owner write-policy guard when upgrading its core.

An experimental `pcm71-48k` trial is rollback-only on HDMI 1. Its journal must
retain `trial_profile`, `trial_port`, both exact maps and the unchanged
persistent selection. Recovery accepts eight-channel bytes only through that
validated journal and returns to its preimage, including an existing working
six-channel override. Do not delete the journal or reinstall an older helper
that cannot recognize it while the trial is pending. This profile cannot be
saved for automatic startup.

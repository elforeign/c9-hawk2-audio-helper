# At-home qualification checklist

Status: the multi-port coordinator is offline-tested only. Do not replace a
working HDMI 1 service while away from the TV. No hardware test on HDMI 3 or 4
has been performed for this version.

1. Preserve the existing working HDMI 1 implementation, original EDID and
   verified calibration backup off the TV. Review migration and recovery first.
   Keep two root SSH sessions available; close other diagnostic sessions.
2. Prepare with automatic application disabled. Verify the private backup
   contains four distinct originals and per-port CEC addresses. Verify the
   preserved calibration, factory white balance and OLED baseline against the
   existing backup. Preparation must perform no native write.
3. With Panasonic directly on HDMI 1 and Sonos on HDMI 2, verify the known
   working baseline: DTS/DTS-HD output PCM, Sound Effects Off; TV input audio
   Bitstream, Digital Sound Output Pass Through and eARC On. Record the
   Panasonic channel count/rate and Sonos Audio In for the same DTS-HD track.
   Repeat the known Atmos disc. Confirm audible output, not only UI labels.
4. Test coordinated HDMI 1 apply and restore first, including the independent
   native result and exact all-port/preservation readbacks. Confirm native
   console cleanup and no pending journal. Do not accept success from a
   detached worker's startup message alone.
5. Trial HDMI 3 alone using `control trial 3 --seconds 180`. First verify the
   preimage includes any working HDMI 1 candidate. Moving the test source to
   HDMI 3 briefly interrupts playback; move only when physically ready.
   Confirm PCM 5.1 at 48 kHz and audible channels, then Atmos. Wait for automatic
   rollback; compare all four inputs to the exact pretrial map. Persistent
   selection and HDMI 1 must remain unchanged.
6. Repeat separately for HDMI 4. If the normal source on an input already
   works correctly, leave its original capabilities alone. Test that source
   separately; a Panasonic success does not qualify every source.
7. Only after each single-port trial passes, select the desired qualified
   inputs, apply, and verify them together. Restore one input and verify the
   other applied profiles and HDMI 2 remain intact.
8. Exercise disable/restore and interrupted-operation recovery while present.
   Use controlled helper-process interruption, never raw firmware process kills
   or forced power loss during console access. Preserve the journal/results.
   See RECOVERY.md if console cleanup is uncertain. Do not intentionally cut
   mains power during a native transaction.
9. Enable startup only after restoration is proven. Test a normal reboot,
   standby/wake, input switching and source reconnect. For each, record boot ID,
   all-port EDID equality, console cleanup, unchanged preservation baseline,
   audible DTS-HD→PCM 5.1 and Atmos. Verify normal CEC and eARC operation too.

Do not change picture calibration, service-menu values or OLED protections for
these tests. Built-in TV apps and PCM 7.1 are separate investigations; this
helper does not add DTS-HD decoding to them. Share a redacted pass/fail table,
source model/settings, firmware version and audio format; keep private data
out of issues and PRs.

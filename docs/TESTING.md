# At-home qualification checklist

Status on 28 October 2026: C9 Python 2.7 synthetic fixtures, preparation and
no-op trial-worker termination/recovery passed with zero native EDID writes.
The original HDMI 1 monitor resumed. With the Panasonic UB820 directly
connected and Sonos Arc on HDMI 2, the owner confirmed:

| Input | Original/current baseline | Temporary six-channel profile | Atmos with profile |
| --- | --- | --- | --- |
| HDMI 1 | Existing persistent workaround: audible Multichannel PCM 5.1 | Existing candidate retained for the no-op recovery proof | Dolby Atmos with normal sound |
| HDMI 3 | Stereo PCM with sound | Multichannel PCM 5.1 with normal sound | Dolby Atmos with normal sound |
| HDMI 4 | Stereo PCM with sound | Multichannel PCM 5.1 with normal sound | Dolby Atmos with normal sound |

The DTS source was the I, Robot DTS-HD track, decoded to PCM by the player with
Sound Effects Off. A fresh player channel/rate readout and discrete channel
identification were not recorded for the HDMI 3/4 trials; Sonos labels and
audible output are owner observations. Each HDMI 3/4 trial ended with exact
all-four EDID restoration verified twice, clean native-console cleanup,
unchanged calibration/OLED checks, no pending operation and the legacy monitor
resumed. HDMI 2 was never a write target. The first HDMI 4 DTS window expired
before an owner observation; a separate repeated trial supplied the result.

The generalized helper is prepared with startup disabled and selection HDMI 1.
The original HDMI 1 implementation remains responsible for normal startup.
Combined apply, single-port restore with other profiles active, and generalized
reboot/standby/input-switch reliability remain pending. A separate explicit HDMI 1 PCM 7.1 trial subsequently produced normal sound
with Panasonic PCM 7.1 at 48 kHz and Sonos Multichannel PCM 7.1 from Tron:
Legacy UHD's German DTS-HD High Resolution 7.1 track. Discrete routing and
Master Audio lossless output were not established. Native-app DTS remains
untested. Do not replace a
working service while away from the TV.

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
   First run a no-op HDMI 1 timed trial against the already working candidate
   with automatic application disabled. Confirm recovery-monitor readiness,
   terminate only that trial worker, and verify automatic journal recovery,
   all-port equality and no native write. Do not proceed if this fails.
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

## Experimental eight-channel profile

The six-channel profile remains the only persistent selection. Before the
explicit HDMI 1 `--profile pcm71-48k` trial, repeat native Python fixtures and
no-op worker-termination recovery using the updated installed source. Confirm
all inputs and preservation equal the working preimage, old/new writer
coordination, independent monitor readiness and no pending native cleanup.
Verify the eight-channel candidate derives from the immutable original and
preserves every non-audio block. During the bounded trial, record both the
player's output rate/channel count and Sonos Audio In with audible sound.
Restore and verify the exact working six-channel preimage; never promote the
trial to startup based on a movie format label alone. Repeat known 5.1 and
Atmos sources under separate bounded trials, then use a real channel-ID source
to investigate discrete routing. High Resolution and Master Audio are separate
source formats and must be labelled accurately.

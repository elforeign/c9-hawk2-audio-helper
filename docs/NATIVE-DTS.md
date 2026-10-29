# Native player DTS observation on the measured C9

On 28 October 2026, the owner tested the C9's installed Photo & Video app
with generated H.264/Matroska clips staged in a temporary directory. The
installed app's existing payload/deep-link path played the local file; no USB,
new app, codec installation, firmware patch or sound-setting change was used.
This is a native local-file observation, not a physical-USB or Plex test.

TV: OLED65C9PUA, webOS 4.10.2 / firmware 05.50.00. Receiver: Sonos Arc on HDMI 2.
Sound output remained external ARC, Digital Sound Output Pass Through, eARC On.
The HDMI 1 runtime profile stayed at the previously verified six-channel map.
No EDID was written during the native-app tests.

| Generated audio source | Owner observation | Sonos Audio In |
| --- | --- | --- |
| DTS core 5.1, 48 kHz, encoder rate 1,536,000 bps | Video, silent audio | No format shown |
| AAC stereo, 48 kHz, 128 kb/s control | Video and steady tone audible | Dolby Digital 2.0 |
| DTS core 5.1, 48 kHz, encoder rate 1,509,750 bps | Labelled tones audible | DTS Surround 5.1 |

The TV reported a six-channel DTS source and normal video playback for the
silent sample. Read-only `/proc/aaudio/module/DEC_0/status` showed an active
DTS decoder at 48 kHz with `Core Fsize` 2047; the audible second sample showed
2015. ARC output status was unmuted with data flowing. These are driver
observations, not independently verified speaker routing or bit-perfect output.
Unused DEC_2/DEC_3 status reads returned EINVAL and were recorded as unavailable.

The rate/frame-size comparison points to sample encoding or framing
compatibility. It does not isolate the cause to the TV, receiver, or a specific
IEC 61937 packing rule. A generated file that decodes in software is not by
itself a sufficient interoperability reference. Do not infer that native DTS
is missing from the C9 based on the silent first file, or patch drivers to
work around that file. The [FFmpeg IEC 61937 muxer](https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/spdifenc.c)
is a useful reference for its distinct handling of DTS packet sizes.

The successful second sample establishes audible native DTS-core playback
reaching this Sonos as DTS 5.1. It does not establish DTS-HD Master Audio,
DTS-HD High Resolution or DTS:X native-app playback. It is independent of the
Panasonic HDMI trial, which decodes DTS on the player and sends PCM. A future
Plex test must verify Direct Play rather than server-transcoded audio.

The clips use original tones, not film audio. They are for coarse playback
observation, not a precision calibration reference. No professional calibration
archive is involved. All four EDIDs, calibration file hashes, factory white
balance and OLED protection readbacks were checked after the test. The test
player was closed normally and HDMI 1 restored. Temporary media and private
capture files were copied off the TV before cleanup; no owner media was removed.

# Backup scope

The audio fix and professional-calibration recovery are separate concerns.
Installing, applying, disabling or recovering this helper never imports or
restores a calibration archive. The helper is independent of Glasshouse's
Settings backup and of any calibration-backup tool.

| Record | Purpose | Professional calibration after a factory reset |
| --- | --- | --- |
| Glasshouse Settings backup | Supported sound, device, HDMI and ad settings, Glasshouse settings, Home layout and an app list | Not included; picture settings, calibration LUTs and EEPROM contents are outside the current backup |
| This helper's EDID rollback record | The same TV's per-input original EDIDs, known audio candidates and operation recovery | Not supported; calibration readings are integrity checks only |
| A dedicated calibration archive | Separately captured picture/calibration data with a model-specific restore procedure | Coverage and recovery must be verified separately for that TV and firmware |

Glasshouse's current backup module explicitly collects `promotions`, `sound`,
`devices` and `hdmi`. It does not collect the `picture` section or copy
calibration files/EEPROM. Its daily backups use the same format; taking them
more frequently does not expand their coverage. See the upstream
[backup implementation](https://github.com/rorygallagher2024/glasshouse/blob/main/server/lib/backup.js)
and [Server documentation](https://github.com/rorygallagher2024/glasshouse/blob/main/docs/dashboard/server.md).

## What the audio helper keeps

`/var/lib/glasshouse-c9-edid/backup.json` belongs only to EDID rollback. It
contains each input's original EDID and candidate checksums, firmware identity,
and a read-only integrity snapshot. Journals and native results belong to the
same audio operation, not a full-system backup. The helper's `restore` command
restores input EDIDs; it has no picture-calibration restore operation.

The integrity snapshot includes calibration-file hashes, factory white-balance
readings and OLED-protection readings. Comparing them prevents an audio
operation from continuing after an unexpected change. Hashes cannot recreate
the files they describe, and those white-balance readings alone are not a
complete calibrated picture profile. These checks remain enabled; separating
the backup purposes does not remove protection for calibrated TVs.

## Factory resets and calibrated TVs

Do not rely on either Glasshouse's Settings backup or this helper's rollback
record to recreate a professional calibration after a factory reset. A
dedicated archive needs explicit coverage of the calibrated SDR, HDR10 and
Dolby Vision modes and input contexts, relevant settings and LUTs, and any
device-specific calibration storage used by that model. Calibrator reports
and measured results can document a calibration without containing every
setting or programmable LUT needed to restore it.

Verifying an archive's file hashes proves the saved files are intact; it does
not by itself prove complete coverage or a successful restore. A supported
restore procedure must establish correct association with the same TV/panel
and firmware, restore ordering and post-restore verification. Never transplant
another panel's calibration or treat a raw EEPROM image as a generic restore
file. Do not perform a factory reset merely to test coverage.

Keep dedicated calibration archives privately off the TV, separately from EDID
rollback files. Do not add calibration restore actions to the audio workflow.
If a reset or recalibration changes the integrity baseline, stop and review;
do not overwrite the original EDID record or relax checks to make it pass.

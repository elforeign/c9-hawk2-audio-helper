"""Build an offline eight-channel candidate; never connect to or write a TV."""

from __future__ import print_function
import argparse
import hashlib
import json
import os
from edid import build_candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--original", required=True, help="Privately saved original HDMI1 EDID"
    )
    parser.add_argument(
        "--output-directory",
        required=True,
        help="New private directory; must not exist",
    )
    args = parser.parse_args()
    with open(args.original, "rb") as source:
        original = source.read()
    fallback = build_candidate(original)
    trial = build_candidate(original, channels=8)
    os.mkdir(args.output_directory, 0o700)
    manifest = {
        "status": "PREPARED ONLY; playback unverified",
        "pcm_rates_khz": [32, 44.1, 48],
        "fallback_channels": 6,
        "trial_channels": 8,
        "original_sha256": hashlib.sha256(original).hexdigest(),
        "fallback_sha256": hashlib.sha256(fallback).hexdigest(),
        "trial_sha256": hashlib.sha256(trial).hexdigest(),
        "automatic_installation": False,
    }
    for name, data in [
        ("fallback-pcm51.bin", fallback),
        ("TRIAL-NOT-INSTALLED-pcm71.bin", trial),
    ]:
        with open(os.path.join(args.output_directory, name), "wb") as target:
            target.write(data)
        os.chmod(os.path.join(args.output_directory, name), 0o600)
    with open(os.path.join(args.output_directory, "manifest.json"), "w") as target:
        json.dump(manifest, target, sort_keys=True, indent=2)
    os.chmod(os.path.join(args.output_directory, "manifest.json"), 0o600)
    print(json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    main()

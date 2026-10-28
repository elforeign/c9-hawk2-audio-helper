"""Offline, fail-closed CTA edit for the measured C9/UB820 PCM 5.1 case."""

from __future__ import print_function
import binascii


def require(condition, message):
    if not condition:
        raise ValueError(message)


def as_bytes(values):
    return binascii.unhexlify("".join("%02x" % value for value in values))


def parse(data):
    values = bytearray(data)
    require(len(values) == 256, "Expected one base block and one CTA block")
    require(
        values[:8] == bytearray(b"\x00\xff\xff\xff\xff\xff\xff\x00"),
        "Invalid EDID header",
    )
    require(values[126] == 1, "Unexpected extension count")
    require(
        all(sum(values[start : start + 128]) % 256 == 0 for start in (0, 128)),
        "Invalid checksum",
    )
    require(values[128:130] == bytearray([2, 3]), "Expected CTA revision 3")
    end = values[130]
    require(4 <= end <= 127, "Invalid CTA data boundary")
    blocks = []
    cursor = 132
    while cursor < 128 + end:
        length = values[cursor] & 31
        require(length > 0 and cursor + length + 1 <= 128 + end, "Truncated CTA block")
        blocks.append(as_bytes(values[cursor : cursor + length + 1]))
        cursor += length + 1
    require(cursor == 128 + end, "Invalid CTA block boundary")
    return blocks


def build_candidate(data, channels=6):
    require(
        channels in (6, 8),
        "Only the tested six-channel or trial eight-channel profile is defined",
    )
    blocks = parse(data)
    audio = [block for block in blocks if bytearray(block)[0] >> 5 == 1]
    require(len(audio) == 1, "Expected one audio block")
    require(
        not any(bytearray(block)[0] >> 5 == 4 for block in blocks),
        "Existing speaker allocation needs separate validation",
    )
    payload = bytearray(audio[0])[1:]
    require(len(payload) % 3 == 0, "Invalid short audio descriptors")
    descriptors = [
        as_bytes(payload[index : index + 3]) for index in range(0, len(payload), 3)
    ]
    # These exact descriptors were measured on the validated firmware. Avoid
    # guessing how a different model's audio declaration should be changed.
    expected = ["0f5707", "150750", "570701", "670403", "3d1ec0", "5f7e01"]
    require(
        descriptors == [binascii.unhexlify(value) for value in expected],
        "Unvalidated audio declaration",
    )
    replacement = as_bytes([0x08 | (channels - 1), 7, 7]) + binascii.unhexlify(
        "1507505707016704033d1ec0"
    )
    replacement_block = as_bytes([0x20 | len(replacement)]) + replacement
    output_blocks = []
    for block in blocks:
        output_blocks.append(replacement_block if block == audio[0] else block)
        if block == audio[0]:
            # CTA speaker bits 0..3 describe the 5.1 set; bit 6 adds
            # RLC/RRC for an eight-channel layout. Eight channels is a trial,
            # not a change to the default capture/install profile.
            output_blocks.append(
                as_bytes([0x83, 0x0F if channels == 6 else 0x4F, 0, 0])
            )
    collection = b"".join(output_blocks)
    values = bytearray(data)
    old_start = 128 + values[130]
    # Keep every detailed timing byte, accepting only the measured single DTD
    # followed by zero padding. The extra byte comes from that padding.
    tail = values[old_start:255]
    require(
        len(tail) >= 19 and any(tail[:18]) and not any(tail[18:]),
        "Unexpected detailed timings/padding",
    )
    new_start = 4 + len(collection)
    require(new_start + 18 <= 127, "No room for speaker allocation")
    extension = (
        bytearray([2, 3, new_start, values[131]]) + bytearray(collection) + tail[:18]
    )
    extension += bytearray(127 - len(extension))
    extension.append((-sum(extension)) & 255)
    candidate = as_bytes(values[:128]) + as_bytes(extension)
    parse(candidate)
    require(candidate[:128] == data[:128], "Base block changed")
    require(
        [block for block in parse(candidate) if bytearray(block)[0] >> 5 not in (1, 4)]
        == [block for block in blocks if bytearray(block)[0] >> 5 not in (1, 4)],
        "Non-audio blocks changed",
    )
    return candidate

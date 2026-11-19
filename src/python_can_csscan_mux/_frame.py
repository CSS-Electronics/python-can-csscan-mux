"""Encoding and decoding of single CAN frames in the CANmod.router mux format.

Each muxed frame is a small header followed by the frame data:

    byte 0:  [7:5] port (S1 = 0 ... S4 = 3)  [4] extended ID  [3] CAN FD  [2:0] DLC bits 3..1
    byte 1:  [7] DLC bit 0  [6] transmitted (not received)  [5] BRS (CAN FD) / RTR (classic CAN)  [4:0] ID high bits
    then:    remaining ID bytes (1 byte for 11-bit IDs, 3 bytes for 29-bit IDs), then the data bytes

Error frames are encoded as two bytes: byte 0 with the low five bits set to 0b00111, and byte 1 holding the error code.

Several frames may be concatenated into one ISO-TP payload.
"""

from __future__ import annotations

from typing import List, Tuple

import can
from can.util import dlc2len, len2dlc

_ERROR_MARKER = 0x07


def encode_frame(message: can.Message, port: int) -> bytes:
    """Encode a single CAN message, destined for (or received on) secondary `port`."""
    head = (port & 0x07) << 5

    if message.is_error_frame:
        code = message.data[0] if message.data else 0
        return bytes([head | _ERROR_MARKER, code])

    if message.is_remote_frame:
        dlc = message.dlc
        data = b""
    else:
        if not message.is_fd and len(message.data) > 8:
            raise ValueError("Classic CAN messages can carry at most 8 bytes")
        dlc = len2dlc(len(message.data))
        data = bytes(message.data).ljust(dlc2len(dlc), b"\x00")

    byte0 = head | (message.is_extended_id << 4) | (message.is_fd << 3) | (dlc >> 1)
    byte1 = (dlc & 0x01) << 7
    if not message.is_rx:
        byte1 |= 0x40
    if message.bitrate_switch if message.is_fd else message.is_remote_frame:
        byte1 |= 0x20

    if message.is_extended_id:
        can_id = message.arbitration_id & 0x1FFFFFFF
        header = [byte0, byte1 | (can_id >> 24), (can_id >> 16) & 0xFF, (can_id >> 8) & 0xFF, can_id & 0xFF]
    else:
        can_id = message.arbitration_id & 0x7FF
        header = [byte0, byte1 | (can_id >> 8), can_id & 0xFF]

    return bytes(header) + data


def decode_frames(payload: bytes) -> List[Tuple[int, can.Message]]:
    """Decode all frames in a payload into (port, message) pairs.

    Decoding stops at the first malformed or truncated frame, since the frame boundaries after it are unknown.
    """
    frames = []
    pos = 0

    while pos < len(payload):
        byte0 = payload[pos]
        port = byte0 >> 5

        if byte0 & 0x1F == _ERROR_MARKER:
            if pos + 2 > len(payload):
                break
            message = can.Message(is_error_frame=True, data=payload[pos + 1:pos + 2])
            frames.append((port, message))
            pos += 2
            continue

        is_extended_id = bool(byte0 & 0x10)
        is_fd = bool(byte0 & 0x08)
        id_end = pos + (5 if is_extended_id else 3)
        if id_end > len(payload):
            break

        byte1 = payload[pos + 1]
        dlc = (byte0 & 0x07) << 1 | byte1 >> 7
        if not is_fd and dlc > 8:
            break

        flag = bool(byte1 & 0x20)
        is_remote_frame = flag and not is_fd
        can_id = int.from_bytes(payload[pos + 1:id_end], "big") & (0x1FFFFFFF if is_extended_id else 0x7FF)

        data_end = id_end if is_remote_frame else id_end + dlc2len(dlc)
        if data_end > len(payload):
            break

        message = can.Message(
            arbitration_id=can_id,
            is_extended_id=is_extended_id,
            is_fd=is_fd,
            bitrate_switch=flag and is_fd,
            is_remote_frame=is_remote_frame,
            is_rx=not byte1 & 0x40,
            dlc=dlc2len(dlc),
            data=payload[id_end:data_end],
        )
        frames.append((port, message))
        pos = data_end

    return frames

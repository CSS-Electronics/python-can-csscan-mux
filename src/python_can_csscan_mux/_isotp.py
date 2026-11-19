"""Minimal ISO-TP (ISO 15765-2) packing and reassembly, as used by the CANmod.router mux.

There is no flow control: multi-frame payloads are sent back-to-back.
"""

from __future__ import annotations

from typing import List, Optional

from can.util import dlc2len, len2dlc

_PADDING = 0xCC


def _pad(frame: bytearray) -> bytes:
    """Pad a frame up to the next valid CAN (FD) data length."""
    return bytes(frame.ljust(dlc2len(len2dlc(len(frame))), bytes([_PADDING])))


def pack(payload: bytes, mtu: int) -> List[bytes]:
    """Split a payload into ISO-TP frames of at most `mtu` bytes (8 for classic CAN, 64 for CAN FD)."""
    single_frame_capacity = mtu - 1 if mtu <= 8 else mtu - 2

    if len(payload) <= single_frame_capacity:
        if len(payload) <= 7:  # Fits in a frame of up to 8 bytes: length in the first byte
            header = bytearray([len(payload)])
        else:  # CAN FD frame of more than 8 bytes: escape, with the length in the second byte
            header = bytearray([0x00, len(payload)])
        return [_pad(header + payload)]

    frames = [bytes([0x10 | (len(payload) >> 8), len(payload) & 0xFF]) + payload[:mtu - 2]]
    sequence_number = 1
    for start in range(mtu - 2, len(payload), mtu - 1):
        frames.append(_pad(bytearray([0x20 | sequence_number]) + payload[start:start + mtu - 1]))
        sequence_number = (sequence_number + 1) & 0x0F

    return frames


class Reassembler:
    """Reassembles ISO-TP payloads from the frames of a single CAN ID."""

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self._buffer = bytearray()
        self._remaining = 0
        self._sequence_number = 0

    def feed(self, data: bytes) -> Optional[bytes]:
        """Feed the data of one frame. Returns the payload once complete, otherwise None."""
        if not data:
            return None

        frame_type = data[0] >> 4

        if frame_type == 0x0:  # Single frame
            self._reset()
            length, start = data[0] & 0x0F, 1
            if length == 0:  # CAN FD escape: length in the second byte
                if len(data) < 2:
                    return None
                length, start = data[1], 2
            if start + length > len(data):
                return None
            return bytes(data[start:start + length])

        if frame_type == 0x1:  # First frame
            self._reset()
            if len(data) < 2:
                return None
            length, start = (data[0] & 0x0F) << 8 | data[1], 2
            if length == 0:  # Escape for payloads above 4095 bytes: length in the next four bytes
                if len(data) < 6:
                    return None
                length, start = int.from_bytes(data[2:6], "big"), 6
            self._buffer = bytearray(data[start:start + length])
            self._remaining = length - len(self._buffer)
            self._sequence_number = 1
            return None

        if frame_type == 0x2:  # Consecutive frame
            if self._remaining == 0 or data[0] & 0x0F != self._sequence_number:
                self._reset()
                return None
            chunk = data[1:1 + self._remaining]
            self._buffer += chunk
            self._remaining -= len(chunk)
            self._sequence_number = (self._sequence_number + 1) & 0x0F
            if self._remaining == 0:
                payload = bytes(self._buffer)
                self._reset()
                return payload

        return None

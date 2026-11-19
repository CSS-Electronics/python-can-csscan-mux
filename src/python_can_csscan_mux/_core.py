from __future__ import annotations

from collections import defaultdict
from typing import Any, DefaultDict, Hashable, Iterable, Iterator, List, Sequence, Tuple

import can

from ._frame import decode_frames, encode_frame
from ._isotp import Reassembler, pack

# Defaults matching the CANmod.router default configuration
MUX_IDS = (0x010, 0x011)
"""CAN IDs carrying muxed data: 0x010 from the router, 0x011 to the router."""
TX_ID = 0x011
"""CAN ID used when sending muxed data to the router."""
CHANNEL_MAP = (0, 1, 2, 3)
"""Channels assigned to the router's secondary ports S1, S2, S3 and S4."""


def _check_channels(channel: Any, channel_map: Sequence[Any]) -> None:
    if channel is not None and channel in channel_map:
        raise ValueError(
            f"The muxed traffic is on channel {channel!r}, which is also in channel_map {tuple(channel_map)}. "
            f"Pass a channel_map without {channel!r}, so messages from S1-S4 get channels of their own."
        )


class Demuxer:
    """Stateful demuxer: feed it messages one at a time, get demuxed messages back.

    Useful when messages arrive one by one, e.g. from `bus.recv()`. For iterables, :func:`demux` is simpler.
    See :func:`demux` for a description of the arguments.
    """

    def __init__(
        self,
        *,
        channel: Any = None,
        mux_ids: Iterable[int] = MUX_IDS,
        extended: bool = False,
        channel_map: Sequence[Any] = CHANNEL_MAP,
        passthrough: bool = True,
    ) -> None:
        _check_channels(channel, channel_map)
        self.channel = channel
        self.mux_ids = frozenset(mux_ids)
        self.extended = extended
        self.channel_map = tuple(channel_map)
        self.passthrough = passthrough
        self._reassemblers: DefaultDict[Tuple[Hashable, int], Reassembler] = defaultdict(Reassembler)

    def is_muxed(self, message: can.Message) -> bool:
        """Whether the message carries muxed data according to this configuration."""
        return (
            message.arbitration_id in self.mux_ids
            and message.is_extended_id == self.extended
            and not message.is_error_frame
            and not message.is_remote_frame
            and (self.channel is None or message.channel == self.channel)
        )

    def feed(self, message: can.Message) -> List[can.Message]:
        """Process one message. Returns zero or more demuxed (or passed through) messages."""
        if not self.is_muxed(message):
            return [message] if self.passthrough else []
        if message.channel is not None:
            # With channel=None the overlap can only be detected once muxed traffic arrives
            _check_channels(message.channel, self.channel_map)

        payload = self._reassemblers[(message.channel, message.arbitration_id)].feed(message.data)
        if payload is None:
            return []

        demuxed = []
        for port, frame in decode_frames(payload):
            if port >= len(self.channel_map) or self.channel_map[port] is None:
                continue
            frame.channel = self.channel_map[port]
            frame.timestamp = message.timestamp
            demuxed.append(frame)
        return demuxed


def demux(
    messages: Iterable[can.Message],
    *,
    channel: Any = None,
    mux_ids: Iterable[int] = MUX_IDS,
    extended: bool = False,
    channel_map: Sequence[Any] = CHANNEL_MAP,
    passthrough: bool = True,
) -> Iterator[can.Message]:
    """Demux CANmod.router traffic into the messages of its secondary ports.

    :param messages: Any iterable of messages, e.g. a `can.LogReader`, a list, or a `can.BusABC` instance.
    :param channel: Only demux messages on this channel. None demuxes muxed messages on any channel.
    :param mux_ids: CAN IDs carrying muxed data.
    :param extended: Whether `mux_ids` are 29-bit (extended) IDs.
    :param channel_map: Channels to assign to messages from S1, S2, S3 and S4. Use None to drop a port.
        Must not contain `channel`.
    :param passthrough: Yield messages that are not muxed unchanged. If False, they are dropped.
    :return: An iterator of demuxed messages. Each has the timestamp of the muxed message that completed it.

    Error frames from the secondary ports are demuxed as messages with `is_error_frame` set and the router error
    code in `data[0]`: 1 = bit error, 2 = form error, 3 = stuff error, 4 = CRC error, 5 = acknowledge error.
    """
    demuxer = Demuxer(
        channel=channel, mux_ids=mux_ids, extended=extended, channel_map=channel_map, passthrough=passthrough
    )
    for message in messages:
        yield from demuxer.feed(message)


def mux(
    message: can.Message,
    *,
    channel: Any = None,
    mux_id: int = TX_ID,
    extended: bool = False,
    channel_map: Sequence[Any] = CHANNEL_MAP,
    fd: bool = True,
    brs: bool = True,
    passthrough: bool = True,
) -> List[can.Message]:
    """Mux a message for transmission on one of the CANmod.router secondary ports.

    The secondary port is selected by looking up `message.channel` in `channel_map`. Messages on other channels are
    regular messages for the bus itself, and are returned unchanged (see `passthrough`).

    :param message: The message to mux.
    :param channel: Channel to set on the muxed messages (the channel the router is connected to).
    :param mux_id: CAN ID to send the muxed data on.
    :param extended: Whether `mux_id` is a 29-bit (extended) ID.
    :param channel_map: Channels assigned to S1, S2, S3 and S4. Must not contain `channel`.
    :param fd: Send the muxed data as CAN FD frames (up to 64 bytes) instead of classic CAN frames.
    :param brs: Use CAN FD bit rate switching. Requires `fd`.
    :param passthrough: Return messages whose channel is not in `channel_map` unchanged, to be sent directly on the
        bus. If False, they raise a ValueError instead.
    :return: One or more messages to send, in order.
    """
    if brs and not fd:
        raise ValueError("brs requires fd")
    _check_channels(channel, channel_map)

    ports = [port for port, mapped in enumerate(channel_map) if mapped is not None and mapped == message.channel]
    if not ports:
        if passthrough:
            return [message]
        raise ValueError(f"Message channel {message.channel!r} is not in channel_map {tuple(channel_map)}")

    payload = encode_frame(message, ports[0])
    return [
        can.Message(
            arbitration_id=mux_id,
            is_extended_id=extended,
            is_fd=fd,
            bitrate_switch=brs,
            channel=channel,
            data=data,
        )
        for data in pack(payload, mtu=64 if fd else 8)
    ]

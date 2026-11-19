# python-can-csscan-mux

Mux and demux CAN data to/from the [CSS Electronics](https://www.csselectronics.com/) CANmod.router.

The CANmod.router combines the traffic of its four secondary CAN ports (S1–S4) into a single CAN ID on its primary port (ISO-TP over CAN). This package converts between that muxed traffic and regular messages:

- `demux(messages)` takes muxed `can.Message`s and yields the original messages of S1–S4.
- `mux(message)` turns a message meant for S1–S4 into muxed `can.Message`s to send to the router.

Both work on plain [python-can](https://python-can.readthedocs.io/) `can.Message` objects. They don't care where messages come from or go to: a live bus, a log file, a list, or anything else.

```
pip install python-can-csscan-mux
```

## Channels

Every `can.Message` has a `channel`. Demuxed messages get a channel per secondary port, taken from `channel_map` (default `(0, 1, 2, 3)` for S1–S4). To mux a message, set its `channel` to the channel of the port to send it on.

These port channels must differ from the channel of the muxed traffic, i.e. the channel your interface reports for the bus the router's primary port is connected to. What that channel is depends on the interface: e.g. interfaces that number their channels `1`, `2`, ... overlap the default `channel_map`. Print a received message to see it. If they overlap, `demux` and `mux` raise a `ValueError`. Then pass a `channel_map` of your own, e.g. `channel_map=("S1", "S2", "S3", "S4")` or `channel_map=(11, 12, 13, 14)`.

## Demux

The router is often added to an existing bus with other traffic. `demux()` yields both the demuxed messages from S1–S4 and the regular messages of that bus, in the order they arrive. Tell them apart by their channel:

```python
import can
from python_can_csscan_mux import demux

ports = ("S1", "S2", "S3", "S4")

# Any python-can interface, connected to the bus with the router's primary port
with can.Bus(interface="...", channel="...") as bus:
    for msg in demux(bus, channel_map=ports):
        if msg.channel in ports:
            print(f"Router port {msg.channel}: {msg}")  # Demuxed from the router
        else:
            print(f"Bus: {msg}")  # Regular message on the bus
```

Iterating a `can.Bus` receives messages until you `break` out of the loop (or press Ctrl+C). `demux()` accepts any iterable of messages, so a log file works the same way: `demux(can.LogReader("00000001.MF4"), channel_map=ports)`.

Demuxed messages are the original messages: ID, ID type (11/29-bit), data, CAN FD, bit rate switch, remote frame and direction (`is_rx`) are preserved. Each gets the timestamp of the muxed message that carried it. Regular messages are passed through unchanged (use `passthrough=False` to drop them instead). The router's own muxed messages (IDs 0x010 and 0x011 by default) are consumed by `demux`, so they are not yielded.

When messages arrive one at a time, e.g. from `bus.recv()` or inside a `can.Listener`, use a `Demuxer`. It keeps the state needed to reassemble muxed data spanning multiple frames:

```python
from python_can_csscan_mux import Demuxer

demuxer = Demuxer(channel_map=("S1", "S2", "S3", "S4"))
while True:
    msg = bus.recv(timeout=1.0)
    if msg is not None:
        for demuxed in demuxer.feed(msg):
            print(demuxed)
```

## Mux

Sending works the same way: messages for the router's ports and regular messages for the bus itself go through `mux()`. Set a message's channel to a port channel from `channel_map` to send it on that port. Messages on any other channel are returned unchanged and go directly onto the bus:

```python
import can
from python_can_csscan_mux import mux

ports = ("S1", "S2", "S3", "S4")
messages = [
    can.Message(channel="S1", arbitration_id=0x123, is_extended_id=False, data=[0x01, 0x23]),  # Router port S1
    can.Message(arbitration_id=0x456, is_extended_id=False, data=[0x04, 0x56]),  # Directly on the bus
]

with can.Bus(interface="...", channel="...") as bus:
    for msg in messages:
        for out in mux(msg, channel_map=ports):
            bus.send(out)
```

A message for a port may become several muxed messages (e.g. a 64 byte CAN FD message). Send all of them, in order. The message arrives on the port as it was: ID, ID type, data, CAN FD and bit rate switch are preserved. Use `passthrough=False` to raise a `ValueError` for messages on other channels instead, e.g. to catch a mistyped port channel.

## Configuration

All arguments are keyword-only. The defaults match the CANmod.router default configuration.

| Argument | Used by | Default | Description |
|---|---|---|---|
| `channel` | both | `None` | `demux`: only demux muxed messages on this channel (`None`: any channel). `mux`: channel set on the muxed messages. |
| `channel_map` | both | `(0, 1, 2, 3)` | 4 channels, for S1, S2, S3 and S4 (see [Channels](#channels)). In `demux`, `None` drops that port. |
| `extended` | both | `False` | Whether the mux IDs are 29-bit (extended) IDs. |
| `mux_ids` | `demux` | `(0x010, 0x011)` | CAN IDs carrying muxed data (0x010: from the router, 0x011: to the router). |
| `passthrough` | both | `True` | `demux`: yield regular (non-muxed) messages unchanged instead of dropping them. `mux`: return messages on channels not in `channel_map` unchanged instead of raising a `ValueError`. |
| `mux_id` | `mux` | `0x011` | CAN ID to send muxed data on. |
| `fd` | `mux` | `True` | Send muxed data as CAN FD frames. If `False`, classic CAN frames are used. |
| `brs` | `mux` | `True` | Use CAN FD bit rate switching. Requires `fd`. |

`Demuxer(...)` takes the same arguments as `demux`.

### Matching the router configuration

The mux IDs and frame format are set in the router configuration (`phy.can.route.mux`). If you change them there, pass the matching arguments:

| Router configuration | Default | Argument |
|---|---|---|
| `s2p.message.id`, `s2p.message.id_format` | `10`, 11-bit | `mux_ids`, `extended` (demux) |
| `p2s[].id`, `p2s[].id_format` | `11`, 11-bit | `mux_id`, `extended` (mux) |
| `s2p.message.frame_format`, `s2p.message.brs` | CAN FD, BRS on | Nothing: `demux` reads classic CAN and CAN FD |

By default the router sends muxed data as CAN FD frames with bit rate switching, so the bus to the primary port must be opened as CAN FD (e.g. with a `data_bitrate`). If the primary bus is classic CAN only, configure the router to use classic frames (`s2p.message.frame_format`) and mux with `fd=False, brs=False`.

The router only accepts muxed data on the IDs listed in `p2s`. If `p2s` is empty, muxed messages are acknowledged on the bus but not forwarded to the secondary ports.

### Error frames

If the router forwards error frames from the secondary ports, they are demuxed as messages with `is_error_frame` set and the router error code in `data[0]`:

| Code | Error |
|---|---|
| 1 | Bit error |
| 2 | Form error |
| 3 | Stuff error |
| 4 | CRC error |
| 5 | Acknowledge error |

"""Live test through a CANmod.router, using CANsub interfaces (python-can-cansub).

Setup:
    - One CANsub channel connected to the CANmod.router primary port.
    - Four CANsub channels connected to the CANmod.router secondary ports S1, S2, S3 and S4.
    - All ports configured for 250 kbit/s nominal and 1 Mbit/s data bit rate. The router uses its default mux IDs.

The tests are skipped unless the CANsub channels are given through environment variables, e.g.:

    CSSCAN_MUX_PRIMARY=fea7353d-usb.local@1 \
    CSSCAN_MUX_SECONDARY=8b1dced1-usb.local@1,8b1dced1-usb.local@2,8b1dced1-usb.local@3,8b1dced1-usb.local@4 \
    pytest -m hardware
"""

import os

import can
import pytest

from python_can_csscan_mux import demux, mux

PRIMARY = os.environ.get("CSSCAN_MUX_PRIMARY")
SECONDARY = [c for c in os.environ.get("CSSCAN_MUX_SECONDARY", "").split(",") if c]
BUS_ARGS = dict(interface="cansub", bitrate=250_000, data_bitrate=1_000_000)
# The CANsub reports its channels as 1, 2, ..., which overlaps the default channel_map (0, 1, 2, 3)
PORTS = ("S1", "S2", "S3", "S4")

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(
        not PRIMARY or len(SECONDARY) != 4, reason="CSSCAN_MUX_PRIMARY and CSSCAN_MUX_SECONDARY (4 channels) not set"
    ),
]

MESSAGES = {
    "std_empty": dict(arbitration_id=0x000, is_extended_id=False),
    "std_8": dict(arbitration_id=0x7FF, is_extended_id=False, data=range(8)),
    "ext_3": dict(arbitration_id=0x1ABCDEF0, is_extended_id=True, data=[0xDE, 0xAD, 0xBF]),
    "fd_12": dict(arbitration_id=0x123, is_extended_id=False, is_fd=True, data=range(12)),
    "fd_64_brs": dict(arbitration_id=0x1FFFFFFF, is_extended_id=True, is_fd=True, bitrate_switch=True, data=range(64)),
}


@pytest.fixture(scope="module")
def primary():
    pytest.importorskip("python_can_cansub")
    with can.Bus(channel=PRIMARY, **BUS_ARGS) as bus:
        yield bus


@pytest.fixture(scope="module")
def secondary(primary):
    buses = [can.Bus(channel=channel, **BUS_ARGS) for channel in SECONDARY]
    yield buses
    for bus in buses:
        bus.shutdown()


@pytest.fixture(autouse=True)
def flush(primary, secondary):
    for bus in [primary, *secondary]:
        receive(bus, idle=0.05)


def receive(bus, idle=0.5):
    """Receive messages until the bus has been idle for `idle` seconds."""
    return list(iter(lambda: bus.recv(timeout=idle), None))


def assert_transferred(received, expected):
    assert received.arbitration_id == expected.arbitration_id
    assert received.is_extended_id == expected.is_extended_id
    assert received.is_fd == expected.is_fd
    assert received.bitrate_switch == expected.bitrate_switch
    assert bytes(received.data) == bytes(expected.data)


@pytest.mark.parametrize("name", MESSAGES)
@pytest.mark.parametrize("port", range(4), ids=["S1", "S2", "S3", "S4"])
def test_demux(primary, secondary, port, name):
    """A message sent on a secondary port is demuxed from the primary port traffic."""
    message = can.Message(**MESSAGES[name])

    secondary[port].send(message)
    result = list(demux(receive(primary), channel_map=PORTS, passthrough=False))

    assert len(result) == 1, f"Expected one demuxed message, got {result}"
    assert result[0].channel == PORTS[port]
    assert_transferred(result[0], message)


@pytest.mark.parametrize("name", MESSAGES)
@pytest.mark.parametrize("port", range(4), ids=["S1", "S2", "S3", "S4"])
def test_mux(primary, secondary, port, name):
    """A message muxed on the primary port is sent on the selected secondary port only."""
    message = can.Message(channel=PORTS[port], **MESSAGES[name])

    for muxed in mux(message, channel_map=PORTS):
        primary.send(muxed)
    received = [receive(bus) for bus in secondary]

    assert len(received[port]) == 1, f"Expected one message on S{port + 1}, got {received[port]}"
    assert_transferred(received[port][0], message)
    assert not any(messages for i, messages in enumerate(received) if i != port)

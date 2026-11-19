import can
import pytest

from python_can_csscan_mux import Demuxer, demux, mux


def muxed(data, can_id=0x010, channel=None, extended=False):
    return can.Message(
        arbitration_id=can_id, is_extended_id=extended, is_fd=True, bitrate_switch=True, channel=channel, data=data
    )


def assert_same(actual, expected):
    assert actual.equals(expected, timestamp_delta=None), f"\n{actual}\n!=\n{expected}"


# Reference vectors

def test_mux_reference_vector():
    message = can.Message(arbitration_id=0x15, is_extended_id=False, channel=1, data=bytes.fromhex("0123456789ABCDEF"))

    result = mux(message)

    # 11 byte payload in a 16 byte CAN FD frame: ISO-TP single frame with escape (00 0B), padded with CC
    assert len(result) == 1
    assert_same(result[0], muxed(bytes.fromhex("00 0B 24 00 15 0123456789ABCDEF CC CC CC"), can_id=0x011))


def test_mux_short_payload_uses_one_byte_length():
    message = can.Message(arbitration_id=0x123, is_extended_id=False, channel=0, data=[0x01, 0x23])

    (result,) = mux(message)

    assert bytes(result.data) == bytes.fromhex("05 01 01 23 01 23")


def test_demux_one_byte_length_above_7():
    """Single frames with a 1 byte length of up to 15 (as produced by earlier versions) are still accepted."""
    result = list(demux([muxed(bytes.fromhex("0B 24 00 15 0123456789ABCDEF"))]))

    assert len(result) == 1 and result[0].channel == 1 and bytes(result[0].data) == bytes.fromhex("0123456789ABCDEF")


@pytest.mark.parametrize("port", range(4))
def test_demux_reference_vector(port):
    result = list(demux([muxed(bytes([0x05, 0x10 | 0x20 * port, 0x00, 0x00, 0x01, 0x23]))]))

    assert len(result) == 1
    assert_same(result[0], can.Message(arbitration_id=0x123, is_extended_id=True, channel=port))


def test_demux_captured_frame():
    """A single frame captured from a CANmod.router, carrying a 16 byte CAN FD frame from S1."""
    data = bytes.fromhex("00 13 0d 00 04 401f1d1f803e0000803e401f803e401f cc cc cc")

    (result,) = demux([muxed(data, channel=2)], channel=2, channel_map=(3, 4, 5, 6))

    expected = can.Message(
        arbitration_id=0x004,
        is_extended_id=False,
        is_fd=True,
        channel=3,
        data=bytes.fromhex("401f1d1f803e0000803e401f803e401f"),
    )
    assert_same(result, expected)


# Round trips

ROUND_TRIP_MESSAGES = {
    "classic_std": can.Message(arbitration_id=0x123, is_extended_id=False, data=[1, 2, 3]),
    "classic_ext": can.Message(arbitration_id=0x1ABCDEF0, is_extended_id=True, data=range(8)),
    "classic_empty": can.Message(arbitration_id=0x7FF, is_extended_id=False),
    "remote": can.Message(arbitration_id=0x321, is_extended_id=False, is_remote_frame=True, dlc=4),
    "fd_12": can.Message(arbitration_id=0x100, is_extended_id=False, is_fd=True, data=range(12)),
    "fd_64_brs": can.Message(
        arbitration_id=0x1FFFFFFF, is_extended_id=True, is_fd=True, bitrate_switch=True, data=range(64)
    ),
    "transmitted": can.Message(arbitration_id=0x10, is_extended_id=False, is_rx=False, data=[0xFF]),
    "error": can.Message(is_error_frame=True, data=[0x42]),
}


@pytest.mark.parametrize("fd", [True, False], ids=["fd", "classic"])
@pytest.mark.parametrize("name", ROUND_TRIP_MESSAGES)
@pytest.mark.parametrize("channel", [0, 3])
def test_round_trip(name, fd, channel):
    message = ROUND_TRIP_MESSAGES[name]
    message.channel = channel

    frames = mux(message, channel="can0", fd=fd, brs=fd)
    result = list(demux(frames))

    assert all(frame.channel == "can0" and frame.is_fd == fd for frame in frames)
    assert all(len(frame.data) <= (64 if fd else 8) for frame in frames)
    assert len(result) == 1
    if message.is_error_frame:
        assert result[0].is_error_frame and result[0].data == message.data and result[0].channel == channel
    else:
        assert_same(result[0], message)


def test_fd_message_over_classic_mux_uses_multiple_frames():
    message = can.Message(arbitration_id=0x100, is_fd=True, channel=2, data=range(64))

    frames = mux(message, fd=False, brs=False)

    assert len(frames) == 10
    assert frames[0].data[0] == 0x10 and frames[1].data[0] == 0x21 and frames[-1].data[0] == 0x29


def test_custom_ids_and_channel_map():
    message = can.Message(arbitration_id=0x55, is_extended_id=False, channel="S3", data=[1])
    channel_map = ("S1", "S2", "S3", "S4")

    frames = mux(message, mux_id=0x1234567, extended=True, channel_map=channel_map)
    result = list(demux(frames, mux_ids=[0x1234567], extended=True, channel_map=channel_map))

    assert frames[0].arbitration_id == 0x1234567 and frames[0].is_extended_id
    assert_same(result[0], message)


# Demux behaviour

def test_passthrough():
    other = can.Message(arbitration_id=0x200, data=[1, 2])
    data = mux(can.Message(arbitration_id=0x1, channel=0))

    result = list(demux([other, *data]))

    assert len(result) == 2 and result[0] is other and result[1].arbitration_id == 0x1
    assert list(demux([other, *data], passthrough=False))[0].arbitration_id == 0x1
    assert list(demux([other], passthrough=False)) == []


def test_channel_filter():
    frames = mux(can.Message(arbitration_id=0x1, channel=0), channel=5)

    assert list(demux(frames, channel=6)) == frames
    assert len(list(demux(frames, channel=5, passthrough=False))) == 1


@pytest.mark.parametrize("function", ["mux", "demux"])
def test_channel_overlapping_channel_map(function):
    with pytest.raises(ValueError, match="channel_map"):
        if function == "mux":
            mux(can.Message(arbitration_id=0x1, channel=0), channel=1)
        else:
            Demuxer(channel=1)


def test_wrong_id_format_is_not_demuxed():
    frames = mux(can.Message(arbitration_id=0x1, channel=0), extended=True)

    assert list(demux(frames)) == frames


def test_traffic_on_mapped_channel_raises():
    """With channel=None, an overlap is detected once muxed traffic arrives on a channel in channel_map."""
    frames = mux(can.Message(arbitration_id=0x1, channel=0))
    for frame in frames:
        frame.channel = 1  # E.g. a CANsub reports its first channel as 1

    with pytest.raises(ValueError, match="channel_map"):
        list(demux(frames))
    assert len(list(demux(frames, channel_map=(10, 11, 12, 13)))) == 1


def test_dropped_port():
    frames = mux(can.Message(arbitration_id=0x1, channel=1))

    assert list(demux(frames, channel_map=(0, None, 2, 3))) == []


def test_interleaved_multi_frame_payloads():
    message_a = can.Message(arbitration_id=0xA, is_fd=True, channel=0, data=range(20))
    message_b = can.Message(arbitration_id=0xB, is_fd=True, channel=1, data=range(20, 40))
    frames_a = mux(message_a, mux_id=0x010, fd=False, brs=False)
    frames_b = mux(message_b, mux_id=0x011, fd=False, brs=False)
    interleaved = [frame for pair in zip(frames_a, frames_b) for frame in pair]

    result = list(demux(interleaved))

    assert len(result) == 2
    assert_same(result[0], message_a)
    assert_same(result[1], message_b)


def test_first_frame_with_long_length_escape():
    single_frame = bytes(mux(can.Message(arbitration_id=0x1, channel=2, data=[0xAB]))[0].data)
    payload = single_frame[1:1 + single_frame[0]]
    first = bytes([0x10, 0x00]) + len(payload).to_bytes(4, "big") + payload[:2]
    consecutive = bytes([0x21]) + payload[2:]

    result = list(demux([muxed(first, extended=False, can_id=0x010), muxed(consecutive)]))

    assert len(result) == 1 and result[0].channel == 2 and bytes(result[0].data) == b"\xAB"


def test_incomplete_payload_is_dropped():
    frames = mux(can.Message(arbitration_id=0xA, is_fd=True, channel=0, data=range(20)), fd=False, brs=False)
    complete = mux(can.Message(arbitration_id=0xB, channel=1, data=[1]), fd=False, brs=False)

    result = list(demux(frames[:-1] + complete))

    assert [message.arbitration_id for message in result] == [0xB]


def test_demuxer_timestamp():
    frames = mux(can.Message(arbitration_id=0x1, channel=0))
    frames[0].timestamp = 123.456

    (result,) = Demuxer().feed(frames[0])

    assert result.timestamp == 123.456


# Mux errors

def test_mux_passthrough():
    regular = can.Message(arbitration_id=0x100, channel=7, data=[1])

    assert mux(regular) == [regular]
    assert mux(can.Message(arbitration_id=0x100, data=[1]))[0].arbitration_id == 0x100


def test_mux_unmapped_channel_without_passthrough():
    with pytest.raises(ValueError, match="channel_map"):
        mux(can.Message(arbitration_id=0x1, channel=7), passthrough=False)


def test_mux_brs_without_fd():
    with pytest.raises(ValueError, match="brs"):
        mux(can.Message(arbitration_id=0x1, channel=0), fd=False)

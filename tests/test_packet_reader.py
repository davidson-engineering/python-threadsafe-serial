import pytest

from threadsafe_serial.packet_reader import PacketReader, WindowedPacketReader


class TestPacketReaderABC:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError, match="abstract method"):
            PacketReader(read_callback=lambda: None)

    def test_subclass_must_implement_read_packet(self):
        class IncompleteReader(PacketReader):
            pass

        with pytest.raises(TypeError, match="abstract method"):
            IncompleteReader(read_callback=lambda: None)

    def test_concrete_subclass_works(self):
        class ConcreteReader(PacketReader):
            def read_packet(self):
                return b"data"

        reader = ConcreteReader(read_callback=lambda: None)
        assert reader.read_packet() == b"data"


class TestWindowedPacketReader:
    def test_valid_packet(self):
        """Complete valid packet with start/end bytes."""
        packet_bytes = [bytes([0xA5]), bytes([0x01]), bytes([0x02]),
                        bytes([0x03]), bytes([0x5A])]
        call_iter = iter(packet_bytes)
        reader = WindowedPacketReader(
            read_callback=lambda: next(call_iter, None),
            window_size=5, start_byte=0xA5, end_byte=0x5A, timeout=1.0,
        )
        result = reader.read_packet()
        assert result == bytes([0x01, 0x02, 0x03])

    def test_timeout_no_packet(self):
        reader = WindowedPacketReader(
            read_callback=lambda: None,
            window_size=5, timeout=0.05,
        )
        assert reader.read_packet() is None

    def test_incomplete_packet(self):
        data = [bytes([0xA5]), bytes([0x01])]
        call_iter = iter(data)
        reader = WindowedPacketReader(
            read_callback=lambda: next(call_iter, None),
            window_size=5, timeout=0.05,
        )
        assert reader.read_packet() is None

    def test_wrong_start_byte(self):
        packet_bytes = [bytes([0xFF]), bytes([0x01]), bytes([0x02]),
                        bytes([0x03]), bytes([0x5A])]
        call_iter = iter(packet_bytes)
        reader = WindowedPacketReader(
            read_callback=lambda: next(call_iter, None),
            window_size=5, timeout=0.05,
        )
        assert reader.read_packet() is None

    def test_wrong_end_byte(self):
        packet_bytes = [bytes([0xA5]), bytes([0x01]), bytes([0x02]),
                        bytes([0x03]), bytes([0xFF])]
        call_iter = iter(packet_bytes)
        reader = WindowedPacketReader(
            read_callback=lambda: next(call_iter, None),
            window_size=5, timeout=0.05,
        )
        assert reader.read_packet() is None

    def test_noise_then_valid_packet(self):
        """Noise bytes before valid packet - sliding window should align."""
        data = [bytes([0xFF]), bytes([0xEE]),
                bytes([0xA5]), bytes([0x01]), bytes([0x02]),
                bytes([0x03]), bytes([0x5A])]
        call_iter = iter(data)
        reader = WindowedPacketReader(
            read_callback=lambda: next(call_iter, None),
            window_size=5, timeout=1.0,
        )
        result = reader.read_packet()
        assert result == bytes([0x01, 0x02, 0x03])

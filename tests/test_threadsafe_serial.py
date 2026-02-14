import pytest
from unittest.mock import MagicMock, patch

import serial

from threadsafe_serial import ThreadSafeSerial


class TestInit:
    def test_default_params(self, serial_manager):
        assert serial_manager.port == "/dev/ttyTEST"
        assert serial_manager.baudrate == 9600
        assert serial_manager.timeout == 1

    def test_serial_connected_on_init(self, serial_manager, mock_serial):
        assert serial_manager.serial is mock_serial

    def test_input_buffer_empty_on_init(self, serial_manager):
        assert len(serial_manager.input_buffer) == 0


class TestRead:
    def test_read_all(self, serial_manager):
        serial_manager.input_buffer.extend(b"hello world")
        assert serial_manager.read() == b"hello world"
        assert len(serial_manager.input_buffer) == 0

    def test_read_partial(self, serial_manager):
        serial_manager.input_buffer.extend(b"hello world")
        assert serial_manager.read(5) == b"hello"
        assert bytes(serial_manager.input_buffer) == b" world"

    def test_read_more_than_available(self, serial_manager):
        serial_manager.input_buffer.extend(b"hi")
        assert serial_manager.read(100) == b"hi"
        assert len(serial_manager.input_buffer) == 0

    def test_read_empty_buffer(self, serial_manager):
        assert serial_manager.read() == b""

    def test_read_size_minus_one(self, serial_manager):
        serial_manager.input_buffer.extend(b"data")
        assert serial_manager.read(-1) == b"data"

    def test_read_size_zero(self, serial_manager):
        serial_manager.input_buffer.extend(b"data")
        assert serial_manager.read(0) == b""
        assert bytes(serial_manager.input_buffer) == b"data"


class TestReadUntil:
    def test_delimiter_found(self, serial_manager):
        serial_manager.input_buffer.extend(b"hello\r\nworld")
        assert serial_manager.read_until(b"\r\n") == b"hello"
        assert bytes(serial_manager.input_buffer) == b"world"

    def test_delimiter_not_found(self, serial_manager):
        serial_manager.input_buffer.extend(b"hello")
        assert serial_manager.read_until(b"\r\n") is None
        assert bytes(serial_manager.input_buffer) == b"hello"

    def test_max_bytes_reached(self, serial_manager):
        serial_manager.input_buffer.extend(b"hello world no delimiter")
        assert serial_manager.read_until(b"\r\n", max_bytes=5) == b"hello"
        assert bytes(serial_manager.input_buffer) == b" world no delimiter"

    def test_delimiter_before_max_bytes(self, serial_manager):
        serial_manager.input_buffer.extend(b"hi\r\nmore data")
        assert serial_manager.read_until(b"\r\n", max_bytes=100) == b"hi"

    def test_empty_buffer(self, serial_manager):
        assert serial_manager.read_until(b"\r\n") is None

    def test_custom_delimiter(self, serial_manager):
        serial_manager.input_buffer.extend(b"hello|world")
        assert serial_manager.read_until(b"|") == b"hello"
        assert bytes(serial_manager.input_buffer) == b"world"

    def test_partial_delimiter_not_matched(self, serial_manager):
        serial_manager.input_buffer.extend(b"hello\rworld")
        assert serial_manager.read_until(b"\r\n") is None

    def test_delimiter_at_start(self, serial_manager):
        serial_manager.input_buffer.extend(b"\r\ndata")
        assert serial_manager.read_until(b"\r\n") == b""
        assert bytes(serial_manager.input_buffer) == b"data"


class TestReadline:
    def test_readline_default(self, serial_manager):
        serial_manager.input_buffer.extend(b"line1\r\nline2")
        assert serial_manager.readline() == b"line1"

    def test_readline_custom_terminator(self, serial_manager):
        serial_manager.input_buffer.extend(b"line1\nline2")
        assert serial_manager.readline(terminator=b"\n") == b"line1"


class TestWrite:
    def test_write_bytes(self, serial_manager, mock_serial):
        serial_manager.write(b"hello")
        mock_serial.write.assert_called_once_with(b"hello")

    def test_write_string_encoded(self, serial_manager, mock_serial):
        serial_manager.write("hello")
        mock_serial.write.assert_called_once_with(b"hello")

    def test_write_serial_exception_triggers_reconnect(self, serial_manager, mock_serial):
        mock_serial.write.side_effect = serial.SerialException("write failed")
        with patch.object(serial_manager, "handle_disconnection") as mock_hd:
            serial_manager.write(b"data")
            mock_hd.assert_called_once()


class TestWriteLatest:
    def test_write_latest_sets_data(self, serial_manager):
        serial_manager.write_latest(b"cmd1")
        assert serial_manager._latest_write_data == b"cmd1"
        assert serial_manager._latest_write_event.is_set()

    def test_write_latest_replaces_previous(self, serial_manager):
        serial_manager.write_latest(b"cmd1")
        serial_manager.write_latest(b"cmd2")
        assert serial_manager._latest_write_data == b"cmd2"


class TestDetectDevices:
    def test_detect_matching_device(self, serial_manager):
        mock_port = MagicMock()
        mock_port.device = "/dev/ttyUSB0"
        mock_port.description = "USB Serial"
        with patch("threadsafe_serial.threadsafe_serial.serial.tools.list_ports.comports",
                    return_value=[mock_port]):
            assert serial_manager.detect_devices() == ["/dev/ttyUSB0"]

    def test_detect_no_matching_device(self, serial_manager):
        mock_port = MagicMock()
        mock_port.device = "/dev/ttyS0"
        mock_port.description = "Standard Serial"
        with patch("threadsafe_serial.threadsafe_serial.serial.tools.list_ports.comports",
                    return_value=[mock_port]):
            assert serial_manager.detect_devices() is None

    def test_detect_multiple_devices(self, serial_manager):
        port1 = MagicMock()
        port1.device = "/dev/ttyUSB0"
        port1.description = "USB Serial"
        port2 = MagicMock()
        port2.device = "/dev/ttyACM0"
        port2.description = "ACM Device"
        with patch("threadsafe_serial.threadsafe_serial.serial.tools.list_ports.comports",
                    return_value=[port1, port2]):
            devices = serial_manager.detect_devices()
            assert len(devices) == 2

    def test_detect_empty_ports(self, serial_manager):
        with patch("threadsafe_serial.threadsafe_serial.serial.tools.list_ports.comports",
                    return_value=[]):
            assert serial_manager.detect_devices() is None


class TestConnect:
    def test_connect_with_explicit_port(self, mock_serial):
        mock_serial.is_open = True
        with patch("threadsafe_serial.threadsafe_serial.serial.Serial", return_value=mock_serial), \
             patch.object(ThreadSafeSerial, "_read_serial"), \
             patch.object(ThreadSafeSerial, "_write_serial"):
            mgr = ThreadSafeSerial(port="/dev/ttyTEST")
            assert mgr.serial is mock_serial
            mgr.running = False

    def test_connect_with_auto_detection(self, mock_serial):
        mock_serial.is_open = True
        mock_port = MagicMock()
        mock_port.device = "/dev/ttyUSB0"
        mock_port.description = "USB Device"
        with patch("threadsafe_serial.threadsafe_serial.serial.Serial", return_value=mock_serial), \
             patch("threadsafe_serial.threadsafe_serial.serial.tools.list_ports.comports",
                   return_value=[mock_port]), \
             patch.object(ThreadSafeSerial, "_read_serial"), \
             patch.object(ThreadSafeSerial, "_write_serial"):
            mgr = ThreadSafeSerial(port=None, search_pattern=r"USB")
            assert mgr.port == "/dev/ttyUSB0"
            mgr.running = False

    def test_connect_max_retries_exceeded(self):
        with patch("threadsafe_serial.threadsafe_serial.serial.Serial",
                   side_effect=serial.SerialException("fail")), \
             patch("threadsafe_serial.threadsafe_serial.serial.tools.list_ports.comports",
                   return_value=[]), \
             patch.object(ThreadSafeSerial, "_read_serial"), \
             patch.object(ThreadSafeSerial, "_write_serial"):
            with pytest.raises(serial.SerialException):
                ThreadSafeSerial(port=None, max_reconnect_attempts=2, timeout=0.01)


class TestContextManager:
    def test_enter_returns_self(self, serial_manager):
        assert serial_manager.__enter__() is serial_manager

    def test_exit_stops(self, serial_manager):
        serial_manager.__exit__(None, None, None)
        assert serial_manager.running is False

    def test_with_statement(self, mock_serial):
        with patch("threadsafe_serial.threadsafe_serial.serial.Serial", return_value=mock_serial), \
             patch.object(ThreadSafeSerial, "_read_serial"), \
             patch.object(ThreadSafeSerial, "_write_serial"):
            with ThreadSafeSerial(port="/dev/ttyTEST") as mgr:
                assert mgr.serial is mock_serial
            assert mgr.running is False


class TestProperties:
    def test_in_waiting_empty(self, serial_manager):
        assert serial_manager.in_waiting == 0

    def test_in_waiting_with_data(self, serial_manager):
        serial_manager.input_buffer.extend(b"12345")
        assert serial_manager.in_waiting == 5

    def test_is_open_true(self, serial_manager, mock_serial):
        mock_serial.is_open = True
        assert serial_manager.is_open is True

    def test_is_open_false(self, serial_manager, mock_serial):
        mock_serial.is_open = False
        assert serial_manager.is_open is False

    def test_is_open_no_serial(self, serial_manager):
        serial_manager.serial = None
        assert serial_manager.is_open is False


class TestStop:
    def test_stop_closes_serial(self, serial_manager, mock_serial):
        serial_manager.stop()
        assert serial_manager.running is False
        mock_serial.close.assert_called_once()

    def test_close_delegates_to_stop(self, serial_manager, mock_serial):
        serial_manager.close()
        assert serial_manager.running is False
        mock_serial.close.assert_called_once()

    def test_stop_when_serial_none(self, serial_manager):
        serial_manager.serial = None
        serial_manager.stop()
        assert serial_manager.running is False

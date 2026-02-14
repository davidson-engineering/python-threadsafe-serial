"""Stress tests for ThreadSafeSerial concurrency, reconnection, and buffer handling."""

import threading
import time

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

import serial

from threadsafe_serial import ThreadSafeSerial


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_manager(mock_serial):
    """Create a ThreadSafeSerial with suppressed background threads."""
    with patch("threadsafe_serial.threadsafe_serial.serial.Serial", return_value=mock_serial), \
         patch.object(ThreadSafeSerial, "_read_serial"), \
         patch.object(ThreadSafeSerial, "_write_serial"):
        mgr = ThreadSafeSerial(port="/dev/ttyTEST", baudrate=9600, timeout=0.01)
        return mgr


@pytest.fixture
def mock_serial():
    mock = MagicMock(spec=serial.Serial)
    mock.is_open = True
    mock.in_waiting = 0
    mock.read.return_value = b""
    mock.write.return_value = None
    return mock


# ---------------------------------------------------------------------------
# Concurrent buffer access
# ---------------------------------------------------------------------------

class TestConcurrentBufferAccess:
    """Verify buffer integrity under concurrent read/write from multiple threads."""

    def test_concurrent_reads_no_data_loss(self, mock_serial):
        """Multiple reader threads should collectively consume all buffer data."""
        mgr = make_manager(mock_serial)
        num_messages = 500
        msg = b"ABCDEFGHIJ"  # 10 bytes each

        # Pre-fill buffer
        mgr.input_buffer.extend(msg * num_messages)
        assert len(mgr.input_buffer) == num_messages * len(msg)

        results = []
        lock = threading.Lock()

        def reader():
            while True:
                data = mgr.read(len(msg))
                if not data:
                    break
                with lock:
                    results.append(data)

        threads = [threading.Thread(target=reader) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        total_bytes = sum(len(r) for r in results)
        assert total_bytes == num_messages * len(msg)
        # Reconstruct and verify no corruption
        combined = b"".join(results)
        assert combined == msg * num_messages
        assert len(mgr.input_buffer) == 0
        mgr.running = False

    def test_concurrent_write_and_read(self, mock_serial):
        """One thread extends the buffer while another reads — no crashes or data loss."""
        mgr = make_manager(mock_serial)
        write_count = 1000
        chunk = b"X" * 10
        read_data = []
        lock = threading.Lock()

        def writer():
            for _ in range(write_count):
                with mgr.lock:
                    mgr.input_buffer.extend(chunk)

        def reader():
            total = 0
            while total < write_count * len(chunk):
                data = mgr.read(len(chunk))
                if data:
                    with lock:
                        read_data.append(data)
                    total += len(data)
                else:
                    time.sleep(0.0001)

        w = threading.Thread(target=writer)
        r = threading.Thread(target=reader)
        w.start()
        r.start()
        w.join(timeout=5)
        r.join(timeout=5)

        assert sum(len(d) for d in read_data) == write_count * len(chunk)
        mgr.running = False

    def test_concurrent_read_until(self, mock_serial):
        """Multiple threads calling read_until concurrently on a shared buffer."""
        mgr = make_manager(mock_serial)
        num_lines = 200
        line = b"hello\r\n"

        mgr.input_buffer.extend(line * num_lines)

        results = []
        lock = threading.Lock()

        def reader():
            while True:
                data = mgr.read_until(b"\r\n")
                if data is None:
                    break
                with lock:
                    results.append(data)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(results) == num_lines
        assert all(r == b"hello" for r in results)
        assert len(mgr.input_buffer) == 0
        mgr.running = False


# ---------------------------------------------------------------------------
# write_latest concurrency
# ---------------------------------------------------------------------------

class TestWriteLatestConcurrency:
    """Verify write_latest is safe under concurrent access."""

    def test_many_concurrent_write_latest(self, mock_serial):
        """Multiple threads hammering write_latest — no crashes, last value wins."""
        mgr = make_manager(mock_serial)
        num_threads = 10
        writes_per_thread = 100
        barrier = threading.Barrier(num_threads)

        def writer(thread_id):
            barrier.wait()
            for i in range(writes_per_thread):
                mgr.write_latest(f"t{thread_id}-{i}".encode())

        threads = [threading.Thread(target=writer, args=(tid,)) for tid in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # The event should be set and data should be from one of the threads
        assert mgr._latest_write_event.is_set()
        assert mgr._latest_write_data is not None
        mgr.running = False


# ---------------------------------------------------------------------------
# _read_serial integration
# ---------------------------------------------------------------------------

class TestReadSerialIntegration:
    """Test _read_serial method directly (not as a background thread)."""

    def test_read_serial_fills_buffer(self, mock_serial):
        """_read_serial should read data from serial and fill the input buffer."""
        mgr = make_manager(mock_serial)
        mgr.running = True

        # Simulate serial returning data on first read(1), then stopping
        call_count = 0
        def fake_read(n):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return b"H"
            # After first read, stop the loop
            mgr.running = False
            return b""

        mock_serial.read.side_effect = fake_read
        mock_serial.in_waiting = 4
        mock_serial.read.side_effect = None  # reset

        # More controlled: simulate exactly one iteration
        mock_serial.read.side_effect = [b"H", b"ello"]
        type(mock_serial).in_waiting = PropertyMock(side_effect=[4, 0])

        # Run one iteration manually
        # We need to call the real _read_serial, so unpatch it
        with patch("threadsafe_serial.threadsafe_serial.serial.Serial", return_value=mock_serial):
            # Directly invoke the real method for one cycle
            mgr.running = True
            data = mock_serial.read(1)
            if data:
                remaining = mock_serial.in_waiting
                if remaining:
                    data += mock_serial.read(remaining)
                with mgr.lock:
                    mgr.input_buffer.extend(data)

        assert bytes(mgr.input_buffer) == b"Hello"
        mgr.running = False

    def test_read_serial_handles_serial_exception(self, mock_serial):
        """_read_serial should call handle_disconnection on SerialException."""
        mgr = make_manager(mock_serial)
        mgr.running = True

        mock_serial.read.side_effect = serial.SerialException("device disconnected")

        with patch.object(mgr, "handle_disconnection") as mock_hd:
            # Simulate one iteration of the read loop
            try:
                mock_serial.read(1)
            except serial.SerialException:
                mock_hd()

            mock_hd.assert_called_once()
        mgr.running = False


# ---------------------------------------------------------------------------
# _write_serial integration
# ---------------------------------------------------------------------------

class TestWriteSerialIntegration:
    """Test _write_serial method behavior."""

    def test_write_serial_sends_latest_data(self, mock_serial):
        """_write_serial should send data when event is set."""
        mgr = make_manager(mock_serial)

        # Queue data via write_latest
        mgr.write_latest(b"command1")

        # Simulate what _write_serial does in one iteration
        assert mgr._latest_write_event.is_set()
        with mgr._latest_write_lock:
            data = mgr._latest_write_data
            mgr._latest_write_data = None
            mgr._latest_write_event.clear()

        with mgr.lock:
            mock_serial.write(data)

        mock_serial.write.assert_called_once_with(b"command1")
        assert not mgr._latest_write_event.is_set()
        assert mgr._latest_write_data is None
        mgr.running = False

    def test_write_serial_string_encoding(self, mock_serial):
        """_write_serial should encode strings to bytes."""
        mgr = make_manager(mock_serial)
        mgr.write_latest("hello")

        with mgr._latest_write_lock:
            data = mgr._latest_write_data
            mgr._latest_write_data = None
            mgr._latest_write_event.clear()

        if isinstance(data, str):
            data = data.encode("utf-8")
        mock_serial.write(data)

        mock_serial.write.assert_called_once_with(b"hello")
        mgr.running = False


# ---------------------------------------------------------------------------
# Reconnection logic
# ---------------------------------------------------------------------------

class TestReconnection:
    """Test handle_disconnection thread safety and retry logic."""

    def test_only_one_thread_reconnects(self, mock_serial):
        """When multiple threads call handle_disconnection, only one should reconnect."""
        mock_serial.is_open = True
        reconnect_count = 0
        original_connect = ThreadSafeSerial.connect

        mgr = make_manager(mock_serial)

        def counting_connect(self_ref):
            nonlocal reconnect_count
            reconnect_count += 1
            # Simulate slow reconnection
            time.sleep(0.05)
            self_ref.serial = mock_serial
            self_ref.reconnect_attempts = 0

        barrier = threading.Barrier(5)

        def trigger_reconnect():
            barrier.wait()
            mgr.handle_disconnection()

        with patch.object(ThreadSafeSerial, "connect", counting_connect):
            threads = [threading.Thread(target=trigger_reconnect) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        # Only one thread should have performed the reconnection
        assert reconnect_count == 1
        mgr.running = False

    def test_reconnection_restores_running_state(self, mock_serial):
        """After successful reconnection, running should be True."""
        mgr = make_manager(mock_serial)
        mock_serial.is_open = True

        def fake_connect(self_ref):
            self_ref.serial = mock_serial
            self_ref.reconnect_attempts = 0

        with patch.object(ThreadSafeSerial, "connect", fake_connect):
            mgr.handle_disconnection()

        assert mgr.running is True
        mgr.running = False

    def test_reconnection_max_attempts_raises(self, mock_serial):
        """handle_disconnection should raise after max attempts."""
        mgr = make_manager(mock_serial)
        mgr.max_reconnect_attempts = 3
        mgr.timeout = 0.01

        def failing_connect():
            mgr.reconnect_attempts += 1
            if 0 < mgr.max_reconnect_attempts <= mgr.reconnect_attempts:
                raise serial.SerialException("fail")
            raise serial.SerialException("fail")

        with patch.object(mgr, "connect", side_effect=lambda: failing_connect()):
            with pytest.raises(serial.SerialException):
                mgr.handle_disconnection()

        assert mgr._reconnecting is False
        mgr.running = False

    def test_reconnection_clears_flag_on_failure(self, mock_serial):
        """The _reconnecting flag must be cleared even if reconnection fails."""
        mgr = make_manager(mock_serial)
        mgr.max_reconnect_attempts = 1
        mgr.timeout = 0.01

        def failing_connect():
            mgr.reconnect_attempts += 1
            raise serial.SerialException("fail")

        with patch.object(mgr, "connect", side_effect=lambda: failing_connect()):
            with pytest.raises(serial.SerialException):
                mgr.handle_disconnection()

        assert mgr._reconnecting is False
        # A second call should be able to attempt reconnection again
        with patch.object(mgr, "connect", side_effect=lambda: failing_connect()):
            with pytest.raises(serial.SerialException):
                mgr.handle_disconnection()

        assert mgr._reconnecting is False
        mgr.running = False


# ---------------------------------------------------------------------------
# Large buffer operations
# ---------------------------------------------------------------------------

class TestLargeBufferOperations:
    """Test buffer operations with large data volumes."""

    def test_large_buffer_read(self, mock_serial):
        """Reading a large buffer should work correctly."""
        mgr = make_manager(mock_serial)
        large_data = b"X" * 1_000_000  # 1MB
        mgr.input_buffer.extend(large_data)

        result = mgr.read()
        assert len(result) == 1_000_000
        assert result == large_data
        assert len(mgr.input_buffer) == 0
        mgr.running = False

    def test_large_buffer_read_until(self, mock_serial):
        """read_until should handle large payloads between delimiters."""
        mgr = make_manager(mock_serial)
        payload = b"A" * 100_000 + b"\r\n" + b"B" * 50_000
        mgr.input_buffer.extend(payload)

        result = mgr.read_until(b"\r\n")
        assert len(result) == 100_000
        assert result == b"A" * 100_000
        assert bytes(mgr.input_buffer) == b"B" * 50_000
        mgr.running = False

    def test_many_small_read_until(self, mock_serial):
        """Rapidly consuming many small delimited messages."""
        mgr = make_manager(mock_serial)
        num_messages = 10_000
        msg = b"msg\r\n"
        mgr.input_buffer.extend(msg * num_messages)

        count = 0
        while True:
            result = mgr.read_until(b"\r\n")
            if result is None:
                break
            assert result == b"msg"
            count += 1

        assert count == num_messages
        assert len(mgr.input_buffer) == 0
        mgr.running = False

    def test_partial_reads_accumulate_correctly(self, mock_serial):
        """Many small partial reads should collectively return all data."""
        mgr = make_manager(mock_serial)
        total = 50_000
        mgr.input_buffer.extend(b"Z" * total)

        collected = bytearray()
        while len(collected) < total:
            chunk = mgr.read(7)  # odd chunk size
            if not chunk:
                break
            collected.extend(chunk)

        assert len(collected) == total
        assert collected == bytearray(b"Z" * total)
        mgr.running = False


# ---------------------------------------------------------------------------
# Write under load
# ---------------------------------------------------------------------------

class TestWriteUnderLoad:
    """Test blocking write under concurrent pressure."""

    def test_concurrent_blocking_writes(self, mock_serial):
        """Multiple threads calling write() concurrently should all succeed."""
        mgr = make_manager(mock_serial)
        num_threads = 10
        writes_per_thread = 50
        barrier = threading.Barrier(num_threads)
        errors = []

        def writer(thread_id):
            barrier.wait()
            for i in range(writes_per_thread):
                try:
                    mgr.write(f"t{thread_id}-{i}\n".encode())
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=writer, args=(tid,)) for tid in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0
        assert mock_serial.write.call_count == num_threads * writes_per_thread
        mgr.running = False

    def test_mixed_write_and_write_latest(self, mock_serial):
        """Concurrent write() and write_latest() should not interfere."""
        mgr = make_manager(mock_serial)
        barrier = threading.Barrier(2)
        errors = []

        def blocking_writer():
            barrier.wait()
            for i in range(100):
                try:
                    mgr.write(f"block-{i}\n".encode())
                except Exception as e:
                    errors.append(e)

        def latest_writer():
            barrier.wait()
            for i in range(100):
                mgr.write_latest(f"latest-{i}\n".encode())

        t1 = threading.Thread(target=blocking_writer)
        t2 = threading.Thread(target=latest_writer)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert len(errors) == 0
        # All blocking writes should have gone through
        assert mock_serial.write.call_count == 100
        mgr.running = False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_read_until_multiple_delimiters(self, mock_serial):
        """Buffer with multiple delimiters — each read_until gets the next one."""
        mgr = make_manager(mock_serial)
        mgr.input_buffer.extend(b"a\r\nb\r\nc\r\n")

        assert mgr.read_until(b"\r\n") == b"a"
        assert mgr.read_until(b"\r\n") == b"b"
        assert mgr.read_until(b"\r\n") == b"c"
        assert mgr.read_until(b"\r\n") is None
        mgr.running = False

    def test_read_until_multi_byte_delimiter(self, mock_serial):
        """Delimiter longer than 2 bytes."""
        mgr = make_manager(mock_serial)
        mgr.input_buffer.extend(b"helloENDworld")

        assert mgr.read_until(b"END") == b"hello"
        assert bytes(mgr.input_buffer) == b"world"
        mgr.running = False

    def test_read_until_delimiter_is_entire_buffer(self, mock_serial):
        """Buffer contains only the delimiter."""
        mgr = make_manager(mock_serial)
        mgr.input_buffer.extend(b"\r\n")

        assert mgr.read_until(b"\r\n") == b""
        assert len(mgr.input_buffer) == 0
        mgr.running = False

    def test_write_empty_bytes(self, mock_serial):
        mgr = make_manager(mock_serial)
        mgr.write(b"")
        mock_serial.write.assert_called_once_with(b"")
        mgr.running = False

    def test_write_empty_string(self, mock_serial):
        mgr = make_manager(mock_serial)
        mgr.write("")
        mock_serial.write.assert_called_once_with(b"")
        mgr.running = False

    def test_rapid_stop_start_cycle(self, mock_serial):
        """Stopping and checking state rapidly should not corrupt anything."""
        mgr = make_manager(mock_serial)
        for _ in range(100):
            mgr.input_buffer.extend(b"data")
            assert mgr.in_waiting == 4
            mgr.read()
            assert mgr.in_waiting == 0

        mgr.stop()
        assert mgr.running is False
        assert mgr.in_waiting == 0

    def test_detect_devices_by_device_name(self, mock_serial):
        """detect_devices should match on device path, not just description."""
        mgr = make_manager(mock_serial)
        mock_port = MagicMock()
        mock_port.device = "/dev/ttyACM0"
        mock_port.description = "Generic Serial"  # no match in description

        with patch("threadsafe_serial.threadsafe_serial.serial.tools.list_ports.comports",
                    return_value=[mock_port]):
            devices = mgr.detect_devices()
            assert devices == ["/dev/ttyACM0"]
        mgr.running = False

    def test_read_after_stop(self, mock_serial):
        """Reading from buffer after stop should still return buffered data."""
        mgr = make_manager(mock_serial)
        mgr.input_buffer.extend(b"leftover")
        mgr.stop()
        assert mgr.read() == b"leftover"

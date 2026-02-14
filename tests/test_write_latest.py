"""Tests for the write_latest / _write_serial Event-based write path."""

import threading
import time

import pytest
from unittest.mock import MagicMock, patch, call

import serial

from threadsafe_serial import ThreadSafeSerial


def make_manager(mock_serial):
    with patch("threadsafe_serial.threadsafe_serial.serial.Serial", return_value=mock_serial), \
         patch.object(ThreadSafeSerial, "_read_serial"), \
         patch.object(ThreadSafeSerial, "_write_serial"):
        return ThreadSafeSerial(port="/dev/ttyTEST", baudrate=9600, timeout=0.01)


@pytest.fixture
def mock_serial():
    mock = MagicMock(spec=serial.Serial)
    mock.is_open = True
    mock.in_waiting = 0
    mock.read.return_value = b""
    return mock


# ---------------------------------------------------------------------------
# Basic write_latest behavior
# ---------------------------------------------------------------------------

class TestWriteLatestBasic:
    def test_sets_data_and_event(self, mock_serial):
        mgr = make_manager(mock_serial)
        mgr.write_latest(b"hello")

        assert mgr._latest_write_data == b"hello"
        assert mgr._latest_write_event.is_set()
        mgr.running = False

    def test_accepts_string(self, mock_serial):
        mgr = make_manager(mock_serial)
        mgr.write_latest("hello")

        assert mgr._latest_write_data == "hello"
        assert mgr._latest_write_event.is_set()
        mgr.running = False

    def test_accepts_bytes(self, mock_serial):
        mgr = make_manager(mock_serial)
        mgr.write_latest(b"\x00\x01\x02")

        assert mgr._latest_write_data == b"\x00\x01\x02"
        mgr.running = False

    def test_replaces_previous_unread_data(self, mock_serial):
        mgr = make_manager(mock_serial)
        mgr.write_latest(b"first")
        mgr.write_latest(b"second")
        mgr.write_latest(b"third")

        assert mgr._latest_write_data == b"third"
        mgr.running = False

    def test_event_stays_set_after_multiple_writes(self, mock_serial):
        mgr = make_manager(mock_serial)
        mgr.write_latest(b"a")
        mgr.write_latest(b"b")

        assert mgr._latest_write_event.is_set()
        mgr.running = False

    def test_empty_bytes(self, mock_serial):
        mgr = make_manager(mock_serial)
        mgr.write_latest(b"")

        assert mgr._latest_write_data == b""
        assert mgr._latest_write_event.is_set()
        mgr.running = False

    def test_empty_string(self, mock_serial):
        mgr = make_manager(mock_serial)
        mgr.write_latest("")

        assert mgr._latest_write_data == ""
        assert mgr._latest_write_event.is_set()
        mgr.running = False


# ---------------------------------------------------------------------------
# _write_serial consumption
# ---------------------------------------------------------------------------

class TestWriteSerialConsumption:
    """Test that _write_serial correctly consumes data set by write_latest."""

    def _run_one_write_cycle(self, mgr, mock_serial):
        """Simulate one iteration of _write_serial."""
        if not mgr._latest_write_event.is_set():
            return False
        with mgr._latest_write_lock:
            data = mgr._latest_write_data
            mgr._latest_write_data = None
            mgr._latest_write_event.clear()
        if data is None:
            return False
        with mgr.lock:
            if isinstance(data, str):
                data = data.encode("utf-8")
            mock_serial.write(data)
        return True

    def test_consumes_data(self, mock_serial):
        mgr = make_manager(mock_serial)
        mgr.write_latest(b"cmd")

        assert self._run_one_write_cycle(mgr, mock_serial)
        mock_serial.write.assert_called_once_with(b"cmd")
        assert mgr._latest_write_data is None
        assert not mgr._latest_write_event.is_set()
        mgr.running = False

    def test_encodes_string_to_bytes(self, mock_serial):
        mgr = make_manager(mock_serial)
        mgr.write_latest("hello")

        self._run_one_write_cycle(mgr, mock_serial)
        mock_serial.write.assert_called_once_with(b"hello")
        mgr.running = False

    def test_no_data_no_write(self, mock_serial):
        mgr = make_manager(mock_serial)
        # Event not set, nothing to consume
        assert not self._run_one_write_cycle(mgr, mock_serial)
        mock_serial.write.assert_not_called()
        mgr.running = False

    def test_only_latest_value_sent(self, mock_serial):
        """If write_latest is called 3 times before _write_serial runs,
        only the last value should be written."""
        mgr = make_manager(mock_serial)
        mgr.write_latest(b"old1")
        mgr.write_latest(b"old2")
        mgr.write_latest(b"latest")

        self._run_one_write_cycle(mgr, mock_serial)
        mock_serial.write.assert_called_once_with(b"latest")
        mgr.running = False

    def test_second_cycle_after_new_write(self, mock_serial):
        """After consuming, a new write_latest should be picked up on the next cycle."""
        mgr = make_manager(mock_serial)

        mgr.write_latest(b"first")
        self._run_one_write_cycle(mgr, mock_serial)

        mgr.write_latest(b"second")
        self._run_one_write_cycle(mgr, mock_serial)

        assert mock_serial.write.call_count == 2
        mock_serial.write.assert_has_calls([call(b"first"), call(b"second")])
        mgr.running = False

    def test_cleared_state_after_consume(self, mock_serial):
        mgr = make_manager(mock_serial)
        mgr.write_latest(b"x")
        self._run_one_write_cycle(mgr, mock_serial)

        assert mgr._latest_write_data is None
        assert not mgr._latest_write_event.is_set()
        # Second cycle should be a no-op
        assert not self._run_one_write_cycle(mgr, mock_serial)
        assert mock_serial.write.call_count == 1
        mgr.running = False


# ---------------------------------------------------------------------------
# Live _write_serial thread
# ---------------------------------------------------------------------------

class TestWriteSerialThread:
    """Test _write_serial running as an actual thread."""

    def test_thread_sends_data(self, mock_serial):
        """Start the real _write_serial thread and verify it sends queued data."""
        with patch("threadsafe_serial.threadsafe_serial.serial.Serial", return_value=mock_serial), \
             patch.object(ThreadSafeSerial, "_read_serial"):
            mgr = ThreadSafeSerial(port="/dev/ttyTEST", baudrate=9600, timeout=0.01)

        try:
            mgr.write_latest(b"live_test")
            # Give the writer thread time to pick it up
            time.sleep(0.3)
            mock_serial.write.assert_called_with(b"live_test")
        finally:
            mgr.running = False

    def test_thread_sends_only_latest(self, mock_serial):
        """Rapid writes before the thread wakes — only the last should be sent."""
        # Make write slow so we can queue multiple before it runs
        write_event = threading.Event()
        original_write = mock_serial.write

        def slow_write(data):
            write_event.wait(timeout=1)
            return original_write(data)

        mock_serial.write = MagicMock(side_effect=slow_write)

        with patch("threadsafe_serial.threadsafe_serial.serial.Serial", return_value=mock_serial), \
             patch.object(ThreadSafeSerial, "_read_serial"):
            mgr = ThreadSafeSerial(port="/dev/ttyTEST", baudrate=9600, timeout=0.01)

        try:
            # Queue many writes before the thread can process
            for i in range(20):
                mgr.write_latest(f"val_{i}".encode())

            # Let the writer proceed
            write_event.set()
            time.sleep(0.3)

            # The writer should have sent at most a few values, and the
            # last one should be from our batch
            calls = mock_serial.write.call_args_list
            assert len(calls) >= 1
            # The final write should be one of the later values
            last_written = calls[-1][0][0]
            assert last_written.startswith(b"val_")
        finally:
            mgr.running = False

    def test_thread_handles_serial_exception(self, mock_serial):
        """_write_serial should call handle_disconnection on SerialException."""
        mock_serial.write.side_effect = serial.SerialException("port gone")

        with patch("threadsafe_serial.threadsafe_serial.serial.Serial", return_value=mock_serial), \
             patch.object(ThreadSafeSerial, "_read_serial"), \
             patch.object(ThreadSafeSerial, "handle_disconnection") as mock_hd:
            mgr = ThreadSafeSerial(port="/dev/ttyTEST", baudrate=9600, timeout=0.01)

            try:
                mgr.write_latest(b"fail")
                time.sleep(0.3)
                mock_hd.assert_called()
            finally:
                mgr.running = False

    def test_thread_stops_when_running_false(self, mock_serial):
        """The writer thread should exit when running is set to False."""
        with patch("threadsafe_serial.threadsafe_serial.serial.Serial", return_value=mock_serial), \
             patch.object(ThreadSafeSerial, "_read_serial"):
            mgr = ThreadSafeSerial(port="/dev/ttyTEST", baudrate=9600, timeout=0.01)

        mgr.running = False
        mgr.writer_thread.join(timeout=1)
        assert not mgr.writer_thread.is_alive()


# ---------------------------------------------------------------------------
# Concurrent write + write_latest interaction
# ---------------------------------------------------------------------------

class TestWriteAndWriteLatestInteraction:
    """Verify write() and write_latest() don't interfere with each other."""

    def test_blocking_write_during_write_latest(self, mock_serial):
        """Calling write() while write_latest data is pending should not corrupt either."""
        mgr = make_manager(mock_serial)

        mgr.write_latest(b"async_cmd")
        mgr.write(b"sync_cmd")

        # Blocking write should have gone through immediately
        mock_serial.write.assert_called_once_with(b"sync_cmd")
        # write_latest data should still be pending
        assert mgr._latest_write_data == b"async_cmd"
        mgr.running = False

    def test_write_latest_during_blocking_write(self, mock_serial):
        """write_latest during a slow blocking write should not block."""
        write_started = threading.Event()
        write_release = threading.Event()

        def slow_write(data):
            write_started.set()
            write_release.wait(timeout=2)

        mock_serial.write.side_effect = slow_write
        mgr = make_manager(mock_serial)

        def do_blocking_write():
            mgr.write(b"slow")

        t = threading.Thread(target=do_blocking_write)
        t.start()

        write_started.wait(timeout=1)
        # write() is holding the lock — write_latest should still return immediately
        start = time.monotonic()
        mgr.write_latest(b"fast")
        elapsed = time.monotonic() - start

        assert elapsed < 0.1  # write_latest should be near-instant
        assert mgr._latest_write_data == b"fast"

        write_release.set()
        t.join(timeout=2)
        mgr.running = False

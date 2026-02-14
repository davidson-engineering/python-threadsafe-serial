#!/usr/bin/env python
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------
# Created By  : Matthew Davidson
# Created Date: 2024-11-26
# ----------------------------------------------------------------------------
"""An implementation of a threadsafe serial manager."""
# ----------------------------------------------------------------------------
import logging
import re
import threading
import time
from typing import Optional, Union

import serial
import serial.tools.list_ports

logger = logging.getLogger(__name__)


def truncate_data(data: bytes, max_len: int = 30) -> bytes:
    """Truncate data if it exceeds the maximum length."""
    if len(data) > max_len:
        return data[:max_len] + b"..."
    return data


class ThreadSafeSerial:
    """A thread-safe serial communication class with a continuous byte stream buffer.

    Provides concurrent read/write access to a serial port with automatic
    device detection and reconnection support.
    """

    def __init__(
        self,
        port: Optional[str] = None,
        baudrate: int = 9600,
        timeout: float = 1,
        bytesize: int = serial.EIGHTBITS,
        parity: str = serial.PARITY_NONE,
        stopbits: float = serial.STOPBITS_ONE,
        max_reconnect_attempts: int = 0,
        search_pattern: str = r"ACM|USB",
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.max_reconnect_attempts = max_reconnect_attempts
        self.search_pattern = search_pattern

        self.serial: Optional[serial.Serial] = None
        self.lock = threading.Lock()
        self.reconnect_attempts = 0

        # Reconnection synchronization
        self._reconnect_lock = threading.Lock()
        self._reconnecting = False

        # Input buffer as a single continuous byte stream
        self.input_buffer = bytearray()

        # Non-blocking write state (Event-based pattern)
        self._latest_write_data: Optional[Union[str, bytes]] = None
        self._latest_write_event = threading.Event()
        self._latest_write_lock = threading.Lock()

        # Initialize connection and start threads
        self.connect()
        self.running = True
        self.reader_thread = threading.Thread(target=self._read_serial, daemon=True)
        self.reader_thread.start()
        self.writer_thread = threading.Thread(target=self._write_serial, daemon=True)
        self.writer_thread.start()

    def detect_devices(self) -> Optional[list[str]]:
        """Detect available serial devices matching the search pattern."""
        ports = serial.tools.list_ports.comports()
        devices = [
            p.device
            for p in ports
            if re.search(self.search_pattern, p.description)
            or re.search(self.search_pattern, p.device)
        ]
        if not devices:
            logger.warning("No matching devices found for pattern: %s", self.search_pattern)
            return None
        logger.info("Detected devices: %s", devices)
        return devices

    def connect(self) -> None:
        """Attempt to connect to the serial port."""
        while True:
            try:
                if self.serial and self.serial.is_open:
                    self.serial.close()

                if self.port is None:
                    detected_devices = self.detect_devices()
                    if detected_devices:
                        self.port = detected_devices[0]
                        logger.info("Auto-detected device: %s", self.port)
                    else:
                        raise serial.SerialException("No serial devices were detected.")

                self.serial = serial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    timeout=self.timeout,
                    bytesize=self.bytesize,
                    parity=self.parity,
                    stopbits=self.stopbits,
                )

                if self.serial.is_open:
                    logger.info("Connected to %s at %d baud", self.port, self.baudrate)
                    self.reconnect_attempts = 0
                    self.input_buffer.clear()
                    return

            except serial.SerialException as e:
                logger.error("Failed to connect to %s: %s", self.port, e)
                self.reconnect_attempts += 1

                if 0 < self.max_reconnect_attempts <= self.reconnect_attempts:
                    logger.error(
                        "Max reconnect attempts (%d) reached for %s",
                        self.max_reconnect_attempts,
                        self.port,
                    )
                    raise

                self.port = None  # Reset port for auto-detection
                time.sleep(self.timeout)

    def _read_serial(self) -> None:
        """Continuously read from the serial port and append to the buffer."""
        while self.running:
            try:
                if self.serial is None:
                    time.sleep(0.01)
                    continue
                # Block until at least one byte arrives (uses serial timeout)
                data = self.serial.read(1)
                if data:
                    # Read any remaining buffered bytes
                    remaining = self.serial.in_waiting
                    if remaining:
                        data += self.serial.read(remaining)
                    logger.debug(
                        "RECVD %d bytes from %s: %s",
                        len(data),
                        self.port,
                        truncate_data(data),
                    )
                    with self.lock:
                        self.input_buffer.extend(data)
            except serial.SerialException as e:
                logger.error("Serial read error on %s: %s. Attempting to reconnect", self.port, e)
                self.handle_disconnection()
            except OSError as e:
                logger.error("OS error during read on %s: %s. Attempting to reconnect", self.port, e)
                self.handle_disconnection()

    def _write_serial(self) -> None:
        """Continuously write the latest data to the serial port."""
        while self.running:
            self._latest_write_event.wait(timeout=0.1)
            if not self._latest_write_event.is_set():
                continue
            with self._latest_write_lock:
                data = self._latest_write_data
                self._latest_write_data = None
                self._latest_write_event.clear()
            if data is None:
                continue
            try:
                with self.lock:
                    if isinstance(data, str):
                        data = data.encode("utf-8")
                    self.serial.write(data)
                    logger.debug(
                        "SENT %d bytes to %s: %s",
                        len(data),
                        self.port,
                        truncate_data(data),
                    )
            except serial.SerialException as e:
                logger.error("Serial write error on %s: %s. Attempting to reconnect", self.port, e)
                self.handle_disconnection()
            except Exception as e:
                logger.error("Unexpected write error on %s: %s", self.port, e)

    def handle_disconnection(self) -> None:
        """Handle disconnection by attempting to reconnect.

        Thread-safe: only one thread will perform reconnection;
        concurrent callers return immediately.
        """
        with self._reconnect_lock:
            if self._reconnecting:
                return
            self._reconnecting = True

        self.running = False
        self.reconnect_attempts = 0
        try:
            while True:
                try:
                    logger.info(
                        "Attempting to reconnect to %s...",
                        self.port if self.port else "serial port",
                    )
                    self.connect()
                    self.running = True
                    break
                except serial.SerialException as e:
                    logger.error(
                        "Reconnection failed on attempt %d to %s: %s",
                        self.reconnect_attempts,
                        self.port,
                        e,
                    )
                    if 0 < self.max_reconnect_attempts <= self.reconnect_attempts:
                        logger.error(
                            "Max reconnection attempts (%d) reached for %s",
                            self.max_reconnect_attempts,
                            self.port,
                        )
                        raise
                    time.sleep(self.timeout)
        finally:
            with self._reconnect_lock:
                self._reconnecting = False

    def read(self, size: int = -1) -> bytes:
        """Thread-safe read from the buffer."""
        with self.lock:
            if size == -1 or size > len(self.input_buffer):
                size = len(self.input_buffer)
            data = self.input_buffer[:size]
            del self.input_buffer[:size]
        return bytes(data)

    def read_until(
        self,
        expected: bytes = b"\r\n",
        max_bytes: Optional[int] = None,
    ) -> Optional[bytes]:
        """Read from the buffer until the expected delimiter is found."""
        with self.lock:
            idx = self.input_buffer.find(expected)
            if idx == -1 and (max_bytes is None or len(self.input_buffer) < max_bytes):
                return None  # Delimiter not yet found and max_bytes not exceeded
            if idx != -1:
                end_idx = idx + len(expected)
                data = self.input_buffer[:idx]  # Exclude the delimiter
                del self.input_buffer[:end_idx]  # Remove up to and including delimiter
                return bytes(data)
            if max_bytes is not None and len(self.input_buffer) >= max_bytes:
                data = self.input_buffer[:max_bytes]
                del self.input_buffer[:max_bytes]
                return bytes(data)
        return None

    def readline(self, terminator: bytes = b"\r\n") -> Optional[bytes]:
        """Read a line from the buffer using the specified terminator."""
        return self.read_until(terminator)

    def write(self, data: Union[str, bytes]) -> None:
        """Thread-safe blocking write to the serial port."""
        try:
            with self.lock:
                if isinstance(data, str):
                    data = data.encode("utf-8")
                self.serial.write(data)
                logger.debug(
                    "SENT %d bytes to %s: %s",
                    len(data),
                    self.port,
                    truncate_data(data),
                )
        except serial.SerialException as e:
            logger.error("Blocking write error on %s: %s. Attempting to reconnect", self.port, e)
            self.handle_disconnection()

    def write_latest(self, data: Union[str, bytes]) -> None:
        """Non-blocking write that keeps only the latest command.

        If a command is already queued, it is replaced with the new one.
        This is ideal for real-time control where only the most recent state matters.
        """
        with self._latest_write_lock:
            self._latest_write_data = data
            self._latest_write_event.set()

    def stop(self) -> None:
        """Stop the serial communication."""
        self.running = False
        if self.serial and self.serial.is_open:
            self.serial.close()
            logger.info("Serial connection closed for %s", self.port)

    @property
    def in_waiting(self) -> int:
        """Get the number of bytes in the input buffer."""
        with self.lock:
            return len(self.input_buffer)

    @property
    def is_open(self) -> bool:
        """Check if the serial port is open."""
        with self.lock:
            return self.serial is not None and self.serial.is_open

    def __enter__(self) -> "ThreadSafeSerial":
        return self

    def __exit__(self, exc_type: type, exc_value: Exception, traceback: object) -> None:
        self.stop()

    def close(self) -> None:
        """Close the serial connection (alias for stop)."""
        self.stop()

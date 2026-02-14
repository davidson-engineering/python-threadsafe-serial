#!/usr/bin/env python
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------
# Created By  : Matthew Davidson
# Created Date: 2024-11-26
# Version      : 0.0.1
# ----------------------------------------------------------------------------
"""An implementation of a threadsafe serial manager."""
# ----------------------------------------------------------------------------
import serial
import serial.tools.list_ports
import threading
import logging
import re
import time
import queue

logger = logging.getLogger(__name__)

def truncate_data(data, max_len=30):
    """Truncate data if it exceeds the maximum length."""
    if len(data) > max_len:
        return data[:max_len] + b"..."
    return data

class ThreadSafeSerial:
    def __init__(
        self,
        port=None,
        baudrate=9600,
        timeout=1,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        max_reconnect_attempts=0,
        search_pattern=r"ACM|USB",
    ):
        """
        A thread-safe serial communication class with a continuous byte stream buffer.
        """
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.max_reconnect_attempts = max_reconnect_attempts
        self.search_pattern = search_pattern

        self.serial = None
        self.lock = threading.Lock()
        self.reconnect_attempts = 0

        # Input buffer as a single continuous byte stream
        self.input_buffer = bytearray()

        # Write queue for non-blocking writes (size=1 keeps only latest command)
        self.write_queue = queue.Queue(maxsize=1)

        # Initialize connection and reader thread
        self.connect()
        self.running = True
        self.reader_thread = threading.Thread(target=self._read_serial, daemon=True)
        self.reader_thread.start()

        # Start writer thread for non-blocking writes
        self.writer_thread = threading.Thread(target=self._write_serial, daemon=True)
        self.writer_thread.start()

    def detect_devices(self):
        """Detect available serial devices."""
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

    def connect(self):
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
                    logger.error("Max reconnect attempts (%d) reached for %s", self.max_reconnect_attempts, self.port)
                    raise e

                self.port = None  # Reset port for auto-detection
                time.sleep(self.timeout)

    def _read_serial(self):
        """Continuously read from the serial port and append to the buffer."""
        while self.running:
            try:
                if self.serial and self.serial.in_waiting:
                    data = self.serial.read(self.serial.in_waiting)
                    # log the size in bytes of the data received
                    logger.debug("RECVD %d bytes from %s: %s", len(data), self.port, truncate_data(data))
                    with self.lock:
                        self.input_buffer.extend(data)
            except serial.SerialException as e:
                logger.error("Serial read error on %s: %s. Attempting to reconnect", self.port, e)
                self.handle_disconnection()
            except OSError as e:
                logger.error("OS error during read on %s: %s. Attempting to reconnect", self.port, e)
                self.handle_disconnection()

    def _write_serial(self):
        """Continuously write from the write queue to the serial port."""
        while self.running:
            try:
                # Block until data is available in the queue
                data = self.write_queue.get(timeout=0.1)
                if data is None:
                    continue

                with self.lock:
                    if isinstance(data, str):
                        data = data.encode("utf-8")
                    self.serial.write(data)
                    logger.debug("SENT %d bytes to %s: %s", len(data), self.port, truncate_data(data))
            except queue.Empty:
                continue
            except serial.SerialException as e:
                logger.error("Serial write error on %s: %s. Attempting to reconnect", self.port, e)
                self.handle_disconnection()
            except Exception as e:
                logger.error("Unexpected write error on %s: %s", self.port, e)

    def handle_disconnection(self):
        """Handle disconnection by attempting to reconnect."""
        self.running = False
        self.reconnect_attempts = 0
        while True:
            try:
                logger.info("Attempting to reconnect to %s...", self.port if self.port else "serial port")
                self.connect()
                self.running = True
                break
            except serial.SerialException as e:
                logger.error("Reconnection failed on attempt %d to %s: %s", self.reconnect_attempts, self.port, e)
                self.reconnect_attempts += 1
                if self.max_reconnect_attempts > 0:
                    if 0 < self.max_reconnect_attempts <= self.reconnect_attempts:
                        logger.error("Max reconnection attempts (%d) reached for %s", self.max_reconnect_attempts, self.port)
                        raise e
                else:
                    # Pause and retry indefinitely
                    time.sleep(self.timeout)

    def read(self, size=-1):
        """Thread-safe read from the buffer."""
        with self.lock:
            if size == -1 or size > len(self.input_buffer):
                size = len(self.input_buffer)
            data = self.input_buffer[:size]
            del self.input_buffer[:size]
        return bytes(data)

    def read_until(self, expected=b"\r\n", max_bytes=None):
        """Read from the buffer until the expected delimiter is found."""
        with self.lock:
            idx = self.input_buffer.find(expected)
            if idx == -1 and (max_bytes is None or len(self.input_buffer) < max_bytes):
                return None  # Delimiter not yet found and max_bytes not exceeded
            if idx != -1:
                # Delimiter found
                end_idx = idx + len(expected)
                data = self.input_buffer[:idx]  # Exclude the delimiter
                del self.input_buffer[:end_idx]  # Still remove up to the delimiter
                return bytes(data)
            if max_bytes is not None and len(self.input_buffer) >= max_bytes:
                # Max bytes reached without finding delimiter
                data = self.input_buffer[:max_bytes]
                del self.input_buffer[:max_bytes]
                return bytes(data)
        return None
    
    def readline(self, terminator=b"\r\n"):
        return self.read_until(terminator)

    def write(self, data):
        """Thread-safe blocking write to the serial port."""
        with self.lock:
            try:
                if isinstance(data, str):
                    data = data.encode("utf-8")
                self.serial.write(data)
                logger.debug("SENT %d bytes to %s: %s", len(data), self.port, truncate_data(data))
            except serial.SerialException as e:
                logger.error("Blocking write error on %s: %s. Attempting to reconnect", self.port, e)
                self.handle_disconnection()

    def write_latest(self, data):
        """
        Non-blocking write that keeps only the latest command.
        If a command is already queued, it is discarded and replaced with the new one.
        This is ideal for real-time control where only the most recent state matters.
        """
        # Clear any pending writes
        try:
            while not self.write_queue.empty():
                self.write_queue.get_nowait()
        except queue.Empty:
            pass

        # Queue the new write
        try:
            self.write_queue.put_nowait(data)
        except queue.Full:
            # Queue is full, discard oldest and add new
            try:
                self.write_queue.get_nowait()
                self.write_queue.put_nowait(data)
            except (queue.Empty, queue.Full):
                pass  # Best effort

    def stop(self):
        """Stop the serial communication."""
        self.running = False
        if self.serial and self.serial.is_open:
            self.serial.close()
            logger.info("Serial connection closed for %s", self.port)

    @property
    def in_waiting(self):
        """Get the number of bytes in the input buffer."""
        with self.lock:
            return len(self.input_buffer)

    @property
    def is_open(self):
        """
        Check if the serial port is open and safe to write.
        :return: True if it is safe to write, False otherwise.
        """
        with self.lock:
            return self.serial is not None and self.serial.is_open
        
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()

    def close(self):
        self.stop()


def main():
    # Example usage of ThreadSafeSerial with context manager
    logging.basicConfig(level=logging.INFO)

    with ThreadSafeSerial(
        baudrate=115200,
        search_pattern=r"ACM|USB",
    ) as serial_manager:
        try:
            # Start a thread for handling serial data
            threading.Thread(target=serial_manager.run, daemon=True).start()

            while True:
                command = input("Enter command (or 'exit' to quit): ")
                if command.lower() == "exit":
                    break
                serial_manager.write(command)

        except KeyboardInterrupt:
            print("Exiting...")


if __name__ == "__main__":
    main()

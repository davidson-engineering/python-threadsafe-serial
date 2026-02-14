#!/usr/bin/env python
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------
# Created By  : Matthew Davidson
# Created Date: 2024-01-01
# version ='0.0.1'
# ---------------------------------------------------------------------------
"""a_short_project_description"""
# ---------------------------------------------------------------------------

import logging
import threading

from threadsafe_serial.threadsafe_serial import ThreadSafeSerial


def send_data(serial_manager):
    """Send data to the serial port."""
    import random

    while True:
        data = random.choice(["Hello\n", "World\n", "123\n"])
        if data.lower() == "exit":
            serial_manager.stop()
        else:
            serial_manager.write(data)


def listen_for_data(serial_manager: ThreadSafeSerial):
    """Listen for data from the serial port."""
    while True:
        data = serial_manager.read_until()
        if data:
            print(f"Received data: {data}")


def main():
    # Create a shared instance of ThreadSafeSerial
    serial_manager = ThreadSafeSerial(baudrate=115200, search_pattern=r"ACM|USB")

    # Start threads for each module
    sender_thread = threading.Thread(
        target=send_data, args=(serial_manager,), daemon=True
    )
    receiver_thread = threading.Thread(
        target=listen_for_data, args=(serial_manager,), daemon=True
    )

    sender_thread.start()
    receiver_thread.start()

    try:
        while True:
            pass  # Main thread can perform other tasks or wait
    except KeyboardInterrupt:
        print("Exiting...")
    finally:
        serial_manager.stop()


if __name__ == "__main__":
    main()

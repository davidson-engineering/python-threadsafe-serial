import pytest
from unittest.mock import MagicMock, patch

import serial

from threadsafe_serial import ThreadSafeSerial


@pytest.fixture
def mock_serial():
    """Create a mock serial.Serial instance."""
    mock = MagicMock(spec=serial.Serial)
    mock.is_open = True
    mock.in_waiting = 0
    mock.read.return_value = b""
    mock.write.return_value = None
    return mock


@pytest.fixture
def serial_manager(mock_serial):
    """Create a ThreadSafeSerial with mocked serial and suppressed threads."""
    with patch("threadsafe_serial.threadsafe_serial.serial.Serial", return_value=mock_serial), \
         patch.object(ThreadSafeSerial, "_read_serial"), \
         patch.object(ThreadSafeSerial, "_write_serial"):
        manager = ThreadSafeSerial(port="/dev/ttyTEST", baudrate=9600)
        yield manager
        manager.running = False

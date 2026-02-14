from abc import ABC, abstractmethod
from collections import deque
import time
from typing import Callable, Optional


class PacketReader(ABC):
    """Abstract base class for packet readers."""

    def __init__(self, read_callback: Callable[[], Optional[bytes]]) -> None:
        self.read_callback = read_callback

    @abstractmethod
    def read_packet(self) -> Optional[bytes]:
        pass


class WindowedPacketReader(PacketReader):
    """Sliding window packet reader with start/end byte framing."""

    def __init__(
        self,
        read_callback: Callable[[], Optional[bytes]],
        window_size: int = 10,
        start_byte: int = 0xA5,
        end_byte: int = 0x5A,
        timeout: float = 1.0,
    ) -> None:
        super().__init__(read_callback)
        self.window_size = window_size
        self.start_byte = start_byte
        self.end_byte = end_byte
        self.timeout = timeout

    def read_packet(self) -> Optional[bytes]:
        """Read packets using a sliding window."""
        start_time = time.time()
        buffer: deque[int] = deque(maxlen=self.window_size)

        while time.time() - start_time <= self.timeout:
            data = self.read_callback()
            if data:
                buffer.extend(data)

                if (
                    len(buffer) == self.window_size
                    and buffer[0] == self.start_byte
                    and buffer[-1] == self.end_byte
                ):
                    return bytes(buffer)[1:-1]
        return None

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("python-threadsafe-serial")
except PackageNotFoundError:
    __version__ = "0.0.0"

from .threadsafe_serial import ThreadSafeSerial
from .packet_reader import PacketReader, WindowedPacketReader

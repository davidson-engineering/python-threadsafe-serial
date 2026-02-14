# python-threadsafe-serial

A Python library for thread-safe serial port communication. Multiple threads can read from and write to the same serial port without data corruption.

## Features

- Thread-safe read/write with automatic locking
- Continuous background reader that buffers incoming bytes
- Non-blocking `write_latest()` for real-time control (keeps only the most recent command)
- Automatic device detection by USB/ACM pattern matching
- Auto-reconnection on disconnect with configurable retry limits
- Packet framing via `WindowedPacketReader` (start/end byte sliding window)
- Context manager support

## Installation

```bash
pip install git+https://github.com/davidson-engineering/python-threadsafe-serial.git
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add git+https://github.com/davidson-engineering/python-threadsafe-serial.git
```

## Quick start

```python
import threading
from threadsafe_serial import ThreadSafeSerial

# Auto-detects the first USB/ACM device if port is None
serial = ThreadSafeSerial(port="/dev/ttyUSB0", baudrate=115200)

def reader():
    while True:
        line = serial.readline()
        if line:
            print(f"Received: {line}")

threading.Thread(target=reader, daemon=True).start()

# Blocking write
serial.write(b"HELLO\r\n")

# Non-blocking write (only the latest value is sent)
serial.write_latest(b"SET_SPEED 100\r\n")

serial.stop()
```

Or as a context manager:

```python
with ThreadSafeSerial(baudrate=115200) as serial:
    serial.write(b"ping\r\n")
    data = serial.read()
```

## API

### ThreadSafeSerial

| Method | Description |
|---|---|
| `read(size=-1)` | Read `size` bytes from the buffer (default: all available) |
| `read_until(expected, max_bytes)` | Read up to a delimiter, returns `None` if not yet found |
| `readline(terminator)` | Shorthand for `read_until(terminator)` |
| `write(data)` | Blocking write (accepts `str` or `bytes`) |
| `write_latest(data)` | Non-blocking write that replaces any pending command |
| `detect_devices()` | List serial devices matching `search_pattern` |
| `stop()` / `close()` | Stop background threads and close the port |
| `in_waiting` | Number of buffered bytes (property) |
| `is_open` | Whether the serial port is open (property) |

### Constructor parameters

```python
ThreadSafeSerial(
    port=None,              # e.g. "/dev/ttyUSB0", None for auto-detect
    baudrate=9600,
    timeout=1,
    bytesize=serial.EIGHTBITS,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    max_reconnect_attempts=0,  # 0 = retry forever
    search_pattern=r"ACM|USB",
)
```

### WindowedPacketReader

For binary protocols with start/end byte framing:

```python
from threadsafe_serial import ThreadSafeSerial, WindowedPacketReader

serial = ThreadSafeSerial(port="/dev/ttyACM0", baudrate=115200)
reader = WindowedPacketReader(
    read_callback=serial.read,
    window_size=10,
    start_byte=0xA5,
    end_byte=0x5A,
)

packet = reader.read_packet()  # returns payload bytes or None on timeout
```

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
uv run pytest --cov
```

## License

MIT. See [LICENSE](LICENSE).


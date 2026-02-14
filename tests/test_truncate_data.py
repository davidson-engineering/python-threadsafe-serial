from threadsafe_serial.threadsafe_serial import truncate_data


class TestTruncateData:
    def test_short_data_unchanged(self):
        assert truncate_data(b"hello") == b"hello"

    def test_exact_max_len(self):
        data = b"x" * 30
        assert truncate_data(data) == data

    def test_over_max_len_truncated(self):
        data = b"x" * 31
        assert truncate_data(data) == b"x" * 30 + b"..."

    def test_custom_max_len(self):
        assert truncate_data(b"hello world", max_len=5) == b"hello..."

    def test_empty_data(self):
        assert truncate_data(b"") == b""

    def test_single_byte_over(self):
        assert truncate_data(b"ab", max_len=1) == b"a..."

import numpy as np
import pytest
from planetary_studio.ser import SerReader, SerWriter, HEADER
from planetary_studio.imaging import debayer


@pytest.mark.parametrize("bits", [8, 12, 16])
@pytest.mark.parametrize("pattern", ["MONO", "RGGB", "GRBG", "GBRG", "BGGR", "RGB"])
def test_raw_recording_preserves_pixels_and_timestamps(tmp_path, bits, pattern):
    shape = (16, 24, 3) if pattern == "RGB" else (16, 24)
    rng = np.random.default_rng(23)
    pixels = rng.integers(0, 2**bits, shape, dtype=np.uint8 if bits <= 8 else np.uint16)
    path = tmp_path / "test.ser"
    with SerWriter(path, shape, bits, pattern) as writer:
        writer.write(pixels, 621355968000000001)
        writer.write(pixels, 621355968000000002)
    with SerReader(path) as reader:
        assert reader.count == 2 and reader.pattern == pattern and reader.bits == bits
        np.testing.assert_array_equal(reader.read(1), pixels)
        assert reader.timestamp(0) == 621355968000000001
        assert reader.timestamp(1) == 621355968000000002


def test_big_endian_ser_from_external_writer(tmp_path):
    path = tmp_path / "external.ser"
    pixels = np.arange(64, dtype=np.uint16).reshape(8, 8) * 1000
    header = HEADER.pack(b"LUCAM-RECORDER", 0, 0, 1, 8, 8, 16, 1, b"", b"", b"", 0, 0)
    path.write_bytes(header + pixels.astype(">u2").tobytes())
    with SerReader(path) as reader:
        np.testing.assert_array_equal(reader.read(0), pixels)


def test_truncated_ser_is_rejected(tmp_path):
    path = tmp_path / "broken.ser"
    with SerWriter(path, (8, 8)) as writer:
        writer.write(np.zeros((8, 8), np.uint8))
    path.write_bytes(path.read_bytes()[:180])
    with pytest.raises(ValueError, match="Truncated"):
        SerReader(path)


def test_writer_never_overwrites_or_accepts_changed_shape(tmp_path):
    path = tmp_path / "record.ser"
    with SerWriter(path, (8, 8)) as writer:
        with pytest.raises(ValueError, match="size"):
            writer.write(np.zeros((16, 16), np.uint8))
        writer.write(np.zeros((8, 8), np.uint8))
    with pytest.raises(FileExistsError):
        SerWriter(path, (8, 8))


@pytest.mark.parametrize("pattern", ["RGGB", "GRBG", "GBRG", "BGGR"])
def test_bayer_channels_have_correct_color_order(pattern):
    grid = np.empty((32, 32), np.uint8)
    for y in range(2):
        for x in range(2):
            grid[y::2, x::2] = {"R": 200, "G": 100, "B": 40}[pattern[y * 2 + x]]
    rgb = debayer(grid, pattern)
    np.testing.assert_array_equal(rgb[8, 8], [200, 100, 40])

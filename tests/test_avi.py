"""The RIFF byte engine: parse_avi/write_avi round-trip contracts."""

import pytest

from datamosh import parse_avi, write_avi
from datamosh.avi import header_info

from .conftest import needs_ffmpeg


def chunk_shape(chunks):
    return [(c["stream"], c["key"], c["data"]) for c in chunks]


@needs_ffmpeg
def test_parse_classifies_streams_and_keyframes(moshable):
    _, _, chunks = parse_avi(moshable)
    assert chunks, "no movi chunks parsed"
    assert {c["stream"] for c in chunks} <= {"v", "a"}
    video = [c for c in chunks if c["stream"] == "v"]
    assert video[0]["key"], "first video chunk should be a keyframe"
    assert sum(1 for c in video if c["key"]) >= 2, (
        "make_moshable should have planted multiple keyframes"
    )
    # every chunk's declared size matches its blob (8-byte header + payload + pad)
    import struct

    for c in chunks:
        size = struct.unpack_from("<I", c["data"], 4)[0]
        assert len(c["data"]) == 8 + size + (size & 1)


@needs_ffmpeg
def test_write_parse_round_trip_preserves_chunks(moshable, tmp_path):
    header, movi_start, chunks = parse_avi(moshable)
    out = tmp_path / "rewritten.avi"
    write_avi(str(out), header, movi_start, chunks)
    _, _, chunks2 = parse_avi(str(out))
    assert chunk_shape(chunks) == chunk_shape(chunks2)


@needs_ffmpeg
def test_rewrite_is_byte_stable(moshable, tmp_path):
    """write -> parse -> write reproduces the file byte-for-byte."""
    header, movi_start, chunks = parse_avi(moshable)
    a, b = tmp_path / "a.avi", tmp_path / "b.avi"
    write_avi(str(a), header, movi_start, chunks)
    header2, movi_start2, chunks2 = parse_avi(str(a))
    write_avi(str(b), header2, movi_start2, chunks2)
    assert a.read_bytes() == b.read_bytes()


@needs_ffmpeg
def test_header_info_reads_moshable_header(moshable):
    header, _, _ = parse_avi(moshable)
    info = header_info(header)
    assert (info["width"], info["height"]) == (160, 120)
    assert info["fps"] == pytest.approx(15, abs=0.01)
    assert info["codec"] == "FMP4"


def test_parse_rejects_non_avi(tmp_path):
    bogus = tmp_path / "not_an.avi"
    bogus.write_bytes(b"MThd" + b"\x00" * 64)
    with pytest.raises(ValueError, match="not an AVI"):
        parse_avi(str(bogus))

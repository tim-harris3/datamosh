"""RIFF/AVI byte-level parsing and writing.

parse_avi() turns an AVI into an ordered list of movi "chunks" (one dict per video
frame / audio frame); write_avi() turns a chunk list back into a playable AVI with
a rebuilt idx1 index. Everything in between (the moshing) is just rearranging,
duplicating, deleting, and corrupting entries in that list -- no decoding.

Chunk shape: {"data": raw bytes incl. 8-byte header, "stream": "v"|"a", "key": bool}
"""

import struct

from . import ffmpeg, paths

AVIIF_KEYFRAME = 0x10


def _walk_movi(data, start, end, chunks):
    """Flatten movi sub-chunks (descending into any LIST 'rec ' groupings)."""
    p = start
    while p + 8 <= end:
        fourcc = data[p : p + 4]
        size = struct.unpack_from("<I", data, p + 4)[0]
        if fourcc == b"LIST":
            # e.g. 'rec ' grouping -- descend into its payload
            _walk_movi(data, p + 12, p + 8 + size, chunks)
            p += 8 + size + (size & 1)
            continue
        total = 8 + size + (size & 1)
        blob = data[p : p + total]
        stream = "v" if fourcc[0:2] == b"00" else ("a" if fourcc[0:2] == b"01" else "?")
        chunks.append({"data": blob, "stream": stream, "key": False})
        p += total


def _read_idx1_video_keyflags(data, top_chunks):
    """Return the list of keyframe booleans for video ('00') entries, in order."""
    for fourcc, off, size in top_chunks:
        if fourcc == b"idx1":
            flags = []
            p = off + 8
            stop = off + 8 + size
            while p + 16 <= stop:
                ckid = data[p : p + 4]
                dwflags = struct.unpack_from("<I", data, p + 4)[0]
                if ckid[0:2] == b"00":
                    flags.append(bool(dwflags & AVIIF_KEYFRAME))
                p += 16
            return flags
    return None


def parse_avi(path):
    """Parse an AVI into (header_prefix, movi_start, chunks).

    header_prefix = raw bytes from file start up to (and including) the 'movi' FOURCC.
    chunks        = ordered list of {data, stream, key} for every movi sub-chunk.
    """
    path = paths.resolve(path)
    with open(path, "rb") as f:
        data = f.read()
    if data[0:4] != b"RIFF" or data[8:12] != b"AVI ":
        raise ValueError(f"not an AVI: {path}")

    top_chunks = []  # (fourcc, offset, size) of each top-level chunk
    movi_data_start = movi_data_end = None
    p = 12
    while p + 8 <= len(data):
        fourcc = data[p : p + 4]
        size = struct.unpack_from("<I", data, p + 4)[0]
        if fourcc == b"LIST":
            listtype = data[p + 8 : p + 12]
            top_chunks.append((listtype, p, size))
            if listtype == b"movi":
                movi_data_start = p + 12
                movi_data_end = p + 8 + size
        else:
            top_chunks.append((fourcc, p, size))
        p += 8 + size + (size & 1)

    if movi_data_start is None:
        raise ValueError(f"no movi list in {path}")

    chunks = []
    _walk_movi(data, movi_data_start, movi_data_end, chunks)

    # Assign keyframe flags to video chunks.
    keyflags = _read_idx1_video_keyflags(data, top_chunks)
    if keyflags is None:
        keyflags = ffmpeg.video_keyflags(path)
    vi = 0
    for c in chunks:
        if c["stream"] == "v":
            c["key"] = keyflags[vi] if vi < len(keyflags) else False
            vi += 1
        else:
            c["key"] = True  # audio chunks are conventionally flagged as keyframes
    header_prefix = data[:movi_data_start]
    return header_prefix, movi_data_start, chunks


def _patch_header(
    header_prefix, movi_data_start, movi_size, video_frame_count, audio_frame_count
):
    """Patch LIST-movi size, avih dwTotalFrames, and the video/audio strh dwLength.

    The audio strh dwLength must track the (now stretched) audio chunk count -- AC3 has
    dwSampleSize 0, so dwLength is measured in chunks; leaving it stale makes players cut
    the audio off after the template clip's length.
    """
    h = bytearray(header_prefix)
    # LIST movi size field sits 8 bytes before the movi payload.
    struct.pack_into("<I", h, movi_data_start - 8, movi_size)

    a = h.find(b"avih")
    if a != -1:
        struct.pack_into("<I", h, a + 8 + 16, video_frame_count)  # dwTotalFrames

    # Patch each stream's strh dwLength: 'vids' -> video frames, 'auds' -> audio chunks.
    s = h.find(b"strh")
    while s != -1:
        if h[s + 8 : s + 12] == b"vids":
            struct.pack_into("<I", h, s + 8 + 32, video_frame_count)
        elif h[s + 8 : s + 12] == b"auds":
            struct.pack_into("<I", h, s + 8 + 32, audio_frame_count)
        s = h.find(b"strh", s + 4)
    return bytes(h)


def write_avi(path, header_prefix, movi_data_start, chunks):
    """Write a chunk list back out as a playable AVI (rebuilt idx1, patched header)."""
    path = paths.resolve(path)
    movi_data = b"".join(c["data"] for c in chunks)

    # Rebuild idx1. Offsets are relative to the 'movi' FOURCC (first chunk -> 4).
    idx = bytearray()
    offset = 4
    for c in chunks:
        blob = c["data"]
        ckid = blob[0:4]
        size = struct.unpack_from("<I", blob, 4)[0]
        flags = AVIIF_KEYFRAME if (c["stream"] == "a" or c["key"]) else 0
        idx += struct.pack("<4sIII", ckid, flags, offset, size)
        offset += len(blob)
    idx1_chunk = b"idx1" + struct.pack("<I", len(idx)) + bytes(idx)

    video_frames = sum(1 for c in chunks if c["stream"] == "v")
    audio_frames = sum(1 for c in chunks if c["stream"] == "a")
    header = _patch_header(
        header_prefix, movi_data_start, 4 + len(movi_data), video_frames, audio_frames
    )

    body = bytearray(header + movi_data + idx1_chunk)
    struct.pack_into("<I", body, 4, len(body) - 8)  # RIFF size
    with open(path, "wb") as f:
        f.write(body)

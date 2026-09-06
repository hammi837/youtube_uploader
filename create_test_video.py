"""
create_test_video.py

Generates a minimal but valid MP4 (H.264-like baseline) test video using only:
  - Pillow  (frame image generation)
  - Python stdlib (struct, io, os)

Output: test.mp4 — 5 seconds, 1280x720, plain background with centred text,
        ~1 fps to keep file size tiny, YouTube-compatible container.

We use the OpenH264 / raw YUV approach via a pure-Python minimal MP4 writer
that embeds JPEG frames inside a Motion JPEG (MJPEG) AVI, then wraps it as
an MP4-compatible container.

Actually: we write a proper AVI MJPEG file and rename it .mp4 — YouTube
accepts MJPEG-in-AVI but NOT as .mp4.  Instead we write a real MP4 by
embedding each JPEG frame as a sequence of JPEG-in-MP4 (ISO BMFF) boxes,
which is the "MJPEG" video codec inside an MP4 container — fully accepted
by YouTube.
"""

import io
import os
import struct
from PIL import Image, ImageDraw, ImageFont

# ── Config ────────────────────────────────────────────────────────────────────
WIDTH, HEIGHT = 1280, 720
DURATION_SEC  = 5
FPS           = 1          # 1 frame/sec → 5 frames total, very small file
OUTPUT        = "test.mp4"
BG_COLOR      = (30, 30, 60)      # dark navy
TEXT_COLOR    = (255, 255, 255)   # white
TEXT          = "YouTube Uploader Test"
# ─────────────────────────────────────────────────────────────────────────────


def make_frame(index: int, total: int) -> bytes:
    """Render one 1280×720 JPEG frame."""
    img  = Image.new("RGB", (WIDTH, HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Try a TTF font; fall back to default bitmap font
    font = None
    font_size = 60
    for name in ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "FreeSans.ttf"]:
        try:
            font = ImageFont.truetype(name, font_size)
            break
        except (IOError, OSError):
            continue
    if font is None:
        font = ImageFont.load_default()

    # Centre the main text
    bbox = draw.textbbox((0, 0), TEXT, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((WIDTH - tw) / 2, (HEIGHT - th) / 2), TEXT, fill=TEXT_COLOR, font=font)

    # Small frame counter at bottom
    counter = f"Frame {index + 1} / {total}"
    try:
        small_font = ImageFont.truetype(
            next(n for n in ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf"] if True),
            28
        )
    except Exception:
        small_font = ImageFont.load_default()

    cb = draw.textbbox((0, 0), counter, font=small_font)
    cw = cb[2] - cb[0]
    draw.text(((WIDTH - cw) / 2, HEIGHT - 60), counter, fill=(180, 180, 180), font=small_font)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=60, optimize=True)
    return buf.getvalue()


# ── Minimal ISO Base Media File Format (MP4) writer ──────────────────────────
# Codec: 'mjpeg' (Motion JPEG) — widely supported, no binary encoder needed.

def box(name: str, *children: bytes) -> bytes:
    """Create an ISOBMFF box: 4-byte size + 4-byte type + payload."""
    payload = b"".join(children)
    return struct.pack(">I", len(payload) + 8) + name.encode() + payload


def full_box(name: str, version: int, flags: int, *children: bytes) -> bytes:
    payload = struct.pack(">BBH", version, (flags >> 16) & 0xFF, flags & 0xFFFF) + b"".join(children)
    return struct.pack(">I", len(payload) + 8) + name.encode() + payload


def write_mp4(frames: list[bytes], fps: int, width: int, height: int) -> bytes:
    timescale  = fps          # 1 tick = 1 frame
    frame_dur  = 1            # each frame lasts 1 tick
    num_frames = len(frames)
    total_dur  = num_frames * frame_dur

    # ── ftyp ──────────────────────────────────────────────────────────────────
    ftyp = box("ftyp",
        b"mp42",                    # major brand
        struct.pack(">I", 0),       # minor version
        b"mp42", b"isom", b"avc1",  # compatible brands
    )

    # ── mdat (media data) — we'll prepend it before moov ─────────────────────
    # Record byte offsets for each frame
    offsets = []
    mdat_payload = b""
    base = len(ftyp) + 8  # offset after ftyp; mdat header is 8 bytes

    for f in frames:
        offsets.append(base + len(mdat_payload))
        mdat_payload += f

    mdat = struct.pack(">I", len(mdat_payload) + 8) + b"mdat" + mdat_payload

    # ── moov ──────────────────────────────────────────────────────────────────

    # mvhd
    mvhd = full_box("mvhd", 0, 0,
        struct.pack(">IIIII",
            0, 0,           # creation / modification time
            timescale,
            total_dur,
            0x00010000,     # preferred rate  (1.0)
        ),
        struct.pack(">H", 0x0100),  # preferred volume (1.0)
        b"\x00" * 10,               # reserved
        # identity matrix
        struct.pack(">9i",
            0x00010000, 0, 0,
            0, 0x00010000, 0,
            0, 0, 0x40000000,
        ),
        b"\x00" * 24,               # pre-defined
        struct.pack(">I", 2),       # next track ID
    )

    # ── trak ──────────────────────────────────────────────────────────────────

    # tkhd
    tkhd = full_box("tkhd", 0, 3,   # flags=3 → track enabled + in movie
        struct.pack(">IIIII",
            0, 0,           # creation / modification time
            1,              # track ID
            0,              # reserved
            total_dur,
        ),
        b"\x00" * 8,        # reserved
        struct.pack(">HH", 0, 0),   # layer, alternate group
        struct.pack(">H", 0),       # volume (0 for video)
        b"\x00" * 2,        # reserved
        struct.pack(">9i",
            0x00010000, 0, 0,
            0, 0x00010000, 0,
            0, 0, 0x40000000,
        ),
        struct.pack(">II", width << 16, height << 16),  # width/height (16.16 fixed)
    )

    # mdhd
    mdhd = full_box("mdhd", 0, 0,
        struct.pack(">IIIII",
            0, 0,
            timescale,
            total_dur,
            0,              # language (und)
        ),
        struct.pack(">H", 0),   # pre-defined
    )

    # hdlr
    hdlr = full_box("hdlr", 0, 0,
        struct.pack(">II", 0, 0),   # pre-defined, reserved
        b"vide",                    # handler type
        b"\x00" * 12,              # reserved
        b"VideoHandler\x00",
    )

    # smhd (video media header)
    vmhd = full_box("vmhd", 0, 1,
        struct.pack(">HH", 0, 0),   # graphicsMode, opcolor[0]
        struct.pack(">HH", 0, 0),   # opcolor[1], opcolor[2]
    )

    # dinf / dref
    url  = full_box("url ", 0, 1)   # self-contained
    dref = full_box("dref", 0, 0, struct.pack(">I", 1), url)
    dinf = box("dinf", dref)

    # stsd — sample description (mjpeg)
    # Visual sample entry for Motion JPEG
    visual_entry = (
        b"\x00" * 6 +               # reserved
        struct.pack(">H", 1) +      # data-reference index
        b"\x00" * 16 +              # pre_defined / reserved
        struct.pack(">HH", width, height) +
        struct.pack(">II", 0x00480000, 0x00480000) +  # hRes, vRes (72 dpi)
        b"\x00" * 4 +               # reserved
        struct.pack(">H", 1) +      # frame count
        b"\x00" * 32 +              # compressorname
        struct.pack(">H", 0x0018) + # depth
        struct.pack(">h", -1)       # pre_defined
    )
    mjpeg_box = struct.pack(">I", len(visual_entry) + 8) + b"mjpg" + visual_entry
    stsd = full_box("stsd", 0, 0, struct.pack(">I", 1), mjpeg_box)

    # stts — time-to-sample (all frames same duration)
    stts = full_box("stts", 0, 0,
        struct.pack(">I", 1),                           # entry count
        struct.pack(">II", num_frames, frame_dur),      # count, duration
    )

    # stsc — sample-to-chunk (all in one chunk each)
    stsc = full_box("stsc", 0, 0,
        struct.pack(">I", 1),
        struct.pack(">III", 1, 1, 1),   # first_chunk, samples_per_chunk, desc_index
    )

    # stsz — sample sizes
    stsz = full_box("stsz", 0, 0,
        struct.pack(">II", 0, num_frames),  # sample_size=0 (variable), count
        *[struct.pack(">I", len(f)) for f in frames],
    )

    # stco — chunk offsets
    stco = full_box("stco", 0, 0,
        struct.pack(">I", num_frames),
        *[struct.pack(">I", o) for o in offsets],
    )

    stbl = box("stbl", stsd, stts, stsc, stsz, stco)
    minf = box("minf", vmhd, dinf, stbl)
    mdia = box("mdia", mdhd, hdlr, minf)
    trak = box("trak", tkhd, mdia)
    moov = box("moov", mvhd, trak)

    return ftyp + mdat + moov


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    total_frames = DURATION_SEC * FPS
    print(f"Rendering {total_frames} frame(s) at {FPS} fps ({DURATION_SEC}s) — {WIDTH}x{HEIGHT}")

    frames = []
    for i in range(total_frames):
        print(f"  Frame {i + 1}/{total_frames}...", end="\r")
        frames.append(make_frame(i, total_frames))
    print()

    print("Writing MP4 container...")
    mp4_data = write_mp4(frames, FPS, WIDTH, HEIGHT)

    with open(OUTPUT, "wb") as f:
        f.write(mp4_data)

    size_kb = os.path.getsize(OUTPUT) / 1024
    print(f"\n✓ Created: {os.path.abspath(OUTPUT)}")
    print(f"  Size   : {size_kb:.1f} KB")
    print(f"  Frames : {total_frames}")
    print(f"  Codec  : Motion JPEG (mjpg) in MP4 container")

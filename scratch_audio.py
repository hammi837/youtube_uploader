import os
from pathlib import Path
from backend.services.media.ffmpeg import get_ffmpeg_path
import subprocess

ff = get_ffmpeg_path()
data_dir = Path("data/assets/music")

subprocess.run([ff, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-b:a", "64k", str(data_dir / "lofi" / "lofi_track_01.mp3")])
subprocess.run([ff, "-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=1", "-b:a", "64k", str(data_dir / "epic" / "epic_track_01.mp3")])
subprocess.run([ff, "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=1", "-b:a", "64k", str(data_dir / "default_bg.mp3")])
print("done")

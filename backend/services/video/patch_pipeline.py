"""One-shot patch: replace lines 381-398 of pipeline.py with new caption-burn block."""

import pathlib

TARGET = pathlib.Path(__file__).parent / "pipeline.py"

NEW_LINES = """\
    # ── 10. Burn captions ──────────────────────────────────────────────────
    step(86, "Burning captions into video...")
    captioned_path = temp_dir / "captioned.mp4"
    captions_fallback_enabled = os.getenv("CAPTIONS_FALLBACK_ENABLED", "true").lower() == "true"
    audio_mixed_duration = _get_duration(str(audio_mixed_path))

    if captions_enabled and caption_srt_path.exists() and caption_srt_path.stat().st_size > 0:
        logger.info(
            "[video_pipeline %s] Caption burn started — srt=%s video=%s fallback_allowed=%s",
            job_id, caption_srt_path.name, audio_mixed_path.name, captions_fallback_enabled,
        )
        try:
            logger.info(
                "[video_pipeline %s] Caption burn command starting — "
                "video_duration=%.1fs srt_size=%d bytes",
                job_id, audio_mixed_duration, caption_srt_path.stat().st_size,
            )
            burn_captions(
                audio_mixed_path, caption_srt_path, captioned_path, width, height,
                video_duration=audio_mixed_duration,
            )
            logger.info(
                "[video_pipeline %s] Caption burn completed — output=%s size=%.1f MB",
                job_id, captioned_path.name, captioned_path.stat().st_size / 1024 / 1024,
            )
            step(88, "Captions burned.")
        except Exception as exc:
            logger.error(
                "[video_pipeline %s] Caption burn failed: %s",
                job_id, exc,
            )
            if captions_fallback_enabled:
                logger.warning(
                    "[video_pipeline %s] Fallback decision: CAPTIONS_FALLBACK_ENABLED=true "
                    "— copying uncaptioned video. Set CAPTIONS_FALLBACK_ENABLED=false "
                    "to enforce captions and fail instead.",
                    job_id,
                )
                import shutil
                shutil.copy2(str(audio_mixed_path), str(captioned_path))
                step(88, "Caption burn failed — using video without captions (fallback).")
            else:
                logger.error(
                    "[video_pipeline %s] Fallback decision: CAPTIONS_FALLBACK_ENABLED=false "
                    "— re-raising caption burn failure.",
                    job_id,
                )
                raise
    else:
        logger.info(
            "[video_pipeline %s] Skipping caption burn (no captions or empty file) — "
            "captions_enabled=%s srt_exists=%s",
            job_id, captions_enabled,
            caption_srt_path.exists() if caption_srt_path else False,
        )
        import shutil
        shutil.copy2(str(audio_mixed_path), str(captioned_path))
        step(88, "Captions skipped.")
""".splitlines()  # no trailing newline on each element

# Read with universal newlines (Python normalises CRLF → LF internally)
with open(TARGET, "r", encoding="utf-8") as fh:
    lines = fh.read().splitlines()  # list of lines WITHOUT any line-ending chars

total = len(lines)
print(f"Total lines read: {total}")

# Lines 381-398 → indices 380-397 inclusive
START_IDX = 380   # line 381
END_IDX   = 397   # line 398 (inclusive)

# Sanity-check: print the block we are about to replace
print("=== BLOCK BEING REPLACED ===")
for i, ln in enumerate(lines[START_IDX:END_IDX + 1], start=START_IDX + 1):
    print(f"  {i:4d}: {ln}")
print("=== END OF BLOCK ===")

# Perform the splice
patched = lines[:START_IDX] + NEW_LINES + lines[END_IDX + 1:]
print(f"Lines after patch: {len(patched)}")

# Write back with CRLF line endings
with open(TARGET, "w", encoding="utf-8", newline="\r\n") as fh:
    fh.write("\n".join(patched) + "\n")

print("Patch applied successfully.")

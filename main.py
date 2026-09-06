"""
main.py — CLI entrypoint for the YouTube uploader.

Usage:
    python main.py --file path/to/video.mp4 --title "My Video" [options]

Run `python main.py --help` for full usage.
"""

import argparse
import sys
from datetime import datetime
from youtube import upload_video
from backend.services.scheduler import parse_schedule_time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload a video to YouTube via the Data API v3.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Immediate private upload
  python main.py --file video.mp4 --title "My Vlog"

  # Immediate public upload with tags
  python main.py --file clip.mp4 --title "Tutorial" --privacy public --tags python coding

  # Scheduled upload (video stays private until publish time)
  python main.py --file video.mp4 --title "Scheduled Video" --schedule "2026-09-05T18:00:00"

  # Scheduled with description and tags
  python main.py --file video.mp4 --title "My Video" --description "Check this out" --tags vlog tutorial --schedule "2026-09-05T18:00:00"
        """,
    )

    parser.add_argument(
        "--file", "-f",
        required=True,
        help="Path to the video file to upload.",
    )
    parser.add_argument(
        "--title", "-t",
        required=True,
        help="Video title (max 100 characters).",
    )
    parser.add_argument(
        "--description", "-d",
        default="",
        help="Video description (max 5000 characters).",
    )
    parser.add_argument(
        "--tags",
        nargs="*",
        default=[],
        metavar="TAG",
        help="Space-separated list of tags.",
    )
    parser.add_argument(
        "--category",
        default="22",
        metavar="ID",
        help="YouTube category ID (default: 22 = People & Blogs).",
    )
    parser.add_argument(
        "--privacy",
        choices=["private", "unlisted", "public"],
        default="private",
        help="Privacy status of the video (default: private). Ignored when --schedule is used.",
    )
    parser.add_argument(
        "--schedule", "-s",
        default=None,
        metavar="DATETIME",
        help=(
            "Schedule the video to go public at this time. "
            "Format: YYYY-MM-DDTHH:MM:SS (e.g. 2026-09-05T18:00:00). "
            "Treated as local time unless a UTC offset is included. "
            "The video will be uploaded as private and published automatically. "
            "Must be at least 1 minute in the future."
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Parse and validate --schedule before hitting the API
    publish_at = None
    if args.schedule:
        try:
            publish_at = parse_schedule_time(args.schedule)
        except ValueError as e:
            print(f"Scheduling error: {e}", file=sys.stderr)
            sys.exit(1)

    try:
        upload_video(
            file_path=args.file,
            title=args.title,
            description=args.description,
            tags=args.tags,
            category_id=args.category,
            privacy_status=args.privacy,
            publish_at=publish_at,
        )
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nUpload cancelled by user.")
        sys.exit(0)
    except Exception as e:
        print(f"Upload failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

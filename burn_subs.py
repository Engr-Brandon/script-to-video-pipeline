#!/usr/bin/env python3
"""
Burn Subtitles - Permanently hard-burns an .srt subtitle file into a video
using ffmpeg, so the captions are baked into the video pixels themselves
(unlike VLC's "load subtitle" feature, which only overlays them during
playback and does not modify the file).

This is what you need before uploading to YouTube if you want subtitles
to always show, since YouTube just plays the raw video file.

Usage:
    python burn_subs.py <input_video> <input_srt> [output_video]

Example:
    python burn_subs.py output/Video.mp4 output/subtitles.srt

Requirements:
- ffmpeg (built with libass support) in system PATH
"""

import os
import re
import sys
import subprocess
import time

# Subtitle appearance - tweak to taste
SUBTITLE_STYLE = (
    "FontName=Arial,FontSize=14,PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H00000000,BorderStyle=3,Outline=1,Shadow=0,"
    "Alignment=2,MarginV=40"
)

TARGET_FPS = 30


def check_libass_available():
    """Warn early if this ffmpeg build can't burn subtitles at all."""
    result = subprocess.run(["ffmpeg", "-filters"], capture_output=True, text=True)
    if "subtitles" not in result.stdout:
        print("WARNING: Your ffmpeg build does not appear to include the "
              "'subtitles' filter (libass). Subtitle burning will fail.")
        print("You may need an ffmpeg build with --enable-libass.")


def probe_duration(video_path):
    """Get video duration in seconds using ffprobe. Returns 0.0 on failure."""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", video_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


_TIME_RE = re.compile(r"time=(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


def parse_time_from_stderr(line):
    """Extract encoded time in seconds from an ffmpeg progress line, or None."""
    m = _TIME_RE.search(line)
    if not m:
        return None
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def format_hms(seconds):
    """Format seconds as HH:MM:SS."""
    s = max(0, int(seconds))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def burn_subtitles(input_video, srt_path, output_video):
    """Burn an SRT file into a video using ffmpeg's subtitles filter."""
    # The subtitles filter argument is single-quoted, so backslashes are
    # literal, not escape characters. Just use forward slashes -- do NOT
    # manually escape the drive-letter colon on Windows, that inserts a
    # literal backslash into the path and breaks it.
    safe_srt = srt_path.replace("\\", "/")

    vf = f"subtitles='{safe_srt}':force_style='{SUBTITLE_STYLE}'"

    cmd = [
        "ffmpeg", "-y",
        "-i", input_video,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-r", str(TARGET_FPS),
        "-c:a", "copy",
        output_video
    ]

    print(f"Burning subtitles...")
    print(f"  Input video:  {input_video}")
    print(f"  Input srt:    {srt_path}")
    print(f"  Output video: {output_video}\n")

    total_seconds = probe_duration(input_video)
    if total_seconds > 0:
        print(f"  Total duration: {format_hms(total_seconds)}\n")
    else:
        print(f"  (Could not determine total duration; ETA will be unavailable.)\n")

    # Stream stderr so we can render a progress bar. We collect every line so
    # the error-reporting path below still has them to print on failure.
    stderr_lines = []
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    start_time = time.time()
    last_render = 0.0
    have_total = total_seconds > 0
    BAR_WIDTH = 30

    try:
        for line in proc.stderr:
            stderr_lines.append(line)
            current = parse_time_from_stderr(line)
            if current is None:
                continue

            now = time.time()
            # Don't redraw more than ~10 times/sec to avoid flicker.
            if now - last_render < 0.1 and current < total_seconds:
                continue
            last_render = now

            if have_total and total_seconds > 0:
                # Clamp display to 99% until FFmpeg exits; the final 100%
                # is rendered after the process completes.
                pct = min(0.99, current / total_seconds)
                filled = int(pct * BAR_WIDTH)
                bar = "#" * filled + "." * (BAR_WIDTH - filled)
                elapsed_in_video = current
                eta = max(0.0, total_seconds - current)
                line_out = (
                    f"\r[{bar}] {int(pct * 100):3d}%  "
                    f"{format_hms(elapsed_in_video)} / {format_hms(total_seconds)}  "
                    f"ETA {format_hms(eta)}"
                )
            else:
                line_out = f"\rBurning subtitles...  encoded {format_hms(current)}"

            sys.stdout.write(line_out)
            sys.stdout.flush()

        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("\nAborted by user.")
        return False

    # Final newline so the next prompt sits on its own line.
    print()

    if proc.returncode != 0:
        filtered = [l.rstrip() for l in stderr_lines
                    if l.strip() and not l.startswith('ffmpeg version')]
        print("FFmpeg error:")
        print('\n'.join(filtered[:20]))
        return False

    if have_total and total_seconds > 0:
        # Final 100% line. Always printed even if the last stream tick
        # was slightly below 100% due to clamping.
        filled = BAR_WIDTH
        bar = "#" * filled
        total_wall = time.time() - start_time
        print(f"[{bar}] 100%  {format_hms(total_seconds)} / {format_hms(total_seconds)}  "
              f"done in {format_hms(total_wall)}")

    return True


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    input_video = sys.argv[1]
    srt_path = sys.argv[2]

    if len(sys.argv) >= 4:
        output_video = sys.argv[3]
    else:
        base, ext = os.path.splitext(input_video)
        output_video = f"{base}_subtitled{ext}"

    if not os.path.exists(input_video):
        print(f"ERROR: Input video not found: {input_video}")
        sys.exit(1)
    if not os.path.exists(srt_path):
        print(f"ERROR: Subtitle file not found: {srt_path}")
        sys.exit(1)

    check_libass_available()

    success = burn_subtitles(input_video, srt_path, output_video)
    if success and os.path.exists(output_video):
        print(f"\nDONE! Subtitled video saved to: {output_video}")
        print("This file has the captions permanently burned in -- safe to upload to YouTube.")
    else:
        print("\nSubtitle burn-in failed. See the ffmpeg error above.")
        sys.exit(1)


if __name__ == "__main__":
    main()


# # Command
# python burn_subs.py output\Video.mp4 output\subtitles.srt output\Video_final.mp4 

# where
#   Input video:  output\Video.mp4
#   Input srt:    output\subtitles.srt
#   Output video: output\Video_final.mp4
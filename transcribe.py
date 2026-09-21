#!/usr/bin/env python3
"""
Transcribe Video.mp4 into subtitles.srt using faster-whisper.

Setup (run once):
    pip install faster-whisper
    # faster-whisper also needs ffmpeg installed on your system:
    #   macOS:   brew install ffmpeg
    #   Ubuntu:  sudo apt install ffmpeg
    #   Windows: https://ffmpeg.org/download.html (add to PATH)

Usage:
    python transcribe.py
    python transcribe.py --input MyVideo.mp4 --output subs.srt --model small
"""

import argparse
import datetime
from faster_whisper import WhisperModel


def format_timestamp(seconds: float) -> str:
    """Convert seconds (float) into SRT timestamp format: HH:MM:SS,mmm"""
    td = datetime.timedelta(seconds=max(seconds, 0))
    total_ms = int(td.total_seconds() * 1000)
    hours, remainder = divmod(total_ms, 3600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def write_srt(segments, output_path: str):
    with open(output_path, "w", encoding="utf-8") as f:
        i = 0
        for seg in segments:
            i += 1
            start = format_timestamp(seg.start)
            end = format_timestamp(seg.end)
            text = seg.text.strip()
            f.write(f"{i}\n{start} --> {end}\n{text}\n\n")
            print(f"[{start} --> {end}] {text}")


def main():
    parser = argparse.ArgumentParser(description="Transcribe a video to an SRT subtitle file.")
    parser.add_argument("--input", "-i", default="Video.mp4", help="Path to input video (default: Video.mp4)")
    parser.add_argument("--output", "-o", default="subtitles.srt", help="Path to output SRT file (default: subtitles.srt)")
    parser.add_argument(
        "--model", "-m", default="tiny",
        choices=["tiny", "base", "small", "medium", "large-v3"],
        help="Whisper model size. Bigger = more accurate but slower (default: small)"
    )
    parser.add_argument("--language", "-l", default=None, help="Force a language code (e.g. 'en'). Auto-detected if omitted.")
    parser.add_argument(
        "--device", default="cpu", choices=["cpu", "cuda"],
        help="Run on CPU or NVIDIA GPU (default: cpu)"
    )
    parser.add_argument(
        "--compute_type", default="int8",
        help="Precision/speed tradeoff, e.g. int8 (fastest on CPU), float16 (GPU), float32 (default: int8)"
    )
    args = parser.parse_args()

    print(f"Loading faster-whisper model '{args.model}' on {args.device} ({args.compute_type})...")
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)

    print(f"Transcribing '{args.input}'... (this can take a while depending on video length and model size)")
    segments, info = model.transcribe(args.input, language=args.language)
    print(f"Detected language: {info.language} (probability {info.language_probability:.2f})")

    print(f"Writing subtitles to '{args.output}'...")
    write_srt(segments, args.output)

    print("Done!")


if __name__ == "__main__":
    main()
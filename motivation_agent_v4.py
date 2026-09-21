#!/usr/bin/env python3
"""
Video Generator - Creates AI-narrated videos from images with script voiceover.
VERSION 4: Uses DATE-BASED image selection (chronological order by file creation date).

Usage: python crime_agent_v4.py

Requirements:
- FFmpeg and ffprobe in system PATH
- pip install edge-tts
"""

import os
import random
import time
import asyncio
import json
import subprocess
import edge_tts
import time as time_module
import sys
from datetime import datetime

import subtitles_gen  # for in-process SRT build from the sidecar

# ==================== CONFIGURATION ====================
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGES_FOLDER = os.path.join(ROOT_DIR, "images")
READY_FOLDER = os.path.join(ROOT_DIR, "ready_media")
OUTPUT_FOLDER = os.path.join(ROOT_DIR, "output")
SCRIPT_FILE = os.path.join(ROOT_DIR, "script.txt")

# Voice options: en-US-GuyNeural, en-US-AvaNeural, en-GB-RyanNeural
VOICE_NAME = "en-US-AndrewNeural"

# Video processing settings
TARGET_RESOLUTION = (1920, 1080)
TARGET_FPS = 30
MIN_IMAGE_DURATION = 10.0
MAX_IMAGE_DURATION = 15.0

# ==================== UTILITY FUNCTIONS ====================

def get_duration(file_path):
    """Get video/audio duration in seconds using ffprobe."""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", file_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0

# ==================== IMAGE PROCESSING ====================

def get_image_files_by_date():
    """Get all image files from images folder, sorted by creation date (oldest first)."""
    if not os.path.exists(IMAGES_FOLDER):
        raise FileNotFoundError(f"Images folder not found: {IMAGES_FOLDER}")

    image_extensions = ('.jpg', '.jpeg', '.png', '.webp')

    # Get all images with their creation/modification dates
    image_data = []
    for f in os.listdir(IMAGES_FOLDER):
        if f.lower().endswith(image_extensions):
            full_path = os.path.join(IMAGES_FOLDER, f)
            # Get modification time (on Windows, this is the most reliable)
            # Use os.path.getmtime for modification time
            # Use os.path.getctime for creation time (Windows only)
            try:
                # Try creation time first (Windows), fallback to modification time
                timestamp = os.path.getctime(full_path)
            except OSError:
                # On non-Windows systems, use modification time
                timestamp = os.path.getmtime(full_path)

            image_data.append({
                'path': full_path,
                'filename': f,
                'timestamp': timestamp,
                'date': datetime.fromtimestamp(timestamp)
            })

    if not image_data:
        raise FileNotFoundError(f"No image files found in {IMAGES_FOLDER}")

    # Sort by date (oldest first - chronological order)
    image_data.sort(key=lambda x: x['timestamp'])

    print("Images sorted by date (oldest first):")
    for img in image_data:
        print(f"  {img['date'].strftime('%Y-%m-%d %H:%M:%S')} - {img['filename'][:50]}...")
    print()

    return image_data

# ==================== IMAGE TO VIDEO ====================

def create_static_image_clip(image_path, duration, output_path):
    """Create a video clip from an image (static, no Ken Burns effect)."""
    print(f"    > Creating clip from: {os.path.basename(image_path)}")

    vf = f"scale={TARGET_RESOLUTION[0]}:{TARGET_RESOLUTION[1]}:force_original_aspect_ratio=decrease,pad={TARGET_RESOLUTION[0]}:{TARGET_RESOLUTION[1]}:(ow-iw)/2:(oh-ih)/2"

    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", image_path,
        "-vf", vf,
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-r", str(TARGET_FPS),
        "-pix_fmt", "yuv420p",
        output_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # Filter out FFmpeg banner and show actual error
        stderr_lines = [l for l in result.stderr.split('\n') if l.strip() and not l.startswith('ffmpeg version') and not l.startswith(' built with')]
        error_msg = '\n'.join(stderr_lines[:10])
        print(f"    > FFmpeg error: {error_msg}")
        return None

    # Verify the file was created
    if not os.path.exists(output_path):
        print(f"    > ERROR: Output file not created: {output_path}")
        return None

    print(f"    > Clip created successfully")
    return output_path

# ==================== AUDIO GENERATION ====================

async def generate_audio_with_timings(text, output_path):
    """Stream edge-tts, write audio bytes to disk, and return per-word timings.

    Reuses the same streaming pattern as subtitles_gen.generate_audio_with_words
    so the main pipeline captures WordBoundary events during its first (and only)
    TTS pass, instead of needing a second edge-tts batch in the subtitle step.
    """
    communicate = edge_tts.Communicate(text, VOICE_NAME)
    words = []
    with open(output_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 10_000_000
                dur = chunk["duration"] / 10_000_000
                words.append({"start": start, "end": start + dur, "text": chunk["text"]})
    return words


def estimate_word_timings(text, duration):
    """Fallback when edge-tts doesn't emit WordBoundary events.

    Distributes words evenly across the known audio duration, weighted lightly
    by word length so long words get a bit more screen time. Same heuristic
    used in subtitles_gen.py.
    """
    raw_words = text.split()
    if not raw_words:
        return []

    weights = [max(len(w), 1) for w in raw_words]
    total_weight = sum(weights)

    words = []
    t = 0.0
    for w, weight in zip(raw_words, weights):
        span = duration * (weight / total_weight)
        words.append({"start": t, "end": t + span, "text": w})
        t += span
    return words


# ==================== MAIN PIPELINE ====================

async def generate_video():
    """Main video generation pipeline."""
    # Create output directories
    os.makedirs(READY_FOLDER, exist_ok=True)
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    # Read script
    if not os.path.exists(SCRIPT_FILE):
        raise FileNotFoundError(f"Script file not found: {SCRIPT_FILE}")

    with open(SCRIPT_FILE, "r", encoding="utf-8") as f:
        raw_text = f.read()

    paragraphs = [p.strip() for p in raw_text.split('\n\n') if p.strip()]

    if not paragraphs:
        raise ValueError("No paragraphs found in script.txt")

    print(f"Found {len(paragraphs)} paragraphs to process.")
    print(f"Found images in: {IMAGES_FOLDER}\n")

    # Get images sorted by date (chronological order)
    image_data = get_image_files_by_date()
    print(f"Using {len(image_data)} images (date-based selection)\n")

    generated_chunks = []
    timings = []  # per-paragraph {paragraph_index, audio_duration, words} for the SRT sidecar

    for idx, paragraph in enumerate(paragraphs):
        print(f"Processing paragraph {idx + 1}/{len(paragraphs)}...")

        # Generate audio for this paragraph (and capture WordBoundary timings
        # for the subtitle sidecar — one TTS call covers both the video and
        # the .srt, so we never re-hit the Edge TTS endpoint).
        temp_audio = os.path.join(READY_FOLDER, f"temp_audio_{idx}.mp3")
        try:
            words = await generate_audio_with_timings(paragraph, temp_audio)
        except Exception as e:
            print(f"  > Error generating audio: {e}")
            print(f"  > Skipping this paragraph due to network error")
            continue
        audio_duration = get_duration(temp_audio)
        print(f"  Audio: {audio_duration:.1f}s, {len(words)} timed words")

        # Fall back to length-weighted even distribution if no WordBoundary
        # events arrived. Keeps the SRT usable instead of empty.
        if not words:
            print(f"  > No WordBoundary from edge-tts; using estimated even timing")
            words = estimate_word_timings(paragraph, audio_duration)

        timings.append({
            "paragraph_index": idx,
            "audio_duration": audio_duration,
            "words": words,
        })

        # Create video clips from images in chronological order
        selected_clips = []
        current_dur = 0

        # Calculate how many images we need based on audio duration
        # Each image displays for about 5-7 seconds on average
        images_per_paragraph = max(1, int(audio_duration / 6) + 1)

        # Get the next images in chronological order for this paragraph
        # Start from where we left off, cycle through if needed
        start_idx = (idx * images_per_paragraph) % len(image_data)

        for i in range(images_per_paragraph):
            img_index = (start_idx + i) % len(image_data)
            img_path = image_data[img_index]['path']
            img_date = image_data[img_index]['date']

            # Determine clip duration
            target_dur = random.uniform(MIN_IMAGE_DURATION, MAX_IMAGE_DURATION)

            # Don't overshoot audio duration
            if current_dur + target_dur > audio_duration:
                target_dur = audio_duration - current_dur + 0.1
                if target_dur < 1.0:
                    break

            # Create clip from image
            clip_path = os.path.join(READY_FOLDER, f"clip_{idx}_{current_dur:.0f}.mp4")
            result = create_static_image_clip(img_path, target_dur, clip_path)
            if result is None:
                print(f"    > Failed to create clip, skipping")
                continue

            selected_clips.append(clip_path)
            current_dur += target_dur

        if not selected_clips:
            print(f"  > No clips created, skipping paragraph")
            continue

        # Concatenate clips for this paragraph
        if len(selected_clips) == 1:
            chunk_path = selected_clips[0]
        else:
            # Write concat list
            concat_path = os.path.join(READY_FOLDER, f"concat_p{idx}.txt")
            with open(concat_path, "w", encoding="utf-8") as f:
                for clip in selected_clips:
                    safe_path = clip.replace("\\", "/")
                    f.write(f"file '{safe_path}'\n")

            chunk_path = os.path.join(READY_FOLDER, f"chunk_{idx}.mp4")
            result = subprocess.run([
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", concat_path,
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "20",
                "-r", str(TARGET_FPS),
                "-pix_fmt", "yuv420p",
                chunk_path
            ], capture_output=True, text=True)
            if result.returncode != 0:
                stderr_lines = [l for l in result.stderr.split('\n') if l.strip() and not l.startswith('ffmpeg version')]
                error_msg = '\n'.join(stderr_lines[:10])
                print(f"    > Concat error: {error_msg}")
            os.remove(concat_path)

        # Add audio to video chunk
        final_chunk = os.path.join(READY_FOLDER, f"final_chunk_{idx}.mp4")
        result = subprocess.run([
            "ffmpeg", "-y",
            "-i", chunk_path,
            "-i", temp_audio,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-r", str(TARGET_FPS),
            "-c:a", "aac",
            "-b:a", "192k",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
            "-pix_fmt", "yuv420p",
            final_chunk
        ], capture_output=True, text=True)
        if result.returncode != 0:
            stderr_lines = [l for l in result.stderr.split('\n') if l.strip() and not l.startswith('ffmpeg version')]
            error_msg = '\n'.join(stderr_lines[:10])
            print(f"    > Audio merge error: {error_msg}")

        generated_chunks.append(final_chunk)

        # Cleanup temp files (add delay to allow file handles to release)
        time_module.sleep(0.1)
        try:
            if os.path.exists(temp_audio):
                os.remove(temp_audio)
        except PermissionError:
            pass  # File still in use, will be cleaned up later
        try:
            if os.path.exists(chunk_path) and chunk_path != final_chunk:
                os.remove(chunk_path)
        except PermissionError:
            pass
        for clip in selected_clips:
            try:
                if os.path.exists(clip) and clip != final_chunk:
                    os.remove(clip)
            except PermissionError:
                pass

    # Persist the captured per-paragraph timings to a sidecar so the subtitle
    # step can build the .srt without re-calling edge-tts.
    sidecar_path = os.path.join(OUTPUT_FOLDER, "_timings.json")
    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump({
            "voice": VOICE_NAME,
            "paragraphs": timings,
        }, f, ensure_ascii=False, indent=2)
    print(f"\nWrote subtitle sidecar: {sidecar_path} ({len(timings)} paragraphs)")

    # Concatenate all chunks into final video
    print("\n--- Concatenating final video ---")

    master_concat = os.path.join(READY_FOLDER, "master_concat.txt")
    with open(master_concat, "w", encoding="utf-8") as f:
        for chunk in generated_chunks:
            f.write(f"file '{os.path.basename(chunk)}'\n")

    final_output = os.path.join(OUTPUT_FOLDER, f"Video.mp4")

    result = subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", "master_concat.txt",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        final_output
    ], cwd=READY_FOLDER, capture_output=True, text=True)
    if result.returncode != 0:
        stderr_lines = [l for l in result.stderr.split('\n') if l.strip() and not l.startswith('ffmpeg version')]
        error_msg = '\n'.join(stderr_lines[:15])
        print(f"    > Final concat error: {error_msg}")
        print(f"    > Stdout: {result.stdout[:500]}")

    # Cleanup
    if os.path.exists(master_concat):
        os.remove(master_concat)

    # Verify output exists
    if os.path.exists(final_output):
        for chunk in generated_chunks:
            if os.path.exists(chunk):
                os.remove(chunk)
        print(f"\nDONE! Output: {final_output}")
    else:
        print(f"\nERROR: Final video not created!")
        print(f"Check the errors above.")

    return final_output


if __name__ == "__main__":
    final_video = asyncio.run(generate_video())

    if not final_video or not os.path.exists(final_video):
        print("\nAborting pipeline: video generation failed, skipping subtitles.")
        sys.exit(1)

    # # ---- Step 2: generate subtitles.srt from the sidecar (no extra TTS call) ----
    # print("\n--- Generating subtitles ---")
    # srt_path = os.path.join(OUTPUT_FOLDER, "subtitles.srt")
    # sidecar_path = os.path.join(OUTPUT_FOLDER, "_timings.json")
    # if not subtitles_gen.build_subtitles_from_sidecar(sidecar_path, srt_path):
    #     print("\nAborting pipeline: subtitle generation failed.")
    #     sys.exit(1)
    # if not os.path.exists(srt_path):
    #     print(f"\nAborting pipeline: expected subtitle file not found at {srt_path}")
    #     sys.exit(1)

    # # ---- Step 3: burn subtitles into the final video ----

    # # 1. Define the command arguments exactly as a list
    # command = [
    #     sys.executable,               # Safely targets 'python'
    #     "burn_subs.py",               # Your script
    #     os.path.join("output", "Video.mp4"),        # input video
    #     os.path.join("output", "subtitles.srt"),    # subtitles
    #     os.path.join("output", "Video_final.mp4")   # output video
    # ]

    # print("Starting to burn subtitles...")

    # # 2. Run the command and halt if burn_subs.py crashes
    # try:
    #     subprocess.run(command, check=True)
    #     print("Subtitles successfully burned!")
    # except subprocess.CalledProcessError as e:
    #     print(f"Error: burn_subs.py failed with exit code {e.returncode}")

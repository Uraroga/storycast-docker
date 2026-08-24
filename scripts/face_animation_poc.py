#!/usr/bin/env python3
"""POC isolato e leggero per animare un volto con Pillow e FFmpeg."""

from __future__ import annotations

import argparse
import array
import json
import math
from pathlib import Path
import random
import re
import resource
import shutil
import subprocess
import sys
import time
import wave

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps


DEFAULT_IMAGE = Path("assets/characters/personaggio_1/personaggio_1_listening_resting_cheek_brightroom_v1.png")
DEFAULT_AUDIO = Path("work/episodes/il-pulsante-che-fermerebbe-per-sempre-intelligenza-artificiale/audio_segments/0007_personaggio_1.wav")
DEFAULT_OUTPUT = Path("output/tests/face_animation/personaggio_1_face_animation_test.mp4")
DEFAULT_CONFIG = Path("config/face_animation/personaggio_1_listening_resting_cheek_brightroom_v1.json")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Prova isolata di animazione minima del volto (CPU-only).")
    p.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    p.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--duration", type=float, default=8.0, help="Durata massima in secondi (default: 8)")
    p.add_argument("--fps", type=int, default=25)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=360)
    p.add_argument("--seed", type=int, default=9001)
    p.add_argument("--dry-run", action="store_true")
    return p


def wav_info(path: Path) -> tuple[float, int, int, int, bytes]:
    with wave.open(str(path), "rb") as wav:
        channels, width, rate, frames = wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
        if wav.getcomptype() != "NONE" or width not in (1, 2, 3, 4):
            raise ValueError("Il WAV deve essere PCM non compresso a 8, 16, 24 o 32 bit")
        return frames / rate, channels, width, rate, wav.readframes(frames)


def pcm_values(raw: bytes, width: int) -> list[int]:
    if width == 1:
        return [value - 128 for value in raw]
    if width == 2:
        values = array.array("h")
        values.frombytes(raw)
        if sys.byteorder != "little":
            values.byteswap()
        return list(values)
    if width == 4:
        values = array.array("i")
        values.frombytes(raw)
        if sys.byteorder != "little":
            values.byteswap()
        return list(values)
    values = []
    for pos in range(0, len(raw) - 2, 3):
        value = raw[pos] | raw[pos + 1] << 8 | raw[pos + 2] << 16
        values.append(value - (1 << 24) if value & (1 << 23) else value)
    return values


def audio_envelope(raw: bytes, channels: int, width: int, rate: int, fps: int, frames: int) -> list[float]:
    samples = pcm_values(raw, width)
    mono = samples[::channels]
    window = max(1, rate // fps)
    rms = []
    for index in range(frames):
        chunk = mono[index * window : (index + 1) * window]
        rms.append(math.sqrt(sum(v * v for v in chunk) / len(chunk)) if chunk else 0.0)
    nonzero = sorted(value for value in rms if value > 0)
    reference = nonzero[min(len(nonzero) - 1, int(len(nonzero) * 0.90))] if nonzero else 1.0
    noise_floor = reference * 0.055
    levels = []
    for value in rms:
        normalized = max(0.0, min(1.0, (value - noise_floor) / max(reference - noise_floor, 1.0)))
        levels.append(0.0 if normalized < 0.08 else 0.28 if normalized < 0.32 else 0.62 if normalized < 0.67 else 1.0)
    smoothed, current = [], 0.0
    for target in levels:
        factor = 0.42 if target > current else 0.20
        current += (target - current) * factor
        if target == 0.0 and current < 0.025:
            current = 0.0
        smoothed.append(current)
    return smoothed


def load_regions(path: Path) -> dict[str, dict[str, float]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    face = data.get("face", {})
    if set(face) != {"x", "y", "width", "height"}:
        raise ValueError("Regione non valida: face")
    oriented = {"center_x", "center_y", "width", "height", "angle_deg", "feather", "padding"}
    eye_tuning = {"close_amount", "upper_lid_weight", "lower_lid_weight", "preserve_iris_ratio", "preserve_lashes", "asymmetry_bias"}
    for name in ("left_eye", "right_eye", "mouth"):
        region = data.get(name, {})
        expected = oriented | ({"closure_line"} | eye_tuning if name != "mouth" else set())
        if set(region) != expected:
            raise ValueError(f"Regione orientata non valida: {name}")
        for key in ("center_x", "center_y", "width", "height", "feather", "padding"):
            if not 0.0 <= float(region[key]) <= 1.0:
                raise ValueError(f"Valore fuori dall'intervallo 0..1: {name}.{key}")
        if name != "mouth" and not 0.0 <= float(region["closure_line"]) <= 1.0:
            raise ValueError(f"Linea di chiusura non valida: {name}")
        if name != "mouth":
            for key in ("close_amount", "upper_lid_weight", "lower_lid_weight", "preserve_iris_ratio"):
                if not 0.0 <= float(region[key]) <= 1.0:
                    raise ValueError(f"Parametro blink non valido: {name}.{key}")
            if not isinstance(region["preserve_lashes"], bool):
                raise ValueError(f"Parametro booleano non valido: {name}.preserve_lashes")
            if not -1.0 <= float(region["asymmetry_bias"]) <= 1.0:
                raise ValueError(f"Bias asimmetrico non valido: {name}")
        if abs(float(region["angle_deg"])) > 45:
            raise ValueError(f"Angolo eccessivo: {name}")
        half_w = region["width"] * (1 + region["padding"]) / 2
        half_h = region["height"] * (1 + region["padding"]) / 2
        if region["center_x"] - half_w < 0 or region["center_x"] + half_w > 1 or region["center_y"] - half_h < 0 or region["center_y"] + half_h > 1:
            raise ValueError(f"Regione oltre i bordi: {name}")
    if any(not 0.0 <= float(value) <= 1.0 for value in face.values()):
        raise ValueError("Coordinate fuori dall'intervallo 0..1: face")
    return data


def box(region: dict[str, float], size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = size
    x0, y0 = round(region["x"] * width), round(region["y"] * height)
    return x0, y0, round((region["x"] + region["width"]) * width), round((region["y"] + region["height"]) * height)


def subject_name(image: Path) -> str:
    match = re.match(r"(.+?_\d+)_", image.stem)
    return match.group(1) if match else image.stem


def oriented_points(region: dict[str, float], size: tuple[int, int], padded: bool = False) -> list[tuple[float, float]]:
    image_w, image_h = size
    cx, cy = region["center_x"] * image_w, region["center_y"] * image_h
    padding = region["padding"] if padded else 0.0
    half_w = region["width"] * image_w * (1 + padding) / 2
    half_h = region["height"] * image_h * (1 + padding) / 2
    angle = math.radians(region["angle_deg"])
    cosine, sine = math.cos(angle), math.sin(angle)
    points = []
    for x, y in ((-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)):
        points.append((cx + x * cosine + y * sine, cy - x * sine + y * cosine))
    return points


def local_to_image(region: dict[str, float], size: tuple[int, int], x: float, y: float) -> tuple[float, float]:
    image_w, image_h = size
    cx, cy = region["center_x"] * image_w, region["center_y"] * image_h
    angle = math.radians(region["angle_deg"])
    return cx + x * math.cos(angle) + y * math.sin(angle), cy - x * math.sin(angle) + y * math.cos(angle)


def make_oriented_preview(image: Image.Image, regions: dict[str, dict[str, float]], path: Path) -> None:
    preview = image.copy().convert("RGBA")
    overlay = Image.new("RGBA", preview.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    colors = {"left_eye": (0, 255, 102, 255), "right_eye": (255, 210, 0, 255), "mouth": (255, 51, 85, 255)}
    line = max(3, round(preview.width / 500))
    for name in ("left_eye", "right_eye", "mouth"):
        region, color = regions[name], colors[name]
        padded = oriented_points(region, preview.size, padded=True)
        base = oriented_points(region, preview.size)
        draw.polygon(padded, fill=(*color[:3], 35))
        draw.line(base + [base[0]], fill=color, width=line, joint="curve")
        cx, cy = region["center_x"] * preview.width, region["center_y"] * preview.height
        radius = line * 2
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(255, 255, 255, 255), outline=color, width=line)
        half_w = region["width"] * preview.width / 2
        axis_a = local_to_image(region, preview.size, -half_w, 0)
        axis_b = local_to_image(region, preview.size, half_w, 0)
        draw.line((axis_a, axis_b), fill=(0, 220, 255, 255), width=line)
        if name != "mouth":
            local_y = (region["closure_line"] - 0.5) * region["height"] * preview.height
            close_a = local_to_image(region, preview.size, -half_w, local_y)
            close_b = local_to_image(region, preview.size, half_w, local_y)
            draw.line((close_a, close_b), fill=(255, 0, 255, 255), width=line)
        label = f"{name} {region['angle_deg']:+.1f} deg"
        draw.text((cx - half_w, cy - region["height"] * preview.height), label, fill=color, stroke_width=2, stroke_fill="black")
    preview = Image.alpha_composite(preview, overlay).convert("RGB")
    path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(path, format="PNG")


def oriented_mask(size: tuple[int, int], region: dict[str, float], image_size: tuple[int, int]) -> Image.Image:
    patch_w, patch_h = size
    eye_w = max(2, round(region["width"] * image_size[0] * (1 + region["padding"] * 0.55)))
    eye_h = max(2, round(region["height"] * image_size[1] * (1 + region["padding"] * 0.45)))
    cx, cy = patch_w / 2, patch_h / 2
    mask = Image.new("L", size, 0)
    bounds = (cx - eye_w / 2, cy - eye_h / 2, cx + eye_w / 2, cy + eye_h / 2)
    ImageDraw.Draw(mask).ellipse(bounds, fill=255)
    blur = max(1.0, region["feather"] * min(eye_w, eye_h))
    soft = mask.filter(ImageFilter.GaussianBlur(blur))
    core = Image.new("L", size, 0)
    inset = blur * 1.15
    ImageDraw.Draw(core).ellipse((bounds[0] + inset, bounds[1] + inset, bounds[2] - inset, bounds[3] - inset), fill=255)
    return ImageChops.lighter(core, soft)


def oriented_warp(image: Image.Image, region: dict[str, float], amount: float, *, opening: bool = False) -> None:
    if amount <= 0.001:
        return
    image_w, image_h = image.size
    padded_w = region["width"] * image_w * (1 + region["padding"])
    padded_h = region["height"] * image_h * (1 + region["padding"])
    angle = math.radians(region["angle_deg"])
    crop_w = max(4, math.ceil(abs(padded_w * math.cos(angle)) + abs(padded_h * math.sin(angle)) + 6))
    crop_h = max(4, math.ceil(abs(padded_w * math.sin(angle)) + abs(padded_h * math.cos(angle)) + 6))
    cx, cy = region["center_x"] * image_w, region["center_y"] * image_h
    bounds = (round(cx - crop_w / 2), round(cy - crop_h / 2), round(cx + crop_w / 2), round(cy + crop_h / 2))
    patch = image.crop(bounds)
    aligned = patch.rotate(-region["angle_deg"], resample=Image.Resampling.BICUBIC)
    width, height = aligned.size
    if opening:
        scale = 1.0 + 0.105 * amount
        closure_y = height / 2
        inverse = 1.0 / scale
        warped = aligned.transform(
            aligned.size,
            Image.Transform.AFFINE,
            (1.0, 0.0, 0.0, 0.0, inverse, closure_y * (1.0 - inverse)),
            resample=Image.Resampling.BICUBIC,
        )
    else:
        amount = min(1.0, amount * region["close_amount"])
        preserved = region["preserve_iris_ratio"] * (1.0 - amount)
        movement = amount * (1.0 - preserved)
        eye_height = region["height"] * image_h
        closure_y = height / 2 + (region["closure_line"] - 0.5) * eye_height
        eye_top = height / 2 - eye_height / 2
        eye_bottom = height / 2 + eye_height / 2
        upper_anchor = eye_top - eye_height * 0.12
        lower_anchor = eye_bottom + eye_height * 0.08
        warped = aligned.copy()
        band_edges = (0, round(width / 3), round(2 * width / 3), width)
        for band in range(3):
            local_factor = 1.0 + region["asymmetry_bias"] * (band - 1)
            band_movement = max(0.0, min(1.0, movement * local_factor))
            upper_scale = max(0.08, 1.0 - 1.25 * band_movement * region["upper_lid_weight"])
            lower_scale = max(0.08, 1.0 - 1.25 * band_movement * region["lower_lid_weight"])
            upper = aligned.transform(
                aligned.size,
                Image.Transform.AFFINE,
                (1.0, 0.0, 0.0, 0.0, upper_scale, upper_anchor * (1.0 - upper_scale)),
                resample=Image.Resampling.BICUBIC,
            )
            lower = aligned.transform(
                aligned.size,
                Image.Transform.AFFINE,
                (1.0, 0.0, 0.0, 0.0, lower_scale, lower_anchor * (1.0 - lower_scale)),
                resample=Image.Resampling.BICUBIC,
            )
            split = Image.new("L", aligned.size, 0)
            ImageDraw.Draw(split).rectangle((0, 0, width, round(closure_y)), fill=255)
            band_warp = Image.composite(upper, lower, split)
            band_mask = Image.new("L", aligned.size, 0)
            overlap = 2
            ImageDraw.Draw(band_mask).rectangle((max(0, band_edges[band] - overlap), 0, min(width, band_edges[band + 1] + overlap), height), fill=255)
            band_mask = band_mask.filter(ImageFilter.GaussianBlur(1.2))
            warped = Image.composite(band_warp, warped, band_mask)
        closed_strength = 1.0 if amount >= 0.90 else smoothstep((amount - 0.44) / 0.46)
        if closed_strength > 0:
            eye_width = region["width"] * image_w
            closed = warped.copy()
            skin_colors = []
            for sample_y in (round(eye_top - eye_height * 0.12), round(eye_bottom + eye_height * 0.14)):
                pixels = []
                if 0 <= sample_y < height:
                    for sample_x in range(round(width / 2 - eye_width * 0.25), round(width / 2 + eye_width * 0.25)):
                        pixel = aligned.getpixel((max(0, min(width - 1, sample_x)), sample_y))
                        if sum(pixel) > 310 and pixel[0] > pixel[2]:
                            pixels.append(pixel)
                skin_colors.append(tuple(round(sum(pixel[channel] for pixel in pixels) / len(pixels)) for channel in range(3)) if pixels else (230, 145, 78))
            lid_width = eye_width * (1 + region["padding"] * 0.55)
            lid_height = eye_height * (1 + region["padding"] * 0.45)
            lid_box = (width / 2 - lid_width / 2, height / 2 - lid_height / 2, width / 2 + lid_width / 2, height / 2 + lid_height / 2)
            lid_surface = Image.new("RGB", aligned.size, skin_colors[0])
            ImageDraw.Draw(lid_surface).rectangle((0, round(closure_y), width, height), fill=skin_colors[1])
            lid_mask = Image.new("L", aligned.size, 0)
            ImageDraw.Draw(lid_mask).ellipse(lid_box, fill=255)
            lid_blur = max(1.0, region["feather"] * eye_height * 0.55)
            lid_soft = lid_mask.filter(ImageFilter.GaussianBlur(lid_blur))
            lid_core = Image.new("L", aligned.size, 0)
            ImageDraw.Draw(lid_core).ellipse((lid_box[0] + lid_blur, lid_box[1] + lid_blur, lid_box[2] - lid_blur, lid_box[3] - lid_blur), fill=255)
            lid_mask = ImageChops.lighter(lid_core, lid_soft)
            closed = Image.composite(lid_surface, closed, lid_mask)
            closed_draw = ImageDraw.Draw(closed)
            half_line = eye_width * 0.39
            arch = eye_height * 0.09
            points = []
            for step in range(17):
                relative_x = -1.0 + step / 8.0
                bias_y = region["asymmetry_bias"] * eye_height * 0.10 * relative_x
                points.append((width / 2 + relative_x * half_line, closure_y - arch * (1.0 - relative_x * relative_x) + bias_y))
            closed_draw.line(points, fill=(72, 42, 27), width=max(1, round(eye_height * 0.075)), joint="curve")
            blend = Image.new("L", aligned.size, round(255 * closed_strength))
            warped = Image.composite(closed, warped, blend)
        if region["preserve_lashes"] and amount < 0.92:
            gray = aligned.convert("L")
            lash_mask = gray.point(lambda value: max(0, min(255, (105 - value) * 5)))
            upper_only = Image.new("L", aligned.size, 0)
            ImageDraw.Draw(upper_only).rectangle((0, max(0, round(eye_top - eye_height * 0.18)), width, round(closure_y)), fill=round(150 * (1.0 - amount)))
            lash_mask = Image.composite(lash_mask, Image.new("L", aligned.size, 0), upper_only)
            warped = Image.composite(aligned, warped, lash_mask)
    mask = oriented_mask(aligned.size, region, image.size)
    composite = Image.composite(warped, aligned, mask)
    restored = composite.rotate(region["angle_deg"], resample=Image.Resampling.BICUBIC)
    restored_mask = mask.rotate(region["angle_deg"], resample=Image.Resampling.BICUBIC)
    image.paste(restored, bounds[:2], restored_mask)


def smoothstep(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


def blink_schedule(duration: float, fps: int, seed: int, delay: float = 0.0) -> list[float]:
    rng = random.Random(seed)
    values = [0.0] * math.ceil(duration * fps)
    moment = rng.uniform(1.2, 2.2)
    while moment < duration - 0.15:
        starts = [moment]
        if rng.random() < 0.18 and moment + 0.34 < duration:
            starts.append(moment + rng.uniform(0.25, 0.36))
        for start in starts:
            start += delay
            close_end, hold_end, end = start + 0.065, start + 0.090, start + 0.180
            for index in range(max(0, int(start * fps)), min(len(values), math.ceil(end * fps))):
                current = index / fps
                if current < close_end:
                    blink = smoothstep((current - start) / (close_end - start))
                elif current <= hold_end:
                    blink = 1.0
                else:
                    blink = 1.0 - smoothstep((current - hold_end) / (end - hold_end))
                values[index] = max(values[index], blink)
        moment += rng.uniform(2.4, 4.6)
    return values


def blink_schedules(duration: float, fps: int, seed: int) -> dict[str, list[float]]:
    return {
        "left_eye": blink_schedule(duration, fps, seed, delay=0.0),
        "right_eye": blink_schedule(duration, fps, seed, delay=0.014),
    }


def eye_diagnostic_crop(image: Image.Image, region: dict[str, float]) -> Image.Image:
    width, height = image.size
    cx, cy = region["center_x"] * width, region["center_y"] * height
    crop_w, crop_h = region["width"] * width * 3.2, region["height"] * height * 3.0
    crop = image.crop((cx - crop_w / 2, cy - crop_h / 2, cx + crop_w / 2, cy + crop_h / 2))
    return ImageOps.fit(crop, (480, 240), method=Image.Resampling.LANCZOS)


def make_blink_diagnostics(image: Image.Image, regions: dict[str, dict[str, float]], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    phases = (("open", 0.0), ("half", 0.5), ("closed", 1.0))
    eye_crops = {}
    for phase, amount in phases:
        frame = image.copy().convert("RGB")
        oriented_warp(frame, regions["left_eye"], amount)
        oriented_warp(frame, regions["right_eye"], amount)
        frame.save(directory / f"blink_{phase}_v2.png", format="PNG")
        for eye in ("left_eye", "right_eye"):
            isolated = image.copy().convert("RGB")
            oriented_warp(isolated, regions[eye], amount)
            crop = eye_diagnostic_crop(isolated, regions[eye])
            crop.save(directory / f"{eye}_{phase}.png", format="PNG")
            eye_crops[(eye, phase)] = crop
    grid = Image.new("RGB", (1440, 540), "#202020")
    draw = ImageDraw.Draw(grid)
    for row, eye in enumerate(("left_eye", "right_eye")):
        draw.text((8, row * 270 + 5), eye, fill="white")
        for column, (phase, _) in enumerate(phases):
            x, y = column * 480, row * 270 + 30
            grid.paste(eye_crops[(eye, phase)], (x, y))
            draw.text((x + 8, y + 8), phase, fill="white", stroke_width=2, stroke_fill="black")
    grid.save(directory / "blink_comparison_grid.png", format="PNG")


def ffmpeg_command(audio: Path, output: Path, width: int, height: int, fps: int, duration: float) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0", "-i", str(audio), "-t", f"{duration:.3f}",
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output)]


def validate_args(args: argparse.Namespace) -> tuple[float, dict[str, dict[str, float]], tuple[float, int, int, int, bytes]]:
    for label, path in (("immagine", args.image), ("audio", args.audio), ("configurazione", args.config)):
        if not path.is_file():
            raise FileNotFoundError(f"{label.capitalize()} non trovato: {path}")
    if args.duration <= 0 or args.duration > 8:
        raise ValueError("--duration deve essere maggiore di zero e non superiore a 8")
    if args.fps < 1 or args.fps > 60 or args.width < 64 or args.height < 64:
        raise ValueError("FPS o risoluzione non validi")
    wav = wav_info(args.audio)
    duration = min(args.duration, wav[0])
    return duration, load_regions(args.config), wav


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        duration, regions, wav = validate_args(args)
    except (OSError, ValueError, json.JSONDecodeError, wave.Error) as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 2
    command = ffmpeg_command(args.audio, args.output, args.width, args.height, args.fps, duration)
    print(f"Immagine: {args.image}")
    print(f"WAV: {args.audio}")
    print(f"Durata: {duration:.3f} s (WAV: {wav[0]:.3f} s)")
    print(f"Risoluzione: {args.width}x{args.height}")
    print(f"FPS: {args.fps}")
    print(f"Configurazione JSON: {args.config}")
    print(f"Output: {args.output}")
    print("FFmpeg previsto: " + " ".join(command))
    if args.dry_run:
        print("Dry-run: nessun video o file diagnostico generato.")
        return 0
    if shutil.which("ffmpeg") is None:
        print("ERRORE: FFmpeg non disponibile", file=sys.stderr)
        return 3

    source = Image.open(args.image).convert("RGB")
    diagnostic = args.output.parent / f"{subject_name(args.image)}_oriented_regions_preview_v2.png"
    make_oriented_preview(source, regions, diagnostic)
    make_blink_diagnostics(source, regions, args.output.parent)
    frame_count = max(1, round(duration * args.fps))
    envelope = audio_envelope(wav[4], wav[1], wav[2], wav[3], args.fps, frame_count)
    blinks = blink_schedules(duration, args.fps, args.seed)
    overscan_size = (math.ceil(args.width * 1.07), math.ceil(args.height * 1.07))
    base = ImageOps.fit(source, overscan_size, method=Image.Resampling.LANCZOS)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None
    try:
        for index in range(frame_count):
            t = index / args.fps
            local = base.copy()
            for eye in ("left_eye", "right_eye"):
                oriented_warp(local, regions[eye], blinks[eye][index])
            oriented_warp(local, regions["mouth"], envelope[index], opening=True)
            angle = 0.48 * math.sin(t * 0.67 + 0.4)
            local = local.rotate(angle, resample=Image.Resampling.BICUBIC)
            dx = 2.0 * math.sin(t * 0.53 + 1.1)
            dy = 2.3 * math.sin(t * 0.61 + 0.2)
            zoom = 1.0 + 0.004 * (0.5 + 0.5 * math.sin(t * 0.39))
            crop_w, crop_h = args.width / zoom, args.height / zoom
            cx, cy = local.width / 2 + dx, local.height / 2 + dy
            frame = local.crop((cx - crop_w / 2, cy - crop_h / 2, cx + crop_w / 2, cy + crop_h / 2))
            frame = frame.resize((args.width, args.height), Image.Resampling.LANCZOS)
            process.stdin.write(frame.tobytes())
        process.stdin.close()
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        code = process.wait()
    except (BrokenPipeError, OSError) as exc:
        process.kill()
        process.wait()
        print(f"ERRORE durante il rendering: {exc}", file=sys.stderr)
        return 4
    if code:
        print(f"ERRORE FFmpeg ({code}): {stderr.strip()}", file=sys.stderr)
        return code
    elapsed = time.monotonic() - started
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"Anteprima regioni: {diagnostic}")
    print(f"Diagnostica globale v2: {args.output.parent / 'blink_open_v2.png'}, {args.output.parent / 'blink_half_v2.png'}, {args.output.parent / 'blink_closed_v2.png'}")
    print(f"Griglia occhi: {args.output.parent / 'blink_comparison_grid.png'}")
    print(f"Fotogrammi inviati via pipe: {frame_count}; fotogrammi permanenti: 0")
    print(f"Tempo elaborazione: {elapsed:.3f} s; velocità: {duration / elapsed:.3f}x; RSS max Python: {rss_mb:.1f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

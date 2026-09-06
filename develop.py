#!/usr/bin/env python3
"""
summoner engine - decode, analyse, develop and save one photo. No Qt in here; usable from a CLI:

    python develop.py photos/ -o developed --profile Landscape
    python develop.py IMG_0231.dng --no-auto --exposure 50 --shadows 30

Pipeline: decode (rawpy for raw, Pillow for the rest) -> float32 sRGB in [0, 1] -> auto analysis
(white balance, exposure, levels, highlight/shadow recovery) -> profile offsets -> user sliders ->
tone / colour / detail passes -> save (temp name + os.replace, never a partial file).
"""
import argparse
import math
import os
import sys

import cv2
import numpy as np
from PIL import Image, ImageOps

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (ALL_EXTENSIONS, DEFAULT_SETTINGS, IMAGE_EXTENSIONS, OUTPUT_FORMATS,
                    PROFILES, RAW_EXTENSIONS)
from utils.naturalsort import natural_sorted

Image.MAX_IMAGE_PIXELS = None   # a 100-megapixel scan is a photo, not a decompression bomb

SLIDER_KEYS = ("exposure", "contrast", "highlights", "shadows", "temperature", "tint",
               "vibrance", "saturation", "clarity", "sharpen", "denoise", "vignette")
SLIDER_RANGE = {"exposure": (-400, 400), "sharpen": (0, 100), "denoise": (0, 100)}


def is_photo(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in ALL_EXTENSIONS


def collect(paths) -> list:
    """Expand files and folders (recursive) into a naturally sorted, de-duplicated photo list."""
    seen, out = set(), []
    for path in paths:
        if os.path.isdir(path):
            found = []
            for folder, _dirs, files in os.walk(path):
                found.extend(os.path.join(folder, fn) for fn in files if is_photo(fn))
            candidates = natural_sorted(found)
        else:
            candidates = [path] if is_photo(path) else []
        for cand in candidates:
            key = os.path.normcase(os.path.abspath(cand))
            if key not in seen:
                seen.add(key)
                out.append(os.path.abspath(cand))
    return out


# ---------------------------------------------------------------- decode

def decode(path: str):
    """Return (float32 RGB image in [0,1], EXIF bytes or None). Raises on unreadable input."""
    ext = os.path.splitext(path)[1].lower()
    if ext in RAW_EXTENSIONS:
        import rawpy
        with rawpy.imread(path) as raw:
            rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=True, output_bps=16,
                                  user_flip=None)
        return rgb.astype(np.float32) / 65535.0, None
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        exif = im.getexif()
        if 274 in exif:      # orientation is applied above; saying it twice would rotate viewers
            del exif[274]
        exif_bytes = exif.tobytes() if len(exif) else None
        if im.mode in ("I;16", "I;16B", "I;16L"):
            arr = np.asarray(im, dtype=np.float32) / 65535.0
            return np.dstack([arr] * 3), exif_bytes
        im = im.convert("RGB")
        return np.asarray(im, dtype=np.float32) / 255.0, exif_bytes


# ---------------------------------------------------------------- auto analysis

def luminance(img: np.ndarray) -> np.ndarray:
    return img[..., 0] * 0.2126 + img[..., 1] * 0.7152 + img[..., 2] * 0.0722


def analyze(img: np.ndarray, raw: bool = False) -> dict:
    """
    Look at the photo and decide how to develop it. Returns gains and slider-scale amounts that
    render() applies before the user's own sliders. The bar (2026-09-07, measured against camera
    JPEGs from the raws' own embedded previews): clearly better than what the camera would have
    produced, while night scenes and silhouettes keep their darkness. Pass raw=True for a raw
    decode - it has had no tone curve or colour rendering applied, so the baseline adds the
    contrast and colour the camera's JPEG engine would have.
    """
    height, width = img.shape[:2]
    scale = 384 / max(height, width)
    small = cv2.resize(img, (max(1, int(width * scale)), max(1, int(height * scale))),
                       interpolation=cv2.INTER_AREA) if scale < 1 else img
    lum = luminance(small)

    # White balance measured on likely-neutral midtone pixels only. Plain grey-world neutralises
    # whatever dominates the frame - green hills come back magenta, sunsets come back grey - so a
    # cast is corrected only when the scene contains something that was plausibly grey to begin
    # with, and left alone otherwise.
    mask = (lum > 0.08) & (lum < 0.95)
    pixels = (small[mask] if int(mask.sum()) > 256 else small.reshape(-1, 3)).reshape(-1, 3)
    mx = pixels.max(axis=1)
    rel_sat = (mx - pixels.min(axis=1)) / np.maximum(mx, 1e-4)
    neutral = pixels[rel_sat < 0.25]
    if len(neutral) >= 128:
        means = np.clip(neutral.mean(axis=0), 1e-4, None)
        gain_r = float(np.clip((means[1] / means[0]) ** 0.7, 0.8, 1.3))
        gain_b = float(np.clip((means[1] / means[2]) ** 0.7, 0.8, 1.3))
    else:
        gain_r = gain_b = 1.0

    # Low-key detection: a frame that is largely near-black is a night scene or a silhouette,
    # not an underexposure - brightening it to a daylight midtone washes black skies grey and
    # lifts silhouettes the photographer wanted dark (ISS aurora frames, sunset beach, 2026-09-07).
    # Thresholds measured on real photos (2026-09-07): night/silhouette frames have 0.32-0.49 of
    # pixels under 0.06 luminance; normal frames with dark clothing or shadow stay at or below 0.15.
    dark_frac = float((lum < 0.06).mean())
    low_key = float(np.clip((dark_frac - 0.18) / 0.25, 0.0, 1.0))

    # Exposure: push the midtone median toward 0.40 - but never so far that the bright end (p95)
    # would blow out, and much more gently on low-key scenes. The measurement is in gamma-encoded
    # values while render() applies exposure in linear light, where a gamma-space ratio r needs
    # 2.2*log2(r) EV - without that factor every correction lands ~2.2x weaker than intended
    # (the chronic "dimmer than the camera JPEG" bug, 2026-09-07).
    median = float(np.median(lum))
    p95 = float(np.percentile(lum, 95.0))
    ev_want = 2.2 * math.log2(0.40 / max(median, 1e-4)) * 0.9
    ev, deficit = ev_want, 0.0
    if ev > 0.0:
        ev = min(ev, max(2.2 * math.log2(0.92 / max(p95, 1e-4)), 0.0))
        # what the cap refused is the backlit signal: a shaded subject against a bright
        # background (group under a tree, DSC01106) cannot be lifted globally without blowing
        # the background - the remaining lift is assigned to the shadows instead
        deficit = ev_want - ev
    ev *= 1.0 - 0.7 * low_key
    ev = float(np.clip(ev, -3.0, 3.0))

    # Levels: anchor the black point decisively and stretch the top - a raw decode is flat, and
    # timid levels were the biggest visible gap to the camera's own JPEG (washed, milky output).
    # Measured here in post-exposure terms (levels apply after the EV gain in render), else the
    # stretch re-crushes lifted shade and blows what the exposure cap protected. The black cap
    # keeps a bimodal shade-plus-sun frame from having its whole shade half counted as "black".
    gamma_gain = 2.0 ** (ev / 2.2)
    black = min(float(np.percentile(lum, 0.5)) * gamma_gain * 0.8, 0.08)
    white = 1.0 - (1.0 - min(float(np.percentile(lum, 99.5)) * gamma_gain, 1.0)) * 0.7
    white = max(white, black + 0.05)

    # Recovery: the more of the frame is blown or crushed, the more we pull back / lift.
    # On a low-key scene the "crushed" area is the night itself - barely lift it.
    blown = float((lum > 0.97).mean())
    crushed = float((lum < 0.03).mean())
    highlights = -min(60.0, blown * 500.0)
    shadows = min(55.0, crushed * 300.0 + deficit * 30.0) * (1.0 - 0.9 * low_key)

    # A raw decode has no tone curve or colour rendering: give it the punch and colour the
    # camera's JPEG engine applies, scaled back on low-key scenes.
    curve = (16.0 if raw else 0.0) * (1.0 - 0.5 * low_key)
    color = (14.0 if raw else 0.0) * (1.0 - 0.5 * low_key)

    return {"gain_r": gain_r, "gain_b": gain_b, "ev": ev, "black": black, "white": white,
            "highlights": highlights, "shadows": shadows, "curve": curve, "color": color}


NEUTRAL_ANALYSIS = {"gain_r": 1.0, "gain_b": 1.0, "ev": 0.0, "black": 0.0, "white": 1.0,
                    "highlights": 0.0, "shadows": 0.0, "curve": 0.0, "color": 0.0}


# ---------------------------------------------------------------- develop

def effective_settings(settings: dict) -> dict:
    """Merge the selected profile's offsets into the user's sliders, clipped to slider bounds."""
    merged = dict(settings)
    for key, offset in PROFILES.get(settings.get("profile", "Standard"), {}).items():
        low, high = SLIDER_RANGE.get(key, (-100, 100))
        merged[key] = int(np.clip(merged.get(key, 0) + offset, low, high))
    return merged


def _gaussian(img: np.ndarray, sigma: float) -> np.ndarray:
    return cv2.GaussianBlur(img, (0, 0), sigma)


def render(img: np.ndarray, settings: dict, analysis: dict = None) -> np.ndarray:
    """Apply the whole develop pipeline. `analysis` comes from analyze() or NEUTRAL_ANALYSIS."""
    s = effective_settings(settings)
    a = analysis or NEUTRAL_ANALYSIS
    img = img.astype(np.float32, copy=True)

    # --- white balance + exposure, in linear light
    temp, tint = s["temperature"] / 150.0, s["tint"] / 200.0
    gain_r = a["gain_r"] * (2.0 ** temp) * (2.0 ** tint)
    gain_g = 2.0 ** (-tint)
    gain_b = a["gain_b"] * (2.0 ** -temp) * (2.0 ** tint)
    ev = a["ev"] + s["exposure"] / 100.0
    lin = np.clip(img, 0.0, 1.0) ** 2.2
    lin *= np.asarray([gain_r, gain_g, gain_b], dtype=np.float32) * (2.0 ** ev)
    img = np.clip(lin, 0.0, None) ** (1.0 / 2.2)

    # --- levels from the analysis
    if a["black"] > 0.0 or a["white"] < 1.0:
        img = (img - a["black"]) / (a["white"] - a["black"])
    img = np.clip(img, 0.0, 1.0)

    # --- highlight recovery / shadow lift, masked on blurred luminance so edges do not halo hard
    highlights = float(np.clip(a["highlights"] + s["highlights"], -100, 100))
    shadows = float(np.clip(a["shadows"] + s["shadows"], -100, 100))
    if highlights or shadows:
        lum = luminance(img)
        soft = _gaussian(lum, max(2.0, max(img.shape[:2]) / 200.0))
        if highlights:
            mask = np.clip((soft - 0.5) * 2.0, 0.0, 1.0) ** 1.5
            img += (highlights / 100.0) * 0.35 * mask[..., None] * img
        if shadows:
            # gamma-style lift on a blurred-luminance mask: multiplicative, so no additive fog
            # (which washed real photos milky), with true blacks anchored by a smoothstep weight
            # so night skies and silhouettes keep their depth (2026-09-07)
            mask = np.clip(1.0 - soft / 0.6, 0.0, 1.0) ** 1.2
            exponent = (1.0 / (1.0 + (shadows / 100.0) * 1.5 * mask))[..., None]
            lifted = np.power(np.clip(img, 1e-6, 1.0), exponent)
            weight = np.clip(img / 0.15, 0.0, 1.0)
            weight = weight * weight * (3.0 - 2.0 * weight)
            img += (lifted - img) * weight
        img = np.clip(img, 0.0, 1.0)

    # --- contrast: blend toward a smoothstep S-curve (user slider + the analysis baseline)
    contrast = float(np.clip(s["contrast"] + a.get("curve", 0.0), -100.0, 100.0))
    if contrast:
        amount = contrast / 100.0
        curve = img * img * (3.0 - 2.0 * img)
        img = np.clip(img + amount * (curve - img), 0.0, 1.0)

    # --- clarity: wide-radius unsharp on luminance
    if s["clarity"]:
        lum = luminance(img)
        soft = _gaussian(lum, max(3.0, max(img.shape[:2]) / 100.0))
        img = np.clip(img + (s["clarity"] / 100.0) * 0.6 * (lum - soft)[..., None], 0.0, 1.0)

    # --- vibrance / saturation on chroma (user sliders + the analysis colour baseline)
    vibrance = float(np.clip(s["vibrance"] + a.get("color", 0.0), -100.0, 100.0))
    if vibrance or s["saturation"]:
        lum3 = luminance(img)[..., None]
        chroma = img - lum3
        sat_now = np.abs(chroma).max(axis=2, keepdims=True) * 2.0
        factor = (1.0 + s["saturation"] / 100.0
                  + (vibrance / 100.0) * np.clip(1.0 - sat_now, 0.0, 1.0))
        img = np.clip(lum3 + chroma * np.clip(factor, 0.0, None), 0.0, 1.0)

    # --- denoise: edge-preserving bilateral, chroma smoothed harder than luminance
    if s["denoise"]:
        strength = s["denoise"] / 100.0
        img = cv2.bilateralFilter(img, 5, 0.08 * strength + 0.02, 3.0)
        lum3 = luminance(img)[..., None]
        chroma = _gaussian(img - lum3, 1.0 + 3.0 * strength)
        img = np.clip(lum3 + chroma, 0.0, 1.0)

    # --- sharpen: small-radius unsharp mask
    if s["sharpen"]:
        soft = _gaussian(img, 1.0)
        img = np.clip(img + (s["sharpen"] / 100.0) * 1.2 * (img - soft), 0.0, 1.0)

    # --- vignette
    if s["vignette"]:
        height, width = img.shape[:2]
        yy, xx = np.ogrid[:height, :width]
        r2 = (((xx - width / 2.0) / (width / 2.0)) ** 2
              + ((yy - height / 2.0) / (height / 2.0)) ** 2).astype(np.float32) / 2.0
        img = np.clip(img * (1.0 + (s["vignette"] / 100.0) * 0.5 * r2)[..., None], 0.0, 1.0)

    return img


# ---------------------------------------------------------------- save

def destination(source: str, settings: dict) -> str:
    ext = {"JPEG": ".jpg", "PNG": ".png", "TIFF 16-bit": ".tif"}[settings["output_format"]]
    out_dir = settings["output_dir"] or os.path.join(os.path.dirname(source), "developed")
    stem = os.path.splitext(os.path.basename(source))[0]
    dest = os.path.join(out_dir, stem + ext)
    if os.path.normcase(os.path.abspath(dest)) == os.path.normcase(os.path.abspath(source)):
        dest = os.path.join(out_dir, stem + "-developed" + ext)   # never touch the original
    return dest


def save(img: np.ndarray, dest: str, settings: dict, exif: bytes = None) -> str:
    """Write under a temp name, os.replace into place when complete. Returns the final path."""
    long_edge = settings.get("resize_long_edge", 0)
    if long_edge and max(img.shape[:2]) > long_edge:
        scale = long_edge / max(img.shape[:2])
        img = cv2.resize(img, (max(1, round(img.shape[1] * scale)),
                               max(1, round(img.shape[0] * scale))), interpolation=cv2.INTER_AREA)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    try:
        if settings["output_format"] == "TIFF 16-bit":
            # Pillow cannot build an RGB image from a uint16 array; cv2 encodes 16-bit TIFF fine,
            # but picks the format from the file extension, so encode to bytes and write those.
            arr16 = (np.clip(img, 0, 1) * 65535.0 + 0.5).astype(np.uint16)
            ok, blob = cv2.imencode(".tif", cv2.cvtColor(arr16, cv2.COLOR_RGB2BGR))
            if not ok:
                raise RuntimeError("TIFF encoding failed")
            with open(tmp, "wb") as fh:
                fh.write(blob.tobytes())
        else:
            im = Image.fromarray((np.clip(img, 0, 1) * 255.0 + 0.5).astype(np.uint8), mode="RGB")
            if settings["output_format"] == "JPEG":
                kwargs = {"format": "JPEG", "quality": int(settings["jpeg_quality"]),
                          "subsampling": 0 if settings["jpeg_quality"] >= 90 else 2}
                if exif:
                    kwargs["exif"] = exif
                im.save(tmp, **kwargs)
            else:
                im.save(tmp, format="PNG")
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return dest


def develop_file(source: str, settings: dict, progress=None) -> str:
    """Decode -> analyse -> render -> save one photo. Returns the output path."""
    def report(phase):
        if progress:
            progress(phase)
    report("decode")
    img, exif = decode(source)
    report("develop")
    raw = os.path.splitext(source)[1].lower() in RAW_EXTENSIONS
    analysis = analyze(img, raw=raw) if settings.get("auto_develop", True) else NEUTRAL_ANALYSIS
    img = render(img, settings, analysis)
    report("save")
    return save(img, destination(source, settings), settings, exif)


# ---------------------------------------------------------------- CLI

def main(argv=None):
    parser = argparse.ArgumentParser(description="Develop digital photos (raw or bitmap).")
    parser.add_argument("inputs", nargs="+", help="photo files and/or folders (recursive)")
    parser.add_argument("-o", "--output", default="", help="output folder "
                        "(default: a 'developed' folder next to each photo)")
    parser.add_argument("--profile", default="Standard", choices=sorted(PROFILES))
    parser.add_argument("--no-auto", action="store_true", help="skip the per-photo auto analysis")
    parser.add_argument("--format", default="JPEG", choices=OUTPUT_FORMATS)
    parser.add_argument("--quality", type=int, default=DEFAULT_SETTINGS["jpeg_quality"])
    parser.add_argument("--resize", type=int, default=0, metavar="PX", help="long edge in pixels")
    for key in SLIDER_KEYS:
        parser.add_argument(f"--{key}", type=int, default=DEFAULT_SETTINGS[key])
    args = parser.parse_args(argv)

    settings = dict(DEFAULT_SETTINGS)
    settings.update(auto_develop=not args.no_auto, profile=args.profile,
                    output_format=args.format, jpeg_quality=args.quality,
                    resize_long_edge=args.resize, output_dir=args.output,
                    **{key: getattr(args, key) for key in SLIDER_KEYS})
    photos = collect(args.inputs)
    if not photos:
        parser.error("no photos found in the given paths")
    failed = 0
    for n, path in enumerate(photos, 1):
        try:
            out = develop_file(path, settings)
            print(f"[{n}/{len(photos)}] {os.path.basename(path)} -> {out}", flush=True)
        except Exception as exc:                                    # degrade per item, keep going
            failed += 1
            print(f"[{n}/{len(photos)}] {os.path.basename(path)} FAILED: {exc}", flush=True)
    return 1 if failed == len(photos) else 0


if __name__ == "__main__":
    sys.exit(main())

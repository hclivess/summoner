"""Pure-logic tests for the develop engine: queueing, destinations, settings merge, rendering."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import develop
from config import DEFAULT_SETTINGS


def _photo(width=320, height=200, value=0.5):
    img = np.full((height, width, 3), value, np.float32)
    img[height // 2:, :] *= 0.4
    return img


def test_collect_natural_order_and_dedupe(tmp_path):
    for name in ("img10.jpg", "img2.jpg", "notaphoto.txt"):
        (tmp_path / name).write_bytes(b"x")
    once = develop.collect([str(tmp_path)])
    assert [os.path.basename(p) for p in once] == ["img2.jpg", "img10.jpg"]
    twice = develop.collect([str(tmp_path), str(tmp_path / "img2.jpg")])
    assert len(twice) == 2


def test_destination_never_overwrites_source(tmp_path):
    source = str(tmp_path / "photo.jpg")
    settings = {**DEFAULT_SETTINGS, "output_dir": str(tmp_path), "output_format": "JPEG"}
    dest = develop.destination(source, settings)
    assert os.path.normcase(dest) != os.path.normcase(source)
    assert "-developed" in os.path.basename(dest)


def test_destination_default_folder(tmp_path):
    source = str(tmp_path / "photo.nef")
    dest = develop.destination(source, dict(DEFAULT_SETTINGS))
    assert os.path.dirname(dest) == str(tmp_path / "developed")
    assert dest.endswith("photo.jpg")


def test_effective_settings_clips_to_bounds():
    merged = develop.effective_settings({**DEFAULT_SETTINGS,
                                         "profile": "Black & white", "saturation": -50})
    assert merged["saturation"] == -100


def test_render_neutral_is_identity():
    img = _photo()
    settings = {**DEFAULT_SETTINGS, "auto_develop": False, "sharpen": 0}
    out = develop.render(img, settings, develop.NEUTRAL_ANALYSIS)
    assert np.abs(out - img).max() < 1e-3


def test_auto_brightens_underexposed():
    img = _photo(value=0.15)
    analysis = develop.analyze(img)
    out = develop.render(img, {**DEFAULT_SETTINGS, "sharpen": 0}, analysis)
    assert float(np.median(develop.luminance(out))) > float(np.median(develop.luminance(img)))


def test_black_and_white_has_no_chroma():
    img = _photo()
    img[..., 0] *= 1.4
    settings = {**DEFAULT_SETTINGS, "auto_develop": False, "profile": "Black & white", "sharpen": 0}
    out = develop.render(img, settings, develop.NEUTRAL_ANALYSIS)
    assert float(np.abs(out.max(axis=2) - out.min(axis=2)).max()) < 0.02


def test_auto_leaves_night_scene_dark():
    """A frame that is mostly near-black is a night scene, not an underexposure (ISS aurora case)."""
    rng = np.random.default_rng(1)
    img = rng.uniform(0.0, 0.04, (200, 320, 3)).astype(np.float32)
    img[40:70, 100:220] = [0.1, 0.55, 0.2]                       # the aurora
    analysis = develop.analyze(img)
    assert analysis["shadows"] < 10
    out = develop.render(img, {**DEFAULT_SETTINGS, "sharpen": 0}, analysis)
    assert float(np.median(develop.luminance(out))) < 0.15       # the night stays night


def test_auto_never_blows_highlights_to_brighten():
    """Sunset-silhouette case: dark median but a bright sky - exposure must not push the sky over."""
    img = np.full((200, 320, 3), 0.08, np.float32)               # silhouette foreground
    img[:80] = [0.9, 0.6, 0.3]                                   # bright sunset sky
    analysis = develop.analyze(img)
    out = develop.render(img, {**DEFAULT_SETTINGS, "sharpen": 0}, analysis)
    assert float((develop.luminance(out) > 0.97).mean()) < 0.02


def test_auto_lifts_backlit_shade_without_blowing_background():
    """DSC01106 case: a shaded subject against a bright background. Global exposure is capped by
    the bright half, so the lift must reach the subject through the shadows instead."""
    img = np.full((200, 320, 3), 0.18, np.float32)               # subject in shade
    img[:, 200:] = 0.78                                          # sunlit background
    analysis = develop.analyze(img)
    assert analysis["shadows"] > 15
    out = develop.render(img, {**DEFAULT_SETTINGS, "sharpen": 0}, analysis)
    shade = float(np.median(develop.luminance(out[:, :180])))
    bright = float(np.median(develop.luminance(out[:, 210:])))
    assert shade > 0.26                                          # subject clearly lifted
    assert bright < 0.98                                         # background not blown


def test_auto_lifts_backlit_minority_subject():
    """DSC01114 case: a small dark subject against a dominant bright sky. The median looks healthy,
    so exposure and its deficit do nothing - the shade fraction itself must drive the lift."""
    img = np.full((200, 320, 3), 0.62, np.float32)               # dominant bright sky
    img[120:, 60:260] = 0.16                                     # the face in shadow
    analysis = develop.analyze(img)
    out = develop.render(img, {**DEFAULT_SETTINGS, "sharpen": 0}, analysis)
    subject = float(np.median(develop.luminance(out[140:, 80:240])))
    assert subject > 0.20                                        # clearly lifted from 0.16
    assert float((develop.luminance(out) > 0.97).mean()) < 0.02  # sky not blown


def test_auto_wb_skips_scene_without_neutrals():
    """Grey-world on a green-dominated frame would go magenta; without neutral pixels, no WB."""
    img = np.tile(np.asarray([0.16, 0.40, 0.12], np.float32), (200, 320, 1))
    analysis = develop.analyze(img)
    assert analysis["gain_r"] == 1.0 and analysis["gain_b"] == 1.0


@pytest.mark.parametrize("fmt,ext", [("JPEG", ".jpg"), ("PNG", ".png"), ("TIFF 16-bit", ".tif")])
def test_save_formats_and_no_partial_left(tmp_path, fmt, ext):
    dest = str(tmp_path / ("out" + ext))
    settings = {**DEFAULT_SETTINGS, "output_format": fmt}
    develop.save(_photo(), dest, settings)
    assert os.path.getsize(dest) > 0
    assert not [fn for fn in os.listdir(tmp_path) if fn.endswith(".part")]


def test_save_resizes_long_edge(tmp_path):
    from PIL import Image
    dest = str(tmp_path / "small.jpg")
    develop.save(_photo(800, 500), dest, {**DEFAULT_SETTINGS, "resize_long_edge": 400})
    with Image.open(dest) as im:
        assert max(im.size) == 400

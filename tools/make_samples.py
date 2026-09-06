#!/usr/bin/env python3
"""
Generate synthetic sample photographs for the self-test and the screenshot: invented scenes with
the faults the auto pass exists to fix (underexposure, a colour cast, blown highlights, noise).
No user material, ever - see STANDARD.md 11.

    python tools/make_samples.py <output-folder>
"""
import os
import sys

import numpy as np
from PIL import Image


def _scene(width=1600, height=1067, seed=0):
    """A believable landscape: sky gradient, sun disc, hills, foreground texture."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    img = np.zeros((height, width, 3), np.float32)
    sky = 1.0 - yy / height
    img[..., 0] = 0.55 + 0.35 * sky
    img[..., 1] = 0.45 + 0.30 * sky
    img[..., 2] = 0.40 + 0.20 * sky
    sun = np.exp(-(((xx - width * 0.72) ** 2 + (yy - height * 0.25) ** 2)
                   / (2 * (width * 0.04) ** 2)))
    img += sun[..., None] * np.asarray([0.9, 0.8, 0.5], np.float32)
    horizon = height * (0.55 + 0.08 * np.sin(xx[0] / width * 9.0 + seed))
    hills = yy > horizon[None, :]
    img[hills] = np.asarray([0.16, 0.22, 0.12], np.float32)
    ground = yy > height * 0.8
    img[ground] *= (0.7 + 0.3 * rng.random((int(ground.sum()),))[:, None]).astype(np.float32)
    img += rng.normal(0, 0.015, img.shape).astype(np.float32)
    return np.clip(img, 0.0, 1.0)


def write_samples(out_dir: str):
    os.makedirs(out_dir, exist_ok=True)
    written = []

    def save(name, img, **kwargs):
        path = os.path.join(out_dir, name)
        Image.fromarray((np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)).save(path, **kwargs)
        written.append(path)

    save("IMG_0231.jpg", _scene(seed=1) * 0.35, quality=90)                       # underexposed
    save("IMG_0232.jpg", np.clip(_scene(seed=2) * 1.8, 0, 1), quality=90)         # blown highlights
    cast = _scene(seed=3) * np.asarray([0.75, 0.95, 1.25], np.float32)            # blue cast
    save("harbor-morning.png", cast)
    noisy = _scene(640, 427, seed=4) * 0.5
    noisy += np.random.default_rng(5).normal(0, 0.08, noisy.shape).astype(np.float32)
    tif16 = (np.clip(noisy, 0, 1) * 65535 + 0.5).astype(np.uint16)
    path = os.path.join(out_dir, "lowlight-alley.tif")
    try:
        import cv2
        cv2.imwrite(path, cv2.cvtColor(tif16, cv2.COLOR_RGB2BGR))
        written.append(path)
    except ImportError:
        save("lowlight-alley.png", noisy)                                          # 8-bit fallback
    return written


if __name__ == "__main__":
    for sample in write_samples(sys.argv[1] if len(sys.argv) > 1 else "samples"):
        print(sample)

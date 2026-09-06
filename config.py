"""
summoner - configuration and constants (the only place APP_NAME / APP_VERSION live)
"""

APP_NAME = "summoner"       # lowercase, hyphenated; also the binary / archive name
APP_VERSION = "1.0"         # bump here, then tag v<APP_VERSION>
WINDOW_MIN_WIDTH = 820
WINDOW_MIN_HEIGHT = 560

REPO_URL = "https://github.com/hclivess/summoner"

# Raw formats rawpy/libraw decodes; everything else photographic goes through Pillow.
RAW_EXTENSIONS = {".arw", ".cr2", ".cr3", ".dng", ".erf", ".kdc", ".mrw", ".nef", ".nrw",
                  ".orf", ".pef", ".raf", ".rw2", ".sr2", ".srw", ".x3f"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
ALL_EXTENSIONS = RAW_EXTENSIONS | IMAGE_EXTENSIONS

OUTPUT_FORMATS = ("JPEG", "PNG", "TIFF 16-bit")

# Develop profiles: slider offsets applied on top of the auto analysis (or of neutral, with auto off).
# "Black & white" is saturation -100: the chroma path collapses to zero, no special-case code.
PROFILES = {
    "Standard": {},
    "Portrait": {"contrast": -8, "temperature": 6, "vibrance": 8, "clarity": -10, "sharpen": -5},
    "Landscape": {"contrast": 10, "vibrance": 20, "clarity": 15, "saturation": 5},
    "Vivid": {"contrast": 15, "saturation": 25, "clarity": 10},
    "Night": {"shadows": 30, "denoise": 35, "temperature": -6, "contrast": 5},
    "Black & white": {"saturation": -100, "contrast": 12, "clarity": 10},
}

DEFAULT_SETTINGS = {
    # every GUI control has a key here; presets and QSettings persist exactly this dict
    "auto_develop": True,
    "profile": "Standard",
    "exposure": 0,        # -400..400, 1/100 EV
    "contrast": 0,        # -100..100
    "highlights": 0,      # -100..100, negative recovers blown skies
    "shadows": 0,         # -100..100, positive opens dark areas
    "temperature": 0,     # -100 blue .. 100 amber
    "tint": 0,            # -100 green .. 100 magenta
    "vibrance": 0,        # -100..100, saturates muted colours first
    "saturation": 0,      # -100..100
    "clarity": 0,         # -100..100, local midtone contrast
    "sharpen": 25,        # 0..100
    "denoise": 0,         # 0..100
    "vignette": 0,        # -100 darkens corners .. 100 brightens them
    "output_format": "JPEG",
    "jpeg_quality": 92,   # 1..100
    "resize_long_edge": 0,  # pixels, 0 = keep original size
    "output_dir": "",     # empty = a "developed" folder next to each photo
}

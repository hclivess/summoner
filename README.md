# summoner

Drop photos in, press **Start**, get developed photos — raw or JPEG, no catalogue, no import step.

![summoner](thumb.png)

## What it does

1. Decodes anything photographic: camera raw (CR2/CR3, NEF, ARW, DNG, ORF, RW2, RAF and friends via
   libraw) and JPEG/PNG/TIFF/WebP/BMP via Pillow. Nothing downloads; everything ships in the archive.
2. **Auto develop** measures every photo separately — white balance from the pixels that were
   plausibly grey, exposure toward a healthy midtone, levels, blown-highlight and crushed-shadow
   recovery — all damped so the result reads as "obviously better", never "obviously processed".
3. A **profile** (Standard, Portrait, Landscape, Vivid, Night, Black & white) shapes the look on top,
   and every slider adjusts on top of that, so 0 means "trust the analysis".
4. Saves JPEG (EXIF kept when the source had it), PNG, or 16-bit TIFF — written under a temporary
   name and moved into place only when complete. Originals are never touched.

```
your-photos/
  IMG_0231.CR2
  IMG_0232.CR2
  developed/          <- appears next to your photos (or wherever you point Output folder)
    IMG_0231.jpg
    IMG_0232.jpg
```

## Install

Grab a prebuilt binary from the [latest release](https://github.com/hclivess/summoner/releases/latest)
(Windows / Linux / macOS, no Python needed), or run from source:

```
pip install -r requirements.txt
python main.py            # GUI   (run.cmd / run.sh do the same)
```

No external tools required.

## Settings

| Tab | |
|---|---|
| **Develop** | Auto develop on/off, profile, then Light (exposure, contrast, highlights, shadows), Colour (temperature, tint, vibrance, saturation) and Detail (clarity, sharpen, denoise, vignette) |
| **Output** | Format (JPEG / PNG / TIFF 16-bit), JPEG quality, resize long edge, output folder |

**Presets** menu: Web JPEG (2048 px), Archive TIFF 16-bit, Quick black & white built in;
save / load / delete / import / export, *save current as defaults*, *reset*.

## CLI

```
python develop.py photos/ -o developed --profile Landscape
python develop.py IMG_0231.dng --no-auto --exposure 50 --shadows 30 --format "TIFF 16-bit"
```

## Tips

- Sky went grey in a sunset → auto white balance only corrects when it finds neutral pixels, so this
  should not happen; if a look is off, lower **Temperature** yourself or switch auto off.
- Night shots look smeared → lower **Denoise**; it trades detail for smoothness.
- Faces look crunchy → the **Portrait** profile softens clarity and sharpening.
- Output too large for mail → the **Web JPEG (2048 px)** preset.
- A photo failed → the queue row turns red with the reason; the rest of the queue still develops.

## Windows says the app is not safe

The Windows build is not code-signed, so SmartScreen shows *"Windows protected your PC — unknown publisher"* the
first time you run it. Nothing is wrong with the file; an unsigned executable from a small project has no
reputation with Microsoft.

- **To run it:** *More info* → *Run anyway*. From a downloaded `.zip` Windows also adds the mark-of-the-web:
  `Unblock-File .\summoner\*` in PowerShell clears it.
- **To verify the download:** every release ships a `.sha256` next to the archive; `Get-FileHash <archive>` must
  print the same digest.
- **If Defender quarantines it** rather than warning, that is a false positive on the PyInstaller runtime — report
  it at <https://www.microsoft.com/wdsi/filesubmission> and open an issue.

## Build

`pip install -r requirements.txt pyinstaller && python build.py` produces `dist/summoner-<version>-<os>-<arch>`.
The GitHub workflow builds all three platforms on every tag and attaches them to the release; the Linux build runs a
headless self-test (`SUMMONER_SELFTEST=<folder of photos>`) on the frozen binary against generated sample scenes.

## Changes in 1.0

First release, built to the [hclivess house standard](https://github.com/hclivess/beautiful-software).

- Auto develop with per-photo analysis, six profiles, twelve manual sliders, live before/after preview.
- Raw via libraw/rawpy, bitmap via Pillow; JPEG / PNG / 16-bit TIFF out; EXIF preserved for JPEG sources.
- No catalogue and no database: a drag-and-drop queue, and a `developed/` folder next to your photos.

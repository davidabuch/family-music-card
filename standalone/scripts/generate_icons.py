"""Crop approved artwork to a square, then resize; never paint or redesign it.

Requires Pillow 11.3.0. --check verifies source provenance and decoded pixels.
"""

import hashlib
import io
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "artwork" / "approved-icon.png"
WEB = ROOT / "web" if (ROOT / "web").is_dir() else ROOT / "public"
DEST = WEB / "icons" / "approved-v2"
SIZES = (16, 32, 180, 192, 512)


def composition():
    metadata = json.loads((ROOT / "artwork" / "provenance.json").read_text())
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == metadata["sha256"]
    source = Image.open(SOURCE).convert("RGBA")
    assert source.size == tuple(metadata["dimensions"])
    recipe = json.loads((ROOT / "artwork" / "composition.json").read_text())
    left, top, right, bottom = recipe["crop_box"]
    assert 0 <= left < right <= source.width and 0 <= top < bottom <= source.height
    assert right - left == bottom - top, "Crop must be square: no distortion"
    return source.crop((left, top, right, bottom))


def verify_full_bleed(image):
    """Reject padding/frames, while allowing isolated white artistic highlights."""
    image = image.convert("RGBA")
    assert image.getextrema()[3] == (255, 255), "Transparent/pre-rounded corners"
    width, height = image.size
    edges = [[image.getpixel((x, y)) for x in range(width)] for y in (0, height - 1)]
    edges += [[image.getpixel((x, y)) for y in range(height)] for x in (0, width - 1)]
    for edge in edges:
        assert sum(min(p[:3]) >= 238 for p in edge) / len(edge) < 0.9, "White frame"
        assert sum(max(p[:3]) <= 8 for p in edge) / len(edge) < 0.9, "Black padding"
    # Original surrounds are visible at the corners even when edges contain artwork.
    side = max(1, min(width, height) // 30)
    for x, y in ((0, 0), (width - side, 0), (0, height - side), (width - side, height - side)):
        pixels = list(image.crop((x, y, x + side, y + side)).getdata())
        assert sum(min(p[:3]) >= 238 for p in pixels) / len(pixels) < 0.9, "White corner"
        assert sum(max(p[:3]) <= 8 for p in pixels) / len(pixels) < 0.9, "Rounded black corner"


def generated():
    source = composition()
    verify_full_bleed(source)
    for size in SIZES:
        output = io.BytesIO()
        resized = source.resize((size, size), Image.Resampling.LANCZOS)
        verify_full_bleed(resized)
        resized.save(output, "PNG")
        yield f"icon-{size}.png", output.getvalue()
    output = io.BytesIO()
    source.save(output, "ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    yield "favicon.ico", output.getvalue()


def main():
    checking = "--check" in sys.argv
    DEST.mkdir(parents=True, exist_ok=True)
    for name, content in generated():
        target = DEST / name
        if checking:
            # Compression differs across platforms; image dimensions/pixels cannot.
            with Image.open(target) as actual, Image.open(io.BytesIO(content)) as expected:
                assert actual.format == expected.format, target
                if actual.format == "ICO":
                    sizes = {(16, 16), (32, 32), (48, 48)}
                    assert actual.info["sizes"] == sizes, target
                    for size in sizes:
                        frame = actual.ico.getimage(size).convert("RGBA")
                        verify_full_bleed(frame)
                        assert (
                            frame.tobytes() == expected.ico.getimage(size).convert("RGBA").tobytes()
                        )
                else:
                    verify_full_bleed(actual)
                    assert actual.size == expected.size, target
                    assert actual.convert("RGBA").tobytes() == expected.convert("RGBA").tobytes(), (
                        target
                    )
        else:
            target.write_bytes(content)
    print(
        "Approved source and six full-bleed derivatives verified" if checking else "Generated icons"
    )


if __name__ == "__main__":
    main()

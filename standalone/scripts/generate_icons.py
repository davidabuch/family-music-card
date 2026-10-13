"""Resize the approved source without cropping, compositing or redesigning it.

Requires Pillow 11.3.0. Run with --check to verify committed derivatives.
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
DEST = WEB / "icons" / "approved-v1"
SIZES = (16, 32, 180, 192, 512)


def generated():
    source = Image.open(SOURCE)
    assert source.size == (1254, 1254), "Unexpected approved source dimensions"
    for size in SIZES:
        output = io.BytesIO()
        source.resize((size, size), Image.Resampling.LANCZOS).save(output, "PNG")
        yield f"icon-{size}.png", output.getvalue()
    output = io.BytesIO()
    source.save(output, "ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    yield "favicon.ico", output.getvalue()


def main():
    checking = "--check" in sys.argv
    metadata = json.loads((ROOT / "artwork" / "provenance.json").read_text())
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == metadata["sha256"]
    DEST.mkdir(parents=True, exist_ok=True)
    for name, content in generated():
        target = DEST / name
        if checking:
            # PNG compression can differ across platforms; decoded pixels cannot.
            with Image.open(target) as actual, Image.open(io.BytesIO(content)) as expected:
                assert actual.format == expected.format, f"Format differs: {target}"
                if actual.format == "ICO":
                    sizes = {(16, 16), (32, 32), (48, 48)}
                    assert actual.info["sizes"] == sizes, f"ICO sizes differ: {target}"
                    for size in sizes:
                        assert (
                            actual.ico.getimage(size).convert("RGBA").tobytes()
                            == expected.ico.getimage(size).convert("RGBA").tobytes()
                        ), target
                else:
                    assert actual.size == expected.size, f"Dimensions differ: {target}"
                    assert actual.convert("RGBA").tobytes() == expected.convert("RGBA").tobytes(), (
                        f"Pixels differ: {target}"
                    )
        else:
            target.write_bytes(content)
    print(
        "Approved source and all six icon derivatives verified"
        if checking
        else "Generated six icons"
    )


if __name__ == "__main__":
    main()

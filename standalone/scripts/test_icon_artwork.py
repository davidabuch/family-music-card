"""Pixel-level icon regressions: originals, crop, dimensions, frames and padding."""

import hashlib
import importlib.util
import io
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("icon_generator", ROOT / "scripts/generate_icons.py")
icons = importlib.util.module_from_spec(spec)
spec.loader.exec_module(icons)
EXPECTED_SOURCE_SHA256 = "1d83b23b6977c600bc7c6efa1cafff6808ca04fd2c118cff1ac41f0f548a9a06"


class ArtworkTests(unittest.TestCase):
    def test_original_is_unchanged(self):
        self.assertEqual(
            hashlib.sha256(icons.SOURCE.read_bytes()).hexdigest(), EXPECTED_SOURCE_SHA256
        )

    def test_all_dimensions_and_pixels_use_one_full_bleed_crop(self):
        for name, generated in icons.generated():
            with self.subTest(name=name):
                with (
                    Image.open(icons.DEST / name) as actual,
                    Image.open(io.BytesIO(generated)) as expected,
                ):
                    if name.endswith(".ico"):
                        self.assertEqual(actual.info["sizes"], {(16, 16), (32, 32), (48, 48)})
                        for size in actual.info["sizes"]:
                            frame = actual.ico.getimage(size).convert("RGBA")
                            self.assertEqual(
                                frame.tobytes(),
                                expected.ico.getimage(size).convert("RGBA").tobytes(),
                            )
                            icons.verify_full_bleed(frame)
                    else:
                        size = int(name.split("-")[1].split(".")[0])
                        self.assertEqual(actual.size, (size, size))
                        self.assertEqual(
                            actual.convert("RGBA").tobytes(), expected.convert("RGBA").tobytes()
                        )
                        icons.verify_full_bleed(actual)

    def test_continuous_white_frame_and_black_padding_are_rejected(self):
        for surround in ("white", "black"):
            image = Image.new("RGBA", (180, 180), surround)
            image.paste(Image.new("RGBA", (160, 160), "navy"), (10, 10))
            with self.subTest(surround=surround), self.assertRaises(AssertionError):
                icons.verify_full_bleed(image)

    def test_white_highlights_are_allowed_but_rounded_transparency_is_rejected(self):
        image = Image.new("RGBA", (180, 180), "navy")
        image.paste(Image.new("RGBA", (35, 35), "white"), (65, 65))
        icons.verify_full_bleed(image)
        image.putpixel((0, 0), (0, 0, 0, 0))
        with self.assertRaises(AssertionError):
            icons.verify_full_bleed(image)


if __name__ == "__main__":
    unittest.main()

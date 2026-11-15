"""The logo files: the .ico is well formed and multi-size, the window icons exist, both static copies agree.

    python3 -m unittest discover -s tests_pure -t .
"""
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
WEB_FILES = ["logo.png", "logo-mark.png", "receipt-logo.png", "favicon.ico", "apple-touch-icon.png"] + [f"icons/icon-{s}.png" for s in ICO_SIZES]


def ico_sizes(path):
    data = Path(path).read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1), "not an icon file"
    sizes = []
    for i in range(count):
        w, h, _c, _r, _planes, bits, length, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
        assert bits == 32 and offset + length <= len(data), "icon entry is damaged"
        sizes.append((w or 256, h or 256))
    return sizes


class LogoAssetTests(unittest.TestCase):
    def test_exe_icon_has_every_windows_size(self):
        self.assertEqual(ico_sizes(ROOT / "packaging" / "winespos.ico"), [(s, s) for s in ICO_SIZES])

    def test_favicon_is_multi_size(self):
        self.assertEqual(ico_sizes(ROOT / "core" / "static" / "core" / "favicon.ico"), [(16, 16), (32, 32), (48, 48)])

    def test_installer_pictures_have_the_sizes_inno_setup_expects(self):
        from PIL import Image
        self.assertEqual(Image.open(ROOT / "packaging" / "wizard_large.bmp").size, (164, 314))
        self.assertEqual(Image.open(ROOT / "packaging" / "wizard_small.bmp").size, (55, 55))

    def test_served_copy_matches_the_source_copy(self):
        for name in WEB_FILES:
            source, served = ROOT / "core" / "static" / "core" / name, ROOT / "staticfiles" / "core" / name
            self.assertTrue(source.is_file(), f"missing {source}")
            self.assertEqual(source.read_bytes(), served.read_bytes(), f"{name}: staticfiles copy is out of date")

    def test_logo_is_transparent_and_tightly_cropped(self):
        from PIL import Image
        logo = Image.open(ROOT / "core" / "static" / "core" / "logo.png")
        self.assertEqual(logo.mode, "RGBA")
        self.assertEqual(logo.getpixel((0, 0))[3], 0)                 # corners are see-through
        self.assertLess(logo.width, 600)                               # not the loose 1408x768 canvas

    def test_launcher_finds_all_window_icons(self):
        from desktop.launcher import ICON_SIZES, icon_files
        self.assertEqual([p.name for p in icon_files()], [f"icon-{s}.png" for s in ICON_SIZES])

    def test_installer_script_uses_the_logo(self):
        iss = (ROOT / "packaging" / "WinesPOS.iss").read_text()
        for needle in ("SetupIconFile=winespos.ico", "WizardImageFile=wizard_large.bmp", "WizardSmallImageFile=wizard_small.bmp"):
            self.assertIn(needle, iss)
        self.assertEqual(iss.count('IconFilename: "{app}\\winespos.ico"'), 6)   # all six shortcuts


if __name__ == "__main__":
    unittest.main()

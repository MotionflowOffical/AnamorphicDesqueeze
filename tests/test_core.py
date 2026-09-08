import unittest
from pathlib import Path

from dng_core import (
    DngInfo, build_jobs, parse_pair, infer_raw_ifd_group, parse_size_for_group,
    _exiftool_windows_download_urls, _exiftool_version_urls
)


class OrientationTests(unittest.TestCase):
    def test_landscape_orientation_1_stretches_raw_x(self):
        d = DngInfo(Path("a.dng"), 6000, 4000, 1)
        self.assertEqual(d.display_orientation, "landscape")
        self.assertEqual(d.raw_axis_for_display_stretch(), "x")
        self.assertEqual(d.target_default_scale(1.33), (1.33, 1.0))

    def test_portrait_via_exif_rotation_maps_vertical_display_to_raw_x(self):
        d = DngInfo(Path("a.dng"), 6000, 4000, 6)
        self.assertEqual(d.display_orientation, "portrait")
        self.assertEqual(d.raw_axis_for_display_stretch(), "x")
        self.assertEqual(d.target_default_scale(2.0), (2.0, 1.0))

    def test_native_portrait_orientation_1_stretches_raw_y(self):
        d = DngInfo(Path("a.dng"), 4000, 6000, 1)
        self.assertEqual(d.display_orientation, "portrait")
        self.assertEqual(d.raw_axis_for_display_stretch(), "y")
        self.assertEqual(d.target_default_scale(1.5), (1.0, 1.5))

    def test_landscape_after_rotation_maps_display_x_to_raw_y(self):
        d = DngInfo(Path("a.dng"), 4000, 6000, 6)
        self.assertEqual(d.display_orientation, "landscape")
        self.assertEqual(d.raw_axis_for_display_stretch(), "y")
        self.assertEqual(d.target_default_scale(1.8), (1.0, 1.8))

    def test_existing_scale_multiplies(self):
        d = DngInfo(Path("a.dng"), 6000, 4000, 1, 1.1, 1.0)
        x, y = d.target_default_scale(2.0, True)
        self.assertAlmostEqual(x, 2.2)
        self.assertAlmostEqual(y, 1.0)

    def test_replace_existing_scale(self):
        d = DngInfo(Path("a.dng"), 6000, 4000, 1, 1.1, 1.2)
        self.assertEqual(d.target_default_scale(2.0, False), (2.0, 1.0))


class ParseTests(unittest.TestCase):
    def test_pair(self):
        self.assertEqual(parse_pair("1.33 1"), (1.33, 1.0))
        self.assertEqual(parse_pair([2, 1]), (2.0, 1.0))


class RawIfdDetectionTests(unittest.TestCase):
    def test_phone_style_raw_in_ifd0(self):
        obj = {
            "IFD0:PhotometricInterpretation": 32803,
            "IFD0:CFARepeatPatternDim": "2 2",
            "IFD0:ImageWidth": 4032,
            "IFD0:ImageHeight": 3024,
        }
        self.assertEqual(infer_raw_ifd_group(obj), "IFD0")
        self.assertEqual(parse_size_for_group(obj, "IFD0"), (4032, 3024))

    def test_adobe_style_thumbnail_ifd0_raw_subifd(self):
        obj = {
            "IFD0:PhotometricInterpretation": 2,
            "IFD0:ImageWidth": 256,
            "IFD0:ImageHeight": 171,
            "SubIFD:PhotometricInterpretation": 32803,
            "SubIFD:CFARepeatPatternDim": "2 2",
            "SubIFD:ImageWidth": 6000,
            "SubIFD:ImageHeight": 4000,
            "SubIFD:DefaultCropSize": "5984 3984",
        }
        self.assertEqual(infer_raw_ifd_group(obj), "SubIFD")
        self.assertEqual(parse_size_for_group(obj, "SubIFD"), (5984, 3984))

    def test_linear_raw_subifd_wins_over_larger_preview_noise(self):
        obj = {
            "IFD0:PhotometricInterpretation": 2,
            "IFD0:ImageWidth": 8000,
            "IFD0:ImageHeight": 5000,
            "SubIFD1:PhotometricInterpretation": 34892,
            "SubIFD1:ImageWidth": 4000,
            "SubIFD1:ImageHeight": 3000,
        }
        self.assertEqual(infer_raw_ifd_group(obj), "SubIFD1")


class ExifToolBootstrapTests(unittest.TestCase):
    def test_windows_download_prefers_current_sourceforge_distribution(self):
        urls = _exiftool_windows_download_urls("13.59")
        self.assertEqual(urls[0], "https://download.sourceforge.net/project/exiftool/files/exiftool-13.59_64.zip")
        self.assertIn("sourceforge.net/projects/exiftool/files/exiftool-13.59_64.zip/download", urls[1])

    def test_version_lookup_has_fallback(self):
        urls = _exiftool_version_urls()
        self.assertGreaterEqual(len(urls), 2)
        self.assertEqual(urls[0], "https://exiftool.org/ver.txt")


if __name__ == "__main__":
    unittest.main()

class UiDispatchTests(unittest.TestCase):
    def test_ui_dispatch_supports_keyword_arguments(self):
        # No Tk display is needed: construct without Tk.__init__ and replace after().
        from app import App
        app = App.__new__(App)
        seen = {}

        def fake_after(delay, callback, *args):
            self.assertEqual(delay, 0)
            callback(*args)

        def target(*args, **kwargs):
            seen["args"] = args
            seen["kwargs"] = kwargs

        app.after = fake_after
        App.ui(app, target, 123, maximum=456)
        self.assertEqual(seen["args"], (123,))
        self.assertEqual(seen["kwargs"], {"maximum": 456})

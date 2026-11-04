import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
import pymupdf as fitz
import pytest

from astro_translate.raster_obstacles import build_region_obstacles, check_overlay, native_ink_alpha


def make_page(image, *, jpeg=False):
    stream = io.BytesIO()
    image.save(stream, format="JPEG" if jpeg else "PNG", **({"quality": 80} if jpeg else {}))
    doc = fitz.open()
    page = doc.new_page(width=image.width, height=image.height)
    page.insert_image(page.rect, stream=stream.getvalue())
    return doc, page


def overlay(text="TEST", point=(25, 50)):
    doc = fitz.open()
    page = doc.new_page(width=200, height=100)
    page.insert_text(point, text, fontsize=14)
    return doc, page


def test_flat_jpeg_background_is_not_obstacle():
    doc, page = make_page(Image.new("RGB", (200, 100), (183, 159, 211)), jpeg=True)
    out, ink = overlay()
    result = check_overlay(page, ink, [10, 10, 180, 90])
    assert result["ok"]
    assert result["raster_foreground_pixels"] == 0


def test_circle_collision_is_rejected_but_free_space_passes():
    image = Image.new("RGB", (200, 100), "white")
    ImageDraw.Draw(image).ellipse((20, 20, 65, 65), fill=(140, 140, 140))
    doc, page = make_page(image, jpeg=True)
    out, ink = overlay()
    result = check_overlay(page, ink, [10, 10, 180, 90])
    assert not result["ok"] and result["raster_overlap_pixels"] > 0
    out2, safe = overlay(point=(100, 50))
    assert check_overlay(page, safe, [10, 10, 180, 90])["ok"]


def test_existing_original_overlap_does_not_exempt_new_ink():
    image = Image.new("RGB", (200, 100), "white")
    ImageDraw.Draw(image).rectangle((20, 20, 70, 60), fill="orange")
    doc, page = make_page(image)
    page.insert_text((25, 50), "TEST", fontsize=14)
    result = check_overlay(page, None, [10, 10, 180, 90], ink_alpha=native_ink_alpha(page))
    assert not result["ok"]


def test_texture_without_dominant_background_fails_closed():
    rng = np.random.default_rng(42)
    doc, page = make_page(Image.fromarray(rng.integers(0, 256, (100, 200, 3), dtype=np.uint8)))
    out, ink = overlay()
    result = check_overlay(page, ink, [10, 10, 180, 90])
    assert not result["ok"]
    assert result["reason"] == "no_dominant_flat_raster_background"


def test_empty_raster_page_passes():
    doc = fitz.open()
    page = doc.new_page(width=200, height=100)
    out, ink = overlay()
    result = check_overlay(page, ink, page.rect)
    assert result["ok"] and result["raster_covered_pixels"] == 0


def test_raster_mask_rejected_conservatively():
    doc, page = make_page(Image.new("RGBA", (200, 100), (255, 0, 0, 100)))
    out, ink = overlay()
    assert check_overlay(page, ink, page.rect)["reason"] == "raster_image_has_mask"


def test_real_pilot_p25_collisions_regression():
    book = Path(__file__).resolve().parents[1] / "books/astrology-wisdom-stars"
    snapshot = book / "work/pilot/raster_guard_regression/p0025-before-guard.pdf"
    if not snapshot.exists():
        pytest.skip("local book regression fixture not present")
    source = fitz.open(book / "source.pdf")
    candidate = fitz.open(snapshot)
    src, target = source[24], candidate[24]
    ink = native_ink_alpha(target)
    for box in ([377.29449, 244.94798, 514.79907, 313.45807],
                [229.40669, 360.08209, 417.18982, 372.08209]):
        assert check_overlay(src, None, box, ink_alpha=ink)["raster_overlap_pixels"] > 0
        assert check_overlay(src, None, box, ink_alpha=native_ink_alpha(src))["raster_overlap_pixels"] == 0


def test_real_icons_p24_p35_synthetic_new_ink_is_detected():
    book = Path(__file__).resolve().parents[1] / "books/astrology-wisdom-stars"
    if not (book / "source.pdf").exists():
        pytest.skip("local book regression fixture not present")
    source = fitz.open(book / "source.pdf")
    # Explicit synthetic bad overlays on real artwork, not historical PDFs.
    for number, box, origin in (
        (24, [363.1169, 496.94, 521.3945, 623.377], (364, 503)),
        (35, [274.2953, 113.54, 383.6717, 181.556], (275, 123)),
    ):
        src = source[number - 1]
        doc = fitz.open()
        page = doc.new_page(width=src.rect.width, height=src.rect.height)
        page.insert_text(origin, "TEST", fontsize=10)
        assert check_overlay(src, page, box)["raster_overlap_pixels"] > 0

"""Local, fail-closed raster artwork guard for native text composition.

Only foreground/background separation is inferred; no image is sent to a model.
Existing source ink overlap is diagnostic, never an automatic exemption, except the
documented pale cases (``native_underlay`` and ``pale_footprint``). A textured
or multicolour background without a dominant flat colour requires manual review.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageFilter
import pymupdf as fitz


@dataclass
class RegionObstacles:
    pixel_bbox: tuple[int, int, int, int]
    mask: np.ndarray
    background_rgb: tuple[int, int, int] | None
    background_fraction: float
    covered_pixels: int
    ambiguous: str | None = None
    native_underlay_pixels: int = 0


def _layer(page: fitz.Page, dpi: int, kind: str) -> np.ndarray:
    """Render actual PDF operators, preserving colour management and transforms.

    Raster path is restricted below to opaque, axis-aligned images.
    Clipping and transparency groups are forwarded to the rasteriser.
    """
    m = fitz.mupdf
    pix = fitz.Pixmap(fitz.csRGB, (page.rect * fitz.Matrix(dpi / 72, dpi / 72)).irect, True)
    pix.clear_with()
    draw = m.fz_new_draw_device(m.FzMatrix(dpi / 72, 0, 0, dpi / 72, 0, 0), pix.this)

    class LayerDevice(m.FzDevice2):
        def __init__(self):
            super().__init__()
            for operation in ("clip_path", "clip_stroke_path", "clip_text",
                              "clip_stroke_text", "clip_image_mask", "pop_clip",
                              "begin_group", "end_group"):
                getattr(self, "use_virtual_" + operation)()
            if kind == "raster":
                self.use_virtual_fill_image()
            else:
                self.use_virtual_fill_text()
                self.use_virtual_stroke_text()

        def clip_path(self, ctx, *args):
            m.ll_fz_clip_path(draw.m_internal, *args)

        def clip_stroke_path(self, ctx, *args):
            m.ll_fz_clip_stroke_path(draw.m_internal, *args)

        def clip_text(self, ctx, *args):
            m.ll_fz_clip_text(draw.m_internal, *args)

        def clip_stroke_text(self, ctx, *args):
            m.ll_fz_clip_stroke_text(draw.m_internal, *args)

        def clip_image_mask(self, ctx, *args):
            m.ll_fz_clip_image_mask(draw.m_internal, *args)

        def pop_clip(self, ctx):
            m.ll_fz_pop_clip(draw.m_internal)

        def begin_group(self, ctx, *args):
            m.ll_fz_begin_group(draw.m_internal, *args)

        def end_group(self, ctx):
            m.ll_fz_end_group(draw.m_internal)

        def fill_image(self, ctx, image, ctm, alpha, params):
            m.ll_fz_fill_image(draw.m_internal, image, ctm, alpha, params)

        def fill_text(self, ctx, text, ctm, colorspace, color, alpha, params):
            m.ll_fz_fill_text(draw.m_internal, text, ctm, m.ll_fz_device_gray(), (0,), alpha, params)

        def stroke_text(self, ctx, text, stroke, ctm, colorspace, color, alpha, params):
            m.ll_fz_stroke_text(draw.m_internal, text, stroke, ctm, m.ll_fz_device_gray(), (0,), alpha, params)

    device = LayerDevice()
    try:
        m.fz_run_page_contents(page.this, device, m.FzMatrix(), m.FzCookie())
    finally:
        m.fz_close_device(device)
        m.fz_close_device(draw)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 4).copy()


def native_ink_alpha(page: fitz.Page, dpi: int = 110) -> np.ndarray:
    """Actual source/candidate native ink for offline regression and diagnostics."""
    return _layer(page, dpi, "native")[:, :, 3]


def _raster_state(page: fitz.Page, dpi: int) -> tuple[np.ndarray, list[tuple[fitz.Rect, str]]]:
    cache = getattr(page, "_astro_raster_obstacle_cache", None)
    if cache is None:
        cache = {}
        page._astro_raster_obstacle_cache = cache
    if dpi not in cache:
        unsupported = []
        masks = {row[0]: row[1] for row in page.get_images(full=True)}
        for item in page.get_image_info(xrefs=True):
            a, b, c, d, _, _ = item["transform"]
            reason = None
            if abs(b) > 1e-5 or abs(c) > 1e-5 or a <= 0 or d <= 0:
                reason = "raster_transform_not_axis_aligned"
            if masks.get(item.get("xref"), 0):
                reason = "raster_image_has_mask"
            if not item.get("xref"):
                reason = "inline_raster_not_audited"
            if reason:
                unsupported.append((fitz.Rect(item["bbox"]), reason))
        cache[dpi] = (_layer(page, dpi, "raster"), unsupported)
    return cache[dpi]


def build_region_obstacles(
    source_page: fitz.Page, region: fitz.Rect | list[float], *, dpi: int = 110,
    colour_tolerance: int = 20, margin_pixels: int = 2,
    minimum_background_fraction: float = 0.55, native_underlay: bool = False,
    pale_footprint: list | None = None,
) -> RegionObstacles:
    """Separate artwork from a region's dominant flat raster background.

    Quantisation selects a JPEG-resistant modal colour; the median of its nearby
    cluster is compared with every raster pixel. Artwork is dilated to include
    antialiased edges. Background estimation never uses translated text pixels.
    """
    region = fitz.Rect(region) & source_page.rect
    rgba, unsupported = _raster_state(source_page, dpi)
    h, w = rgba.shape[:2]
    scale = dpi / 72
    x0 = max(0, int(np.floor(region.x0 * scale)))
    y0 = max(0, int(np.floor(region.y0 * scale)))
    x1 = min(w, int(np.ceil(region.x1 * scale)))
    y1 = min(h, int(np.ceil(region.y1 * scale)))
    box = (x0, y0, x1, y1)
    crop = rgba[y0:y1, x0:x1]
    covered = crop[:, :, 3] > 250
    count = int(covered.sum())
    mask = np.zeros(covered.shape, dtype=bool)
    for rect, reason in unsupported:
        if region.intersects(rect):
            return RegionObstacles(box, mask, None, 0.0, count, reason)
    if np.any((crop[:, :, 3] > 0) & ~covered):
        return RegionObstacles(box, mask, None, 0.0, count, "translucent_raster_background")
    if not count:
        return RegionObstacles(box, mask, None, 1.0, 0)
    pixels = crop[:, :, :3][covered].astype(np.int16)
    quant = pixels // 16
    keys = quant[:, 0] * 256 + quant[:, 1] * 16 + quant[:, 2]
    values, counts = np.unique(keys, return_counts=True)
    centre = np.median(pixels[keys == values[np.argmax(counts)]], axis=0)
    near = np.max(np.abs(pixels - centre), axis=1) <= colour_tolerance
    background = np.median(pixels[near], axis=0).astype(np.int16)
    distances = np.max(np.abs(crop[:, :, :3].astype(np.int16) - background), axis=2)
    foreground = covered & (distances > colour_tolerance)
    underlay_pixels=0
    if native_underlay and np.min(background)>180:
        # A pale two-tone watermark already under native prose is background,
        # not a foreground illustration. Require source ink on BOTH tones and
        # low RGB contrast throughout; dark/colourful artwork remains blocked.
        pale=covered & (distances>colour_tolerance) & (distances<=48) & (np.min(crop[:,:,:3],axis=2)>175)
        original=native_ink_alpha(source_page,dpi)[y0:y1,x0:x1]>80
        if np.count_nonzero(original & pale)>=50 and np.count_nonzero(original & covered & ~foreground)>=50:
            underlay_pixels=int(pale.sum())
            foreground &= ~pale
    if pale_footprint and np.min(background)>180:
        # A running head or label printed over a pale watermark (light stars
        # with white specks): inside the original text's own footprint, light
        # artwork that the original ink already covers is not an obstacle for
        # the new text. Outside the footprint, and for any dark pixel, the
        # guard is unchanged.
        zone=np.zeros(covered.shape,dtype=bool)
        for rect in pale_footprint:
            r=fitz.Rect(rect)
            a,b=max(0,int(np.floor(r.x0*scale))-x0),max(0,int(np.floor(r.y0*scale))-y0)
            c,d=min(x1,int(np.ceil(r.x1*scale)))-x0,min(y1,int(np.ceil(r.y1*scale)))-y0
            if c>a and d>b:zone[b:d,a:c]=True
        light=covered & (np.min(crop[:,:,:3],axis=2)>175) & zone
        original=native_ink_alpha(source_page,dpi)[y0:y1,x0:x1]>80
        if np.count_nonzero(original & light & (distances>colour_tolerance)):
            underlay_pixels+=int(np.count_nonzero(foreground & light))
            foreground &= ~light
    fraction = float(np.count_nonzero(covered & ~foreground) / count)
    if margin_pixels:
        foreground = np.asarray(Image.fromarray(foreground.astype(np.uint8) * 255).filter(
            ImageFilter.MaxFilter(2 * margin_pixels + 1))) > 0
    ambiguous = "no_dominant_flat_raster_background" if fraction < minimum_background_fraction else None
    return RegionObstacles(box, foreground, tuple(int(v) for v in background), fraction, count, ambiguous, underlay_pixels)


def check_overlay(
    source_page: fitz.Page, overlay_page: fitz.Page | None,
    region: fitz.Rect | list[float], *, ink_alpha: np.ndarray | None = None,
    dpi: int = 110, **options: Any,
) -> dict[str, Any]:
    """Reject any native ink on detected artwork, including preexisting overlap.

    ``overlay_page`` must contain only the proposed native text on transparency.
    Passing ``ink_alpha`` avoids rendering the same fit trial more than once.
    """
    obstacles = build_region_obstacles(source_page, region, dpi=dpi, **options)
    if ink_alpha is None:
        if overlay_page is None:
            raise ValueError("overlay_page or ink_alpha is required")
        pix = overlay_page.get_pixmap(dpi=dpi, alpha=True, colorspace=fitz.csRGB)
        ink_alpha = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 4)[:, :, 3]
    x0, y0, x1, y1 = obstacles.pixel_bbox
    ink = ink_alpha[y0:y1, x0:x1] > 80
    if ink.shape != obstacles.mask.shape:
        raise ValueError("ink alpha dimensions differ from source page")
    overlap = int(np.count_nonzero(ink & obstacles.mask))
    return {
        "ok": not obstacles.ambiguous and overlap == 0,
        "reason": obstacles.ambiguous or ("native_ink_over_raster_artwork" if overlap else None),
        "raster_overlap_pixels": overlap,
        "raster_foreground_pixels": int(obstacles.mask.sum()),
        "raster_covered_pixels": obstacles.covered_pixels,
        "raster_background_rgb": obstacles.background_rgb,
        "raster_background_fraction": obstacles.background_fraction,
        "raster_guard_dpi": dpi,
        "verified_pale_native_underlay_pixels": obstacles.native_underlay_pixels,
    }

"""
Image normalization for user-uploaded icons.

Every ImageField on the project (Company / ProductLine / Product / PaymentMethod)
runs uploaded files through :func:`optimize_image` before they hit storage. The
result is a cache-friendly WebP capped at a sensible square so that the
employee sales screen on mobile loads chips in kilobytes instead of megabytes,
even when management uploads a multi-MB PNG straight from a phone camera.
"""

from __future__ import annotations

import io
import logging
import os
from typing import Optional

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile

logger = logging.getLogger(__name__)

DEFAULT_MAX_SIZE = 256
DEFAULT_QUALITY = 82
WEBP_EXTENSION = ".webp"

# Django's FileField stores the relative path in a varchar(100) column by
# default. We cap the basename stem here so that even worst-case uploads
# (long upload_to prefix + storage's collision suffix `_xxxxxxx`) still fit
# without requiring a schema migration.
MAX_STEM_LEN = 60


def _trim_empty_borders(img):
    """
    Crop transparent / uniform borders away from *img* so logos display
    flush against the edges of the image box.

    Strategy:

    * If the image has an alpha channel, find the bounding box of pixels
      whose alpha is above ``8/255`` (i.e. visibly opaque). This handles
      the typical "PNG with transparent padding" case perfectly.
    * Otherwise, sample the four corners and trim rows/columns whose
      pixels are all within a small distance of the (assumed) background
      color. This rescues white-padded JPGs without false-positives on
      photos because the threshold is conservative.

    Returns the (possibly cropped) image. Always returns the original
    when no meaningful crop is possible — never returns an empty image.
    """
    from PIL import Image, ImageChops

    try:
        if img.mode == "RGBA":
            alpha = img.split()[-1]
            # Drop near-transparent pixels before measuring the bbox so
            # subtle anti-aliasing halos don't leave a 1px ghost border.
            mask = alpha.point(lambda v: 255 if v > 8 else 0)
            bbox = mask.getbbox()
        elif img.mode == "LA":
            bbox = img.split()[-1].point(lambda v: 255 if v > 8 else 0).getbbox()
        else:
            # Heuristic: assume the top-left corner is the background.
            bg = img.getpixel((0, 0))
            bg_img = Image.new(img.mode, img.size, bg)
            diff = ImageChops.difference(img, bg_img)
            # `getbbox` returns None if the image is entirely background.
            if diff.getbbox() is None:
                return img
            # Threshold the diff so off-white pixels don't keep a margin.
            if diff.mode != "L":
                diff = diff.convert("L")
            mask = diff.point(lambda v: 255 if v > 12 else 0)
            bbox = mask.getbbox()

        if not bbox:
            return img

        x0, y0, x1, y1 = bbox
        if (x1 - x0) < 4 or (y1 - y0) < 4:
            # Suspiciously empty — probably an artefact, leave the
            # original alone instead of returning a blank thumbnail.
            return img
        return img.crop(bbox)
    except Exception:  # noqa: BLE001 — Pillow can raise on weird modes
        return img


def optimize_image(
    file_obj,
    *,
    max_size: int = DEFAULT_MAX_SIZE,
    quality: int = DEFAULT_QUALITY,
    trim: bool = False,
) -> Optional[ContentFile]:
    """
    Re-encode *file_obj* as a size-capped, EXIF-stripped WebP.

    Returns a :class:`~django.core.files.base.ContentFile` ready to assign to an
    ``ImageField``, keeping the original filename stem but switching the
    extension to ``.webp``. Returns ``None`` when *file_obj* is falsy or when
    Pillow cannot decode it (corrupt upload, unsupported format) — callers
    should fall back to leaving the original file untouched in that case.

    Set ``trim=True`` for assets that should sit flush in their display box
    (logos, banners) — empty transparent / solid-color borders are cropped
    out before resizing so a centered wordmark on a 2000×600 canvas doesn't
    show up as a tiny image swimming in white space.
    """
    if not file_obj:
        return None

    try:
        from PIL import Image, ImageOps
    except ImportError:
        logger.warning("Pillow is not installed; skipping icon optimization.")
        return None

    original_name = getattr(file_obj, "name", "") or "icon"

    try:
        if hasattr(file_obj, "seek"):
            try:
                file_obj.seek(0)
            except (AttributeError, OSError):
                pass

        with Image.open(file_obj) as img:
            # Honor EXIF orientation before stripping metadata so portrait
            # phone uploads don't end up sideways.
            img = ImageOps.exif_transpose(img)

            # Palette / 1-bit / CMYK uploads can't be saved as WebP directly;
            # normalize to a mode WebP supports and that preserves alpha.
            if img.mode in ("P", "LA"):
                img = img.convert("RGBA")
            elif img.mode == "CMYK":
                img = img.convert("RGB")
            elif img.mode not in ("RGB", "RGBA", "L"):
                img = img.convert("RGBA")

            if trim:
                img = _trim_empty_borders(img)

            # Inline downscale; thumbnail() is a no-op for already-small images.
            img.thumbnail((max_size, max_size), Image.LANCZOS)

            buffer = io.BytesIO()
            save_kwargs = {
                "format": "WEBP",
                "quality": quality,
                "method": 6,
            }
            if img.mode == "RGBA":
                save_kwargs["lossless"] = False
            img.save(buffer, **save_kwargs)
            buffer.seek(0)
    except Exception as exc:  # noqa: BLE001 — Pillow raises a wide variety
        logger.warning("Could not optimize uploaded image %r: %s", original_name, exc)
        return None

    stem, _ = os.path.splitext(os.path.basename(original_name))
    stem = (stem or "icon")[:MAX_STEM_LEN]
    new_name = stem + WEBP_EXTENSION
    return ContentFile(buffer.read(), name=new_name)


def optimize_field_file(field_file) -> bool:
    """
    In-place re-encode of an existing ``ImageField`` value on disk.

    Used by the ``optimize_icons`` management command to backfill files that
    pre-date this hook. Returns ``True`` when the underlying storage was
    rewritten with a smaller WebP, ``False`` when nothing changed (file
    missing, already optimized, or Pillow could not read it).
    """
    if not field_file or not getattr(field_file, "name", ""):
        return False

    storage = field_file.storage
    old_name = field_file.name

    if _already_optimized(storage, old_name):
        return False

    try:
        with storage.open(old_name, "rb") as src:
            optimized = optimize_image(src)
    except FileNotFoundError:
        logger.warning("Icon file is missing on disk: %s", old_name)
        return False

    if optimized is None:
        return False

    dirname = os.path.dirname(old_name)
    stem, _ = os.path.splitext(os.path.basename(old_name))
    stem = stem[:MAX_STEM_LEN]
    target_name = os.path.join(dirname, stem + WEBP_EXTENSION) if dirname else stem + WEBP_EXTENSION

    saved_name = storage.save(target_name, optimized)
    try:
        field_file.name = saved_name
        field_file.instance.save(update_fields=[field_file.field.attname])
    except Exception:
        # The WebP is on disk but the DB row could not be updated (e.g. column
        # length overflow). Delete the orphan to keep storage consistent and
        # let the caller decide how to surface the failure.
        try:
            storage.delete(saved_name)
        except OSError as cleanup_exc:
            logger.warning("Could not delete orphan WebP %s: %s", saved_name, cleanup_exc)
        field_file.name = old_name
        raise

    if saved_name != old_name:
        try:
            storage.delete(old_name)
        except OSError as exc:
            logger.warning("Could not remove old icon %s: %s", old_name, exc)

    return True


def _already_optimized(storage, name: str, max_size: int = DEFAULT_MAX_SIZE) -> bool:
    """
    True when the stored file is a WebP whose largest dimension fits *max_size*.

    This makes :func:`optimize_field_file` idempotent: re-running the backfill
    command on icons that have already been processed is a no-op instead of a
    pointless re-encode that just generates a new collision-suffixed copy.
    """
    if not name or not name.lower().endswith(WEBP_EXTENSION):
        return False

    try:
        from PIL import Image
    except ImportError:
        return False

    try:
        with storage.open(name, "rb") as src:
            with Image.open(src) as img:
                width, height = img.size
    except (FileNotFoundError, OSError, ValueError):
        return False
    except Exception:  # noqa: BLE001 — Pillow can raise misc decode errors
        return False

    return max(width, height) <= max_size


def maybe_optimize_image_field(
    instance,
    field_name: str = "icon",
    *,
    max_size: int = DEFAULT_MAX_SIZE,
    quality: int = DEFAULT_QUALITY,
    trim: bool = False,
) -> None:
    """
    Re-encode a freshly uploaded image on *instance* in place before save.

    Intended to be called from a model's ``save()`` immediately before
    ``super().save()``. No-ops when the field is empty, when its current value
    is already a stored file (i.e. not a fresh upload), or when the optimizer
    cannot decode the input. ``max_size`` / ``quality`` let bigger fields
    (logos, banners) opt into a larger cap than the icon default; ``trim``
    enables transparent / solid-color border cropping for logo-style assets.
    """
    field_file = getattr(instance, field_name, None)
    if not field_file:
        return

    raw = getattr(field_file, "file", None)
    if not isinstance(raw, UploadedFile):
        return

    optimized = optimize_image(raw, max_size=max_size, quality=quality, trim=trim)
    if optimized is None:
        return

    setattr(instance, field_name, optimized)

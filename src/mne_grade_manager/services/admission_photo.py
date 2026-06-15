"""Extraction de la photo d'identité depuis un dossier de candidature PDF."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

_MIN_SIDE_PX = 120
_MIN_BYTES = 5_000
_MAX_SCAN_PAGES = 12
_IDEAL_RATIO = 1.33
_PORTRAIT_WIDTH_RATIO = 0.75  # largeur / hauteur du recadrage portrait
_FACE_DETECT_MAX_DIM = 1_200
_FACE_MIN_CROP_SIDE = 80

_CASCADE = None


@dataclass
class ExtractedPhoto:
    data: bytes
    suffix: str
    page_index: int
    width: int
    height: int
    score: float


@dataclass(frozen=True)
class _FaceBox:
    x: int
    y: int
    width: int
    height: int

    @property
    def center_x(self) -> int:
        return self.x + self.width // 2

    @property
    def center_y(self) -> int:
        return self.y + self.height // 2

    def area_ratio(self, image_width: int, image_height: int) -> float:
        if image_width <= 0 or image_height <= 0:
            return 0.0
        return (self.width * self.height) / (image_width * image_height)


def _face_cascade():
    global _CASCADE
    if _CASCADE is None:
        import cv2

        _CASCADE = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
    return _CASCADE


def _detect_largest_face(pil_image) -> _FaceBox | None:
    """Détecte le plus grand visage (coordonnées dans l'image d'origine)."""
    import cv2
    import numpy as np
    from PIL import Image

    pil = pil_image.convert("RGB")
    width, height = pil.size
    scale = 1.0
    work = pil
    if max(width, height) > _FACE_DETECT_MAX_DIM:
        scale = _FACE_DETECT_MAX_DIM / max(width, height)
        work = pil.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.Resampling.LANCZOS,
        )

    gray = cv2.cvtColor(np.array(work), cv2.COLOR_RGB2GRAY)
    faces = _face_cascade().detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
    if faces is None or len(faces) == 0:
        return None

    x, y, fw, fh = max(faces, key=lambda f: int(f[2]) * int(f[3]))
    if scale != 1.0:
        inv = 1.0 / scale
        x, y, fw, fh = int(x * inv), int(y * inv), int(fw * inv), int(fh * inv)
    return _FaceBox(x=x, y=y, width=fw, height=fh)


def _clamp_crop_box(
    left: int,
    top: int,
    right: int,
    bottom: int,
    *,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    crop_w = right - left
    crop_h = bottom - top
    if crop_w > image_width:
        crop_w = image_width
        left = 0
        right = image_width
    if crop_h > image_height:
        crop_h = image_height
        top = 0
        bottom = image_height

    if left < 0:
        right = min(image_width, right - left)
        left = 0
    if top < 0:
        bottom = min(image_height, bottom - top)
        top = 0
    if right > image_width:
        left = max(0, left - (right - image_width))
        right = image_width
    if bottom > image_height:
        top = max(0, top - (bottom - image_height))
        bottom = image_height
    return left, top, right, bottom


def _crop_portrait_on_face(pil_image, face: _FaceBox):
    """Recadre un portrait 3:4 centré sur le visage (tête + épaules)."""
    from PIL import Image

    img_w, img_h = pil_image.size
    crop_h = max(int(face.height / 0.45), int(face.width / (0.45 * _PORTRAIT_WIDTH_RATIO)))
    crop_w = int(crop_h * _PORTRAIT_WIDTH_RATIO)

    left = face.center_x - crop_w // 2
    top = face.center_y - int(crop_h * 0.40)
    right = left + crop_w
    bottom = top + crop_h
    left, top, right, bottom = _clamp_crop_box(
        left, top, right, bottom, image_width=img_w, image_height=img_h
    )

    if right - left < _FACE_MIN_CROP_SIDE or bottom - top < _FACE_MIN_CROP_SIDE:
        return pil_image
    return pil_image.crop((left, top, right, bottom))


def _encode_jpeg(pil_image, *, quality: int = 88) -> bytes:
    buf = BytesIO()
    pil_image.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _score_photo_candidate(
    *,
    page_index: int,
    width: int,
    height: int,
    nbytes: int,
    fmt: str,
    face_area_ratio: float = 0.0,
) -> float:
    if width < _MIN_SIDE_PX or height < _MIN_SIDE_PX:
        return -1.0
    if nbytes < _MIN_BYTES:
        return -1.0
    if min(width, height) < 80:
        return -1.0

    ratio = height / width if width else 0.0
    if ratio < 0.82 or ratio > 2.1:
        return -1.0

    area = width * height
    short_side = min(width, height)
    long_side = max(width, height)

    if area > 2_200_000:
        return -1.0
    if long_side > 1_400:
        return -1.0

    score = 100.0
    score -= page_index * 10.0
    score -= abs(ratio - _IDEAL_RATIO) * 22.0

    if 350 <= short_side <= 800 and 450 <= long_side <= 1_100:
        score += 30.0
    elif short_side < 250 or long_side > 1_200:
        score -= 20.0

    if area > 1_000_000:
        score -= 35.0
    elif 200_000 <= area <= 900_000:
        score += 15.0

    if fmt.upper() in {"JPEG", "JPG"}:
        score += 8.0
    elif fmt.upper() == "PNG":
        score += 2.0

    if 8_000 <= nbytes <= 200_000:
        score += 12.0
    if nbytes > 350_000:
        score -= 20.0

    if face_area_ratio > 0:
        score += 90.0
        score += min(face_area_ratio * 220.0, 80.0)
        if face_area_ratio < 0.08:
            score -= 70.0
    else:
        score -= 60.0

    return score


def _refine_extracted_photo(pil_image, raw_data: bytes, fmt: str) -> tuple[bytes, int, int]:
    """Recadre sur le visage si détecté, sinon renvoie l'image d'origine."""
    face = _detect_largest_face(pil_image)
    if face is None:
        return raw_data, pil_image.size[0], pil_image.size[1]

    cropped = _crop_portrait_on_face(pil_image, face)
    if cropped.size == pil_image.size:
        return raw_data, pil_image.size[0], pil_image.size[1]

    suffix_fmt = fmt.upper()
    if suffix_fmt in {"JPEG", "JPG"} or _normalize_suffix(fmt, "x.jpg") == ".jpg":
        return _encode_jpeg(cropped), cropped.size[0], cropped.size[1]
    return _encode_jpeg(cropped), cropped.size[0], cropped.size[1]


def extract_candidate_photo_from_pdf(path: str | Path) -> ExtractedPhoto | None:
    """
    Retourne la meilleure candidate « photo d'identité » parmi les images du PDF.
    Priorité au visage détecté, puis recadrage portrait centré sur le visage.
    """
    from PIL import Image
    from pypdf import PdfReader

    pdf_path = Path(path)
    if not pdf_path.is_file():
        return None

    reader = PdfReader(str(pdf_path))
    best: ExtractedPhoto | None = None
    seen_hashes: set[str] = set()

    for page_index, page in enumerate(reader.pages[:_MAX_SCAN_PAGES]):
        for img in page.images:
            digest = hashlib.md5(img.data).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)

            try:
                pil = Image.open(BytesIO(img.data))
                width, height = pil.size
                fmt = str(pil.format or "JPEG")
                face = _detect_largest_face(pil)
                face_ratio = face.area_ratio(width, height) if face else 0.0
                score = _score_photo_candidate(
                    page_index=page_index,
                    width=width,
                    height=height,
                    nbytes=len(img.data),
                    fmt=fmt,
                    face_area_ratio=face_ratio,
                )
                if score < 0:
                    continue
                candidate = ExtractedPhoto(
                    data=img.data,
                    suffix=_normalize_suffix(fmt, img.name),
                    page_index=page_index,
                    width=width,
                    height=height,
                    score=score,
                )
                if best is None or candidate.score > best.score:
                    best = candidate
            except Exception:
                continue

    if best is None:
        return None

    try:
        pil_best = Image.open(BytesIO(best.data))
        refined_data, refined_w, refined_h = _refine_extracted_photo(pil_best, best.data, pil_best.format or "JPEG")
        return ExtractedPhoto(
            data=refined_data,
            suffix=".jpg" if refined_data != best.data else best.suffix,
            page_index=best.page_index,
            width=refined_w,
            height=refined_h,
            score=best.score,
        )
    except Exception:
        return best


def _normalize_suffix(fmt: str, name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ext
    f = fmt.upper()
    if f in {"JPEG", "JPG"}:
        return ".jpg"
    if f == "PNG":
        return ".png"
    if f == "WEBP":
        return ".webp"
    if f == "GIF":
        return ".gif"
    return ".jpg"


def save_extracted_photo_temp(photo: ExtractedPhoto) -> Path:
    """Écrit la photo extraite dans un fichier temporaire (pour import_student_photo)."""
    import os
    import tempfile

    suffix = photo.suffix if photo.suffix.startswith(".") else f".{photo.suffix}"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="admission_photo_")
    with os.fdopen(fd, "wb") as fh:
        fh.write(photo.data)
    return Path(tmp_path)

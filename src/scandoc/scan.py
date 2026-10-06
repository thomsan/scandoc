"""Document geometry shared by the CLI, desktop editor and web service."""
from pathlib import Path
import io
import os
import tempfile
import zlib

import cv2
import numpy as np
from PIL import Image, ImageOps

valid_formats = [".jpg", ".jpeg", ".jp2", ".png", ".bmp", ".webp", ".tiff", ".tif"]


def load_image(source, max_pixels=24_000_000, allowed_formats=None):
    try:
        with Image.open(source) as image:
            if allowed_formats is not None and image.format not in allowed_formats:
                raise ValueError("Unsupported image; use JPEG, PNG, WebP or TIFF")
            if image.width * image.height > max_pixels:
                raise ValueError("Image exceeds the pixel limit")
            return ImageOps.exif_transpose(image).convert("RGB")
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid or unsupported image") from exc


def detect_corners(image):
    array = np.asarray(image)
    height, width = array.shape[:2]
    scale = min(1.0, 1000 / max(width, height))
    small = cv2.resize(array, (max(2, round(width * scale)), max(2, round(height * scale))))
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    masks = [cv2.Canny(gray, 50, 150), cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]]
    candidates = []
    for mask in masks:
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:20]:
            polygon = cv2.approxPolyDP(contour, .02 * cv2.arcLength(contour, True), True)
            area = cv2.contourArea(polygon)
            if len(polygon) == 4 and cv2.isContourConvex(polygon) and area > small.shape[0] * small.shape[1] * .08:
                points = polygon.reshape(4, 2)
                border = np.any((points[:, 0] <= 2) | (points[:, 1] <= 2) | (points[:, 0] >= small.shape[1]-3) | (points[:, 1] >= small.shape[0]-3))
                candidates.append((area * (.4 if border else 1), points))
    if not candidates:
        return [[0, 0], [width-1, 0], [width-1, height-1], [0, height-1]]
    points = max(candidates, key=lambda item: item[0])[1].astype(float) / scale
    points[:, 0] = np.clip(points[:, 0], 0, width-1)
    points[:, 1] = np.clip(points[:, 1], 0, height-1)
    center = points.mean(axis=0)
    points = points[np.argsort(np.arctan2(points[:, 1]-center[1], points[:, 0]-center[0]))]
    points = np.roll(points, -np.argmin(points.sum(axis=1)), axis=0)
    return points.tolist()


def validate_corners(corners, width, height):
    points = np.asarray(corners, dtype=np.float32)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        raise ValueError("Supply four finite corners in top-left, top-right, bottom-right, bottom-left order")
    if np.any(points < 0) or np.any(points[:, 0] > width-1) or np.any(points[:, 1] > height-1):
        raise ValueError("Corners must be inside the image")
    if not cv2.isContourConvex(points.reshape(4, 1, 2)) or cv2.contourArea(points, oriented=True) < 16:
        raise ValueError("Corners must form a non-intersecting quadrilateral")
    return points


def correct_image(image, corners=None, rotation=0, mode="color"):
    if rotation not in (0, 90, 180, 270) or mode not in ("color", "grayscale", "document"):
        raise ValueError("Invalid rotation or cleanup mode")
    points = validate_corners(corners if corners is not None else detect_corners(image), *image.size)
    tl, tr, br, bl = points
    width = max(2, round(max(np.linalg.norm(tr-tl), np.linalg.norm(br-bl))))
    height = max(2, round(max(np.linalg.norm(bl-tl), np.linalg.norm(br-tr))))
    matrix = cv2.getPerspectiveTransform(points, np.float32([[0, 0], [width-1, 0], [width-1, height-1], [0, height-1]]))
    result = cv2.warpPerspective(np.asarray(image), matrix, (width, height))
    if mode != "color":
        result = cv2.cvtColor(result, cv2.COLOR_RGB2GRAY)
        if mode == "document":
            result = cv2.adaptiveThreshold(result, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 12)
    return Image.fromarray(result).rotate(-rotation, expand=True)


def write_pdf(images, output_path, page_size="natural", max_bytes=None):
    """Write each page directly to disk without retaining previous image streams.

    Color pages use high-quality JPEG; grayscale/cleanup pages remain lossless.
    PDF object offsets and page references are the only per-document state.
    """
    if page_size not in ("natural", "a4"):
        raise ValueError("Unknown PDF page size")
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    offsets = {}
    pages = []
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix="."+path.name+".", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            def write(data):
                if max_bytes is not None and output.tell() + len(data) > max_bytes:
                    raise ValueError("Generated PDF exceeds the document byte limit; reduce the number or size of pages")
                output.write(data)

            def object(number, body, stream=None):
                offsets[number] = output.tell()
                write(f"{number} 0 obj\n".encode() + body)
                if stream is not None:
                    write(b"\nstream\n")
                    write(stream)
                    write(b"\nendstream")
                write(b"\nendobj\n")

            write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
            object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
            for image in images:
                width, height = image.size
                if page_size == "a4":
                    pw, ph = (595.28, 841.89) if width <= height else (841.89, 595.28)
                    scale = min((pw-36)/width, (ph-36)/height)
                    dw, dh = width*scale, height*scale
                else:
                    pw, ph = width * 72/300, height * 72/300
                    dw, dh = pw, ph
                image_id = 3 + len(pages)*3
                if image.mode in ("1", "L"):
                    content = zlib.compress(image.convert("L").tobytes())
                    space, filter_name = "DeviceGray", "FlateDecode"
                else:
                    encoded = io.BytesIO()
                    image.convert("RGB").save(encoded, "JPEG", quality=95)
                    content = encoded.getvalue()
                    space, filter_name = "DeviceRGB", "DCTDecode"
                    encoded.close()
                object(image_id, (f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
                                 f"/ColorSpace /{space} /BitsPerComponent 8 /Filter /{filter_name} /Length {len(content)} >>").encode(), content)
                del content
                commands = f"q {dw:.6f} 0 0 {dh:.6f} {(pw-dw)/2:.6f} {(ph-dh)/2:.6f} cm /Image Do Q\n".encode()
                object(image_id+1, f"<< /Length {len(commands)} >>".encode(), commands)
                page_id = image_id+2
                object(page_id, (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.6f} {ph:.6f}] "
                                 f"/Resources << /XObject << /Image {image_id} 0 R >> >> /Contents {image_id+1} 0 R >>").encode())
                pages.append(page_id)
            if not pages:
                raise ValueError("No images to export")
            references = " ".join(f"{number} 0 R" for number in pages)
            object(2, f"<< /Type /Pages /Count {len(pages)} /Kids [{references}] >>".encode())
            start = output.tell()
            size = max(offsets) + 1
            write(f"xref\n0 {size}\n0000000000 65535 f \n".encode())
            for number in range(1, size):
                write(f"{offsets[number]:010d} 00000 n \n".encode())
            write(f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def scan(img_path: str, output_path: str | None = None, interactive_mode: bool = False):
    image = load_image(img_path)
    corners = detect_corners(image)
    if interactive_mode:
        from .interactive_get_contour import interactive_get_contour
        corners = interactive_get_contour(np.asarray(corners), np.asarray(image)).tolist()
    result = correct_image(image, corners)
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        result.save(output_path)
    return np.asarray(result)


def multi_scan(img_dir: str, output_dir: str | None = None, interactive_mode: bool = False) -> list:
    files = sorted(p for p in Path(img_dir).iterdir() if p.suffix.lower() in valid_formats)
    if not files:
        raise ValueError("No supported images found")
    images = []
    for path in files:
        result = scan(str(path), str(Path(output_dir)/path.name) if output_dir else None, interactive_mode)
        if not output_dir:
            images.append(Image.fromarray(result))
    return images


def scan2pdf(img_path: str, output_path: str, interactive_mode: bool = False):
    write_pdf([Image.fromarray(scan(img_path, interactive_mode=interactive_mode))], output_path)


def multi_scan2pdf(img_dir: str, output_path: str, interactive_mode: bool = False):
    files = sorted(p for p in Path(img_dir).iterdir() if p.suffix.lower() in valid_formats)
    write_pdf((Image.fromarray(scan(str(p), interactive_mode=interactive_mode)) for p in files), output_path)

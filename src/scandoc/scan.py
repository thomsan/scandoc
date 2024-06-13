import os
import sys

import cv2
import imutils
from imutils import perspective
from PIL import Image

from .interactive_get_contour import interactive_get_contour

APPROX_POLY_DP_ACCURACY_RATIO = 0.02
IMG_RESIZE_H = 500.0

valid_formats = [".jpg", ".jpeg", ".jp2", ".png", ".bmp", ".tiff", ".tif"]


def scan(img_path: str, output_path: str | None = None, interactive_mode: bool = False):
    """
    Scan a single image and return the scanned image as a numpy array.
    :param img_path: Path to the image to be scanned
    :param output_path: Path to save the scanned image
    :param interactive_mode: Flag for manually verifying and/or setting document corners
    :return: Scanned image as a numpy array
    """

    if os.path.splitext(img_path)[1].lower() not in valid_formats:
        print(f"Invalid file format. Valid formats are: {valid_formats}")
        sys.exit(1)

    use_otsu = True
    ksize = 7
    threshold = 130

    img = cv2.imread(img_path)
    assert img is not None
    orig = img.copy()
    ratio = img.shape[0] / IMG_RESIZE_H
    img = imutils.resize(img, height=int(IMG_RESIZE_H))

    img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    img = cv2.GaussianBlur(img, (ksize, ksize), 0)
    # thresholding
    threshold_type = cv2.THRESH_BINARY + cv2.THRESH_OTSU if use_otsu else cv2.THRESH_BINARY
    _, img = cv2.threshold(
        img,
        threshold,
        255,
        threshold_type,
    )
    # find contours
    cnts, _ = cv2.findContours(img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    # Sort contours by area and keep the largest one
    cnts = sorted(cnts, key=cv2.contourArea, reverse=True)[:5]

    for c in cnts:
        # Approximate the contour to a polygon
        perimeter = cv2.arcLength(c, True)
        polygon = cv2.approxPolyDP(c, APPROX_POLY_DP_ACCURACY_RATIO * perimeter, True)
        # If the polygon has 4 vertices, we've likely found the paper
        if len(polygon) == 4:
            outline = polygon.reshape(4, 2)

    if interactive_mode and outline.any():
        tmp_img = cv2.cvtColor(orig, cv2.COLOR_BGR2RGB)
        outline = interactive_get_contour(outline, imutils.resize(tmp_img, height=int(IMG_RESIZE_H)))

    if outline is None:
        result = orig
    else:
        result = perspective.four_point_transform(orig, outline * ratio)

    if output_path:
        if not os.path.exists(os.path.dirname(output_path)):
            os.makedirs(os.path.dirname(output_path))
        cv2.imwrite(output_path, result)

    return cv2.cvtColor(result, cv2.COLOR_BGR2RGB)


def multi_scan(img_dir: str, output_dir: str | None = None, interactive_mode: bool = False) -> list:
    """
    Scan multiple images and return the scanned images as a list of PIL Images.
    :param img_dir: Directory of images to be scanned
    :param output_dir: Directory to save the scanned images
    :param interactive_mode: Flag for manually verifying and/or setting document corners
    :return: List of PIL Images
    """

    img_files = [f for f in os.listdir(img_dir) if os.path.splitext(f)[1].lower() in valid_formats]
    images = []
    for img_f in img_files:
        if output_dir:
            scan(
                img_path=os.path.join(img_dir, img_f),
                output_path=os.path.join(output_dir, img_f),
                interactive_mode=interactive_mode,
            )
        else:
            output_img = scan(img_path=os.path.join(img_dir, img_f), interactive_mode=interactive_mode)
            images.append(Image.fromarray(output_img))
    if not output_dir:
        return images


def scan2pdf(img_path: str, output_path: str, interactive_mode: bool = False):
    """
    Scan a single image and save the scanned image as a PDF.
    :param img_path: Path to the image to be scanned
    :param output_path: Path to save the scanned PDF
    :param interactive_mode: Flag for manually verifying and/or setting document corners
    """

    image = Image.fromarray(scan(img_path=img_path, interactive_mode=interactive_mode))
    if not os.path.exists(os.path.dirname(output_path)):
        os.makedirs(os.path.dirname(output_path))
    image.save(output_path, "PDF", resolution=100.0)


def multi_scan2pdf(img_dir: str, output_path: str, interactive_mode: bool = False):
    """
    Scan multiple images and save the scanned images as a PDF.
    :param img_dir: Directory of images to be scanned
    :param output_path: Path to save the scanned PDF
    :param interactive_mode: Flag for manually verifying and/or setting document corners
    """

    images = multi_scan(img_dir=img_dir, interactive_mode=interactive_mode)
    # scale images to same size
    widths, heights = zip(*(i.size for i in images))
    min_width = min(widths)
    min_height = min(heights)
    images = [i.resize((min_width, min_height)) for i in images]
    # make sure the directory exists
    output_dir = os.path.dirname(output_path)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    images[0].save(output_path, "PDF", resolution=100.0, save_all=True, append_images=images[1:])

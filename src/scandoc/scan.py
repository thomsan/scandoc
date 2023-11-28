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


def scan(img_path, output_path=None, interactive_mode=False):
    if os.path.splitext(img_path)[1].lower() not in valid_formats:
        print("Invalid file format. Valid formats are: {}".format(valid_formats))
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
        outline = interactive_get_contour(outline, imutils.resize(orig, height=int(IMG_RESIZE_H)))

    if outline is None:
        result = orig
    else:
        result = perspective.four_point_transform(orig, outline * ratio)

    if output_path:
        if not os.path.exists(os.path.dirname(output_path)):
            os.makedirs(os.path.dirname(output_path))
        cv2.imwrite(output_path, result)

    return result


def multi_scan(img_dir, output_dir=None, interactive_mode=False):
    img_files = [f for f in os.listdir(img_dir) if os.path.splitext(f)[1].lower() in valid_formats]
    images = []
    for img_f in img_files:
        if output_dir:
            scan(
                os.path.join(img_dir, img_f),
                output_path=os.path.join(output_dir, img_f),
                interactive_mode=interactive_mode,
            )
        else:
            output_img = scan(os.path.join(img_dir, img_f), interactive_mode)
            images.append(Image.fromarray(output_img))
    if not output_dir:
        return images


def scan2pdf(img_path, output_path, interactive_mode=False):
    img = scan(img_path, interactive_mode)
    if not os.path.exists(os.path.dirname(output_path)):
        os.makedirs(os.path.dirname(output_path))
    Image.fromarray(img).save(output_path, "PDF", resolution=100.0)


def multi_scan2pdf(img_dir, output_path, interactive_mode=False):
    images = multi_scan(img_dir, interactive_mode)
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

import argparse
import os

from .scan import multi_scan, multi_scan2pdf, scan, scan2pdf


def main_cli():
    """
    Command line interface for scanning documents.

    Usage:
    python -m src.scandoc.main_cli --images <img_dir> --output <output_dir>
    python -m src.scandoc.main_cli --image <img_file> --output <output_file>
    python -m src.scandoc.main_cli --images <img_dir> --pdf --output <output_file>
    python -m src.scandoc.main_cli --image <img_file> --pdf --output <output_file>
    python -m src.scandoc.main_cli --images <img_dir> --output <output_dir> --interactive
    python -m src.scandoc.main_cli --image <img_file> --output <output_file> --interactive
    python -m src.scandoc.main_cli --images <img_dir> --pdf --output <output_file> --interactive
    python -m src.scandoc.main_cli --image <img_file> --pdf --output <output_file> --interactive
    """
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--images", help="Directory of images to be scanned")
    group.add_argument("--image", help="Path to single image to be scanned")
    ap.add_argument("-o", "--output", help="Path to output file or directory")
    ap.add_argument(
        "-i", "--interactive", action="store_true", help="Interactive mode for manually setting document corners"
    )
    ap.add_argument("-pdf", action="store_true", help="Save as pdf instead of image file")

    args = vars(ap.parse_args())
    img_dir = args["images"]
    img_file_path = args["image"]
    interactive_mode = args["i"]
    pdf = args["pdf"]
    output_path = args["output"]

    # single file
    if img_file_path:
        if pdf:
            if output_path is None:
                output_path = os.path.splitext(img_file_path)[0] + ".pdf"
            scan2pdf(img_file_path, output_path, interactive_mode)
        else:
            if output_path is None:
                input_dir = os.path.dirname(img_file_path)
                output_dir = os.path.join(input_dir, "output")
                output_path = os.path.join(output_dir, os.path.basename(img_file_path))
            scan(img_file_path, output_path=output_path, interactive_mode=interactive_mode)

    # multiple files
    else:
        if pdf:
            if output_path is None:
                output_path = os.path.join(img_dir, "output.pdf")
            multi_scan2pdf(img_dir, output_path=output_path, interactive_mode=interactive_mode)
        else:
            if output_path is None:
                output_dir = os.path.join(img_dir, "output")
            multi_scan(img_dir, output_dir=output_dir, interactive_mode=interactive_mode)


if __name__ == "__main__":
    main_cli()

# Scandoc

**A command line tool and python library for scanning documents in images.**

The tool uses OpenCV to detect the document in the image and then uses a perspective transform to transform the document into a rectangle. It can output it as an image or a pdf. Multiple images are combined into a single pdf.

## Usage

Install scandoc for the current user

```
make install
```

As soon as it is published on PyPI, you can install it using pip. Which is not the case yet.
Install the package using pip

```
pip install scandoc
```

#### Use from the command line

```
scandoc (--images <IMG_DIR> | --image <IMG_PATH>) [-i] [-pdf] [--output <OUTPUT_PATH>]
```

The `-i` flag enables interactive mode, where you will be prompted to click and drag the corners of the document.

The `-pdf`` flag enables pdf output. If -pdf is enabled together with --images, the output will be a single pdf file containing all the images.

The `--output` flag specifies the output path. If not specified, the output images will be saved in a folder called `output` in the input directory. For pdf output, the default output path is `output.pdf` in the input directory for multiple images and `<IMG_NAME>.pdf` for a single image.

#### Use as python library

```python
from scandoc import scan, scan2pdf, multi_scan, multi_scan2pdf

img_path = "input.jpg"
output_path = "output.jpg"
scan(img_path, output_path=output_path, interactive_mode=False)
scan2pdf(input_path, output_path, interactive_mode=False)

img_dir = "input"
output_dir = "output"
multi_scan(img_dir, output_dir=output_dir, interactive_mode=False)
output_path = "output.pdf"
multi_scan2pdf(img_dir, output_path=output_path, interactive_mode=False)

```

## License

Licensed under [MIT License](LICENSE).

<a href="https://www.buymeacoffee.com/thomsan" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/default-yellow.png" alt="Buy Me A Coffee" height="41" width="174"></a>

## Credits

Code is based on:

- [danielgatis/docscan](https://github.com/danielgatis/docscan/tree/master)
- [endalk200
  /document-scanner](https://github.com/endalk200/document-scanner/tree/main)

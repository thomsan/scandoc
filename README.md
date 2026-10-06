# Scandoc

**A document scanning library, CLI and installable web app.**

The tool uses OpenCV to detect the document in the image and then uses a perspective transform to transform the document into a rectangle. It can output it as an image or a pdf. Multiple images are combined into a single pdf.

## Web app and standalone installation

Requires Python 3.12 and Node 24 to build from source:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-web.lock
.venv/bin/pip install -e '.[web,test]'
npm ci --prefix frontend
npm run build --prefix frontend
.venv/bin/scandoc serve
```

Open `http://127.0.0.1:8000`. Standalone mode binds to loopback and works without
Paperless. The existing CLI and library entrypoints remain available. Install
`.[desktop]` additionally for Matplotlib corner editing in the CLI.

The English interface accepts JPEG, PNG, WebP and TIFF, corrects perspective,
provides editable corners with a magnifier, rotates and cleans pages, and exports
multipage PDFs preserving each page's proportions. Choose a device download,
an administrator-configured server folder, authenticated Paperless ingestion,
or HTTPS WebDAV. Curved paper and blurred text restoration are outside its scope.

Hosted mode requires `SCANDOC_HOSTED=true`, `SCANDOC_ORIGIN=https://...`,
`SCANDOC_PAPERLESS_URL`, and a Fernet `SCANDOC_ENCRYPTION_KEY` provided outside Git.
The same image serves API and built frontend; hosted access uses Paperless accounts.
`SCANDOC_DATA_DIR` stores SQLite state, encrypted credentials and unfinished files.
One processing worker runs per instance. OpenAPI is at `/docs` and `/openapi.json`.

`SCANDOC_CONFIG_FILE` may point to a protected JSON file containing `destinations`:

```json
{"destinations":[{"id":"archive","name":"Local archive","kind":"folder","root":"/absolute/export/folder","default":true}]}
```

File entries override UI settings and are read-only. WebDAV entries use `url`,
`username` and `password`; protect this file like any other secret. Use
`SCANDOC_CA_FILE` for a trusted private CA. Ordinary users never choose server paths.
The configurable limits are `SCANDOC_MAX_PAGES=20`,
`SCANDOC_MAX_IMAGE_BYTES=26214400`, `SCANDOC_MAX_PIXELS=24000000`, and
`SCANDOC_MAX_DOCUMENT_BYTES=209715200`.

The Android PWA needs trusted HTTPS and a first online load. It retains unfinished
images, ordering, edits and metadata in account-separated IndexedDB indefinitely.
Manual editing works offline; automatic detection, previews, PDFs and delivery
resume after reconnection. Logout hides retained drafts. Storage errors are shown
instead of silently dropping pages. Device downloads retain drafts until deletion;
confirmed folder, WebDAV and Paperless delivery delete server and phone source files.
An uncertain Paperless upload must be reconciled before any resend.

Run `pytest tests` and `npm run test:e2e --prefix frontend` after building the UI.
Hosted browser tests use `SCANDOC_TEST_URL` and the disposable back-office fixture's
`.env`; never point them at a production archive. Native phone acceptance is separate.

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

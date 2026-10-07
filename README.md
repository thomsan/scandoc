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
`SCANDOC_DATA_DIR` stores SQLite state, encrypted Paperless tokens, connection metadata and retained document files. WebDAV passwords never enter this directory.
One processing worker runs per instance. OpenAPI is at `/docs` and `/openapi.json`.

`SCANDOC_CONFIG_FILE` may point to a protected JSON file containing `destinations`:

```json
{"destinations":[{"id":"archive","name":"Local archive","kind":"folder","root":"/absolute/export/folder","default":true}]}
```

File entries override administrator server-folder settings and are read-only.
Shared WebDAV entries are rejected: every user connects their own account from
Destination settings and chooses an existing remote folder. Use
`SCANDOC_CA_FILE` to add a trusted private CA alongside the standard HTTPS roots. Ordinary users never choose server paths.
WebDAV login forms support phone/password-manager autofill using username and
current-password fields. Use a personal app password where supported. Scandoc
keeps it only in session memory shared with its one processing worker; it is never
written into SQLite, files, browser storage or API responses. Logout/expiry clears
that session's credentials. Backend restart clears all WebDAV credentials while
preserving account metadata, selected folders and document sources. Sign in again
from Destination settings and retry interrupted uploads; uncertain outcomes are
reconciled against the existing destination file before resending. Sessions on
another device, even for the same user, require their own WebDAV login. Disconnect
removes saved account/destination metadata without deleting remote documents.
The phone password manager owns password persistence; Scandoc cannot guarantee
its autofill/save prompt on every browser, so verify it on the actual phone.

The configurable limits are `SCANDOC_MAX_PAGES=20`,
`SCANDOC_MAX_IMAGE_BYTES=26214400`, `SCANDOC_MAX_PIXELS=24000000`, and
`SCANDOC_MAX_DOCUMENT_BYTES=209715200`.

The Android PWA needs trusted HTTPS and a first online load. It retains unfinished
images, ordering, edits and metadata in account-separated IndexedDB indefinitely.
Manual editing works offline; automatic detection, previews, PDFs and delivery
resume after reconnection. Logout hides retained drafts. Storage errors are shown
instead of silently dropping pages. Completed downloads and deliveries move to **History**. Original images, edits
and PDFs remain in Scandoc until you explicitly delete the History item.
**Export again** sends the same document to another destination. **Delete all**
removes only your History items and Scandoc files; unfinished drafts and remote
Paperless/WebDAV/folder copies remain. Active or uncertain deliveries block deletion.
An uncertain Paperless upload must be reconciled before any resend.

The save form follows the selected destination. Paperless requires a document
type and accepts an optional **Description**, such as “Extension cables” for a
receipt. Its company archive owns OCR date extraction, title policy and filenames;
Scandoc attaches the dedicated Description custom field before ingestion and
confirms it before reporting delivery. The delivery receipt shows the resulting
archive filename. Download, folder and WebDAV exports require a document date
and Description and generate `YYYY-MM-DD DESCRIPTION.pdf`, with safe characters.
A conflicting folder/WebDAV name fails without overwriting or deleting the draft.
Paperless identifiers and credentials are never used for these direct exports.
Page sizing and administrator type creation remain under **More options**.

Older drafts preserve their entered Note/title as Description. Older API clients
can still submit `metadata.note` or `metadata.title` as an authored Paperless note;
their explicit export filenames remain supported. New clients send
`metadata.description` and, only for direct exports, `metadata.created`.

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

PDF pages are written sequentially to disk to bound memory. The API and worker share one image-processing slot; waiting uploads remain in spooled files. Destination transfers and verification use bounded streams. Color pages use JPEG quality 95 at the full corrected resolution; grayscale and document-cleanup pages use lossless compression. Hosted PDFs also respect the configured document byte limit. A failed PDF export preserves any existing output file.

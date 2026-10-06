import io
import sys
import numpy as np
import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader
from scandoc import scan, scan2pdf, multi_scan, multi_scan2pdf
from scandoc.scan import load_image, detect_corners, correct_image, write_pdf, validate_corners
from scandoc.main_cli import main_cli


def test_no_contours_and_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Image.new("RGB", (100, 160), "white").save("blank.png")
    result = scan("blank.png", "out.png")
    assert result.shape[:2] == (159, 99)
    scan2pdf("blank.png", "out.pdf")
    assert len(PdfReader("out.pdf").pages) == 1


def test_detect_and_warp_geometry():
    image = Image.new("RGB", (400, 500), "#222222")
    expected = [[70,50],[340,70],[310,450],[40,430]]
    ImageDraw.Draw(image).polygon([tuple(p) for p in expected], fill="white")
    actual = np.array(detect_corners(image))
    assert np.max(np.abs(actual-np.array(expected))) < 5
    corrected = correct_image(image, expected)
    assert corrected.width == 271
    assert corrected.height == 381


def test_exif_orientation():
    image = Image.new("RGB", (80, 120), "white")
    exif = image.getexif(); exif[274] = 6
    raw = io.BytesIO();image.save(raw,"JPEG",exif=exif);raw.seek(0)
    assert load_image(raw).size == (120, 80)


@pytest.mark.parametrize("corners", [[], [[0,0]]*4, [[0,0],[99,99],[99,0],[0,99]], [[0,0],[0,99],[99,99],[99,0]], [[-1,0],[99,0],[99,99],[0,99]], [[0,0],[99,0],[99,float('nan')],[0,99]]])
def test_reject_invalid_corners(corners):
    with pytest.raises(ValueError):validate_corners(corners,100,100)


def test_mixed_page_sizes_and_empty_batch(tmp_path):
    images=[Image.new("RGB",(120,600)),Image.new("RGB",(700,200))]
    path=tmp_path/"document.pdf";write_pdf(iter(images),path)
    pdf=PdfReader(path)
    assert float(pdf.pages[0].mediabox.width)/float(pdf.pages[0].mediabox.height) == pytest.approx(.2)
    assert float(pdf.pages[1].mediabox.width)/float(pdf.pages[1].mediabox.height) == pytest.approx(3.5)
    with pytest.raises(ValueError):write_pdf([],tmp_path/"empty.pdf")


def test_cli_explicit_folder(tmp_path,monkeypatch):
    source=tmp_path/"source";source.mkdir();Image.new("RGB",(100,150)).save(source/"one.png")
    output=tmp_path/"elsewhere"
    monkeypatch.setattr(sys,"argv",["scandoc","--images",str(source),"--output",str(output)])
    main_cli()
    assert (output/"one.png").exists()


def test_malformed_and_limits(tmp_path):
    with pytest.raises(ValueError):load_image(io.BytesIO(b"not an image"))
    raw=io.BytesIO();Image.new("RGB",(20,20)).save(raw,"PNG");raw.seek(0)
    with pytest.raises(ValueError):load_image(raw,max_pixels=100)
    with pytest.raises(ValueError):multi_scan(tmp_path)


def test_cleanup_rotation_and_a4(tmp_path):
    image=Image.new("RGB",(60,300),"white")
    for mode in ("color","grayscale","document"):
        result=correct_image(image,rotation=90,mode=mode)
        assert result.size == (299,59)
    write_pdf([image],tmp_path/"a4.pdf","a4")
    assert float(PdfReader(tmp_path/"a4.pdf").pages[0].mediabox.width)==pytest.approx(595.28)


@pytest.mark.parametrize('format', ['JPEG','PNG','WEBP','TIFF'])
def test_supported_formats_and_retained_text_region(format):
    image=Image.new('RGB',(200,400),'white')
    ImageDraw.Draw(image).rectangle((60,100,140,300),fill='black')
    raw=io.BytesIO();image.save(raw,format);raw.seek(0)
    normalized=load_image(raw)
    corrected=correct_image(normalized,[[0,0],[199,0],[199,399],[0,399]])
    assert np.asarray(corrected)[100:295,60:135].mean() < 8


def test_pdf_embedded_images_quality_order_and_byte_limit(tmp_path):
    color=Image.new('RGB',(200,400),'white')
    ImageDraw.Draw(color).rectangle((60,100,140,300),fill='black')
    gray=color.convert('L')
    path=tmp_path/'quality.pdf'
    write_pdf(iter([color,gray]),path)
    pages=PdfReader(path).pages
    assert pages[0].images[0].image.size == color.size
    assert np.asarray(pages[0].images[0].image)[105:295,65:135].mean() < 3
    assert np.array_equal(np.asarray(pages[1].images[0].image),np.asarray(gray))
    with pytest.raises(ValueError,match='document byte limit'):
        write_pdf([color],tmp_path/'too-large.pdf',max_bytes=100)
    assert not (tmp_path/'too-large.pdf').exists()

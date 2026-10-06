"""Names for exports that do not pass through an archive."""
from datetime import date
import re


def export_filename(created, description):
    if not isinstance(created, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", created):
        raise ValueError("Enter a document date")
    date.fromisoformat(created)
    if not isinstance(description, str) or not description.strip():
        raise ValueError("Enter a brief description")
    label = re.sub(r"[^\w .()-]", "-", description.strip(), flags=re.UNICODE)
    label = re.sub(r"\s+", " ", label)[:128].strip()
    # Linux limits path components by bytes, not characters.
    while len(label.encode("utf-8")) > 180:
        label = label[:-1]
    return f"{created} {label}.pdf"

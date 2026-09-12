"""Deterministically extract paragraph text from the canonical DOCX specification."""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path
from xml.etree import ElementTree


EXTRACTION_METHOD = "docx-paragraph-text-v1"
_WORD_NAMESPACE = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def extract_docx_paragraph_text(source: Path) -> bytes:
    """Return nonempty paragraph text in document order as UTF-8 with one final LF."""
    with zipfile.ZipFile(source) as package:
        document = ElementTree.fromstring(package.read("word/document.xml"))
    paragraphs = []
    for paragraph in document.findall(".//w:p", _WORD_NAMESPACE):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", _WORD_NAMESPACE))
        if text.strip():
            paragraphs.append(text)
    return ("\n".join(paragraphs) + "\n").encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    arguments.output.write_bytes(extract_docx_paragraph_text(arguments.source))


if __name__ == "__main__":
    main()

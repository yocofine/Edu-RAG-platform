"""Utilities for decoding user supplied text files safely.

Uploaded teaching materials commonly come from Windows applications and may be
encoded as UTF-8 (with or without BOM) or GB18030.  Decoding through this module
keeps preview and indexing on the same canonical UTF-8 text.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DecodedText:
    text: str
    encoding: str
    replacement_ratio: float
    control_ratio: float

    @property
    def healthy(self) -> bool:
        return bool(self.text.strip()) and self.replacement_ratio < 0.01 and self.control_ratio < 0.02


def decode_bytes(data: bytes) -> DecodedText:
    """Decode bytes using BOM-aware UTF-8, UTF-8 and Chinese Windows encodings."""
    # UTF-16 is common for files exported by older Windows/Office tools. Only
    # try its BOM-aware codec when a BOM is actually present; otherwise an
    # even-length GB18030 byte stream could be misread as UTF-16 garbage.
    candidates = ["utf-8-sig", "utf-8"]
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        candidates.append("utf-16")
    candidates.extend(("gb18030", "gbk"))
    text = None
    used = candidates[-1]
    for encoding in candidates:
        try:
            text = data.decode(encoding)
            used = encoding
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = data.decode("utf-8", errors="replace")
        used = "utf-8-replace"
    length = max(len(text), 1)
    replacements = text.count("\ufffd")
    controls = sum(1 for c in text if ord(c) < 32 and c not in "\r\n\t")
    return DecodedText(text, used, replacements / length, controls / length)


def quality_report(text: str) -> dict[str, object]:
    """Return a small serialisable report used by ingestion quality gates."""
    length = max(len(text), 1)
    replacement_ratio = text.count("\ufffd") / length
    control_ratio = sum(1 for c in text if ord(c) < 32 and c not in "\r\n\t") / length
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    suspicious = sum(1 for c in text if c in "ÃÂ�")
    report = {
        "characters": len(text),
        "replacement_ratio": replacement_ratio,
        "control_ratio": control_ratio,
        "cjk_characters": cjk,
        "suspicious_characters": suspicious,
    }
    report["healthy"] = bool(text.strip()) and replacement_ratio < 0.01 and control_ratio < 0.02 and suspicious / length < 0.03
    return report


# Compatibility hook: older loaders in the project call ``Path.read_text`` with
# a hard-coded utf-8-sig encoding.  Install a narrow wrapper when this module is
# imported so those paths transparently support GB18030/GBK files as well.
if not getattr(Path, "_edurag_encoding_patch", False):
    _path_read_text = Path.read_text

    def _read_text_compatible(self: Path, encoding=None, errors=None, newline=None):
        if self.suffix.lower() in {".txt", ".md", ".markdown", ".csv", ".tsv"}:
            return decode_bytes(self.read_bytes()).text
        # Python 3.12's Path.read_text() does not accept ``newline`` (it was
        # added in a later Python release).  Do not forward it to the original
        # method or imports of package METADATA files (for example pymilvus)
        # will fail before indexing starts.
        return _path_read_text(self, encoding=encoding, errors=errors)

    Path.read_text = _read_text_compatible
    Path._edurag_encoding_patch = True

try:  # pandas is optional for deployments that only ingest PDF/TXT files.
    import pandas as _pd
    from io import StringIO as _StringIO

    if not getattr(_pd, "_edurag_encoding_patch", False):
        _pd_read_csv = _pd.read_csv

        def _read_csv_compatible(filepath_or_buffer, *args, **kwargs):
            if isinstance(filepath_or_buffer, (str, Path)):
                candidate = Path(filepath_or_buffer)
                if candidate.exists() and candidate.suffix.lower() in {".csv", ".tsv"}:
                    decoded = decode_bytes(candidate.read_bytes())
                    return _pd_read_csv(_StringIO(decoded.text), *args, **kwargs)
            return _pd_read_csv(filepath_or_buffer, *args, **kwargs)

        _pd.read_csv = _read_csv_compatible
        _pd._edurag_encoding_patch = True
except Exception:
    pass

"""Read visible metadata from extracted Basara 4 add-on folders."""
from pathlib import Path
import struct

EXPECTED_TITLE_ID = "CUSA01159"
MAX_ENTRIES = 4096


def _parse_sfo(data):
    if len(data) < 20:
        raise ValueError("param.sfo is truncated")
    magic, _version, key_offset, data_offset, count = struct.unpack_from("<4sIIII", data)
    if magic != b"\0PSF" or count > MAX_ENTRIES:
        raise ValueError("invalid param.sfo")
    index_end = 20 + count * 16
    if not index_end <= key_offset <= data_offset <= len(data):
        raise ValueError("invalid param.sfo offsets")

    values = {}
    for index in range(count):
        key_at, _format, length, _maximum, value_at = struct.unpack_from(
            "<HHIII", data, 20 + index * 16)
        key_start = key_offset + key_at
        key_end = data.find(b"\0", key_start, data_offset)
        value_start = data_offset + value_at
        value_end = value_start + length
        if key_start >= data_offset or key_end < 0 or value_end > len(data):
            raise ValueError("param.sfo entry is out of bounds")
        key = data[key_start:key_end].decode("utf-8", "replace")
        value = data[value_start:value_end].split(b"\0", 1)[0].decode("utf-8", "replace")
        values[key] = value
    return values


def read_extracted_dlc(path):
    """Return user-visible metadata for a valid extracted add-on directory."""
    path = Path(path)
    if not path.is_dir():
        raise ValueError("not a DLC folder")
    values = _parse_sfo((path / "sce_sys" / "param.sfo").read_bytes())
    content_id = values.get("CONTENT_ID", "")
    title_id = values.get("TITLE_ID", "")
    category = values.get("CATEGORY", "")
    if title_id != EXPECTED_TITLE_ID:
        raise ValueError(f"folder is for {title_id or 'an unknown title'}, not {EXPECTED_TITLE_ID}")
    if category != "ac" or len(content_id) <= 20:
        raise ValueError("folder metadata does not identify a Basara 4 add-on")
    label = content_id[20:]
    try:
        encoded_label = label.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("DLC entitlement label is not ASCII") from error
    if len(encoded_label) != 16 or any(
            not (char.isalnum() or char == "_") for char in label):
        raise ValueError("invalid DLC entitlement label in CONTENT_ID")
    if path.name != label:
        raise ValueError(f"folder name must match its entitlement label ({label})")
    return {
        "title": values.get("TITLE") or label,
        "content_id": content_id,
        "title_id": title_id,
        "label": label,
    }


def scan_extracted_dlc(folder):
    """Return sorted (path, metadata, error) records for extracted DLC folders."""
    if folder is None or not str(folder).strip():
        return []
    folder = Path(folder)
    if not folder.is_dir():
        return [(folder, None, "DLC folder does not exist")]
    records = []
    for path in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_dir() or not (path / "sce_sys" / "param.sfo").is_file():
            continue
        try:
            records.append((path, read_extracted_dlc(path), ""))
        except (OSError, ValueError, struct.error) as error:
            records.append((path, None, str(error)))
    return records

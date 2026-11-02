"""GoldHEN cheat data for Sengoku Basara 4 Sumeragi, CUSA01159 update 01.02."""
import struct
from pathlib import Path

from basara4_dlc import _parse_sfo

BASE_ADDRESS = 0x400000
SUPPORTED_VERSION = "01.02"
AUTHOR = "Q8Despair"
SOURCE_URL = "https://github.com/GoldHEN/GoldHEN_Cheat_Repository/blob/main/json/CUSA01159_01.02.json"

CHEATS = {
    "gold": {
        "name": "Gold Won't Decrease",
        "writes": (
            (0x16BF800, "B89999990990E98CA226FF", "0000000000000000000000"),
            (0x929A91, "E96A5DD90090", "8988DC000000"),
        ),
    },
    "special_coin": {
        "name": "Special coin x99999 in battle",
        "writes": (
            (0x12BF9E0, "C787E80200003F420F00E9C888D9FE", "000000000000000000000000000000"),
            (0x582B0, "E92B7726019090", "F001B7E8020000"),
        ),
    },
    "growth_points": {
        "name": "Growth points won't decrease x10",
        "writes": (
            (0x12BF800, "B89999990990E9D03024FF", "0000000000000000000000"),
            (0x5028D4, "E927CFDB009090", "412985B4060000"),
        ),
    },
    "weapon_points": {
        "name": "Weapon points won't decrease x10",
        "writes": (
            (0x12BF80D, "B89999090090E9B4172AFF", "0000000000000000000000"),
            (0x560FC6, "E942E8D50090", "8981E4000000"),
        ),
    },
    "weapon_rank_points": {
        "name": "Weapon rank points won't decrease",
        "writes": (
            (0x12BF80D, "B89900000090E98FEE29FF", "0000000000000000000000"),
            (0x55E6A1, "E96711D60090", "8990E8000000"),
        ),
    },
}


def normalize_selection(cheat_ids):
    if not isinstance(cheat_ids, (list, tuple)):
        raise ValueError("Cheat selection must be a list of cheat IDs")
    selected = list(cheat_ids)
    if any(not isinstance(cheat_id, str) for cheat_id in selected):
        raise ValueError("Cheat IDs must be text")
    if len(selected) != len(set(selected)):
        raise ValueError("Cheat selection contains duplicates")
    unknown = sorted(set(selected) - set(CHEATS))
    if unknown:
        raise ValueError(f"Unknown cheat selection: {', '.join(unknown)}")
    return selected


def validate_selection(cheat_ids):
    return normalize_selection(cheat_ids)


def compile_cheats(cheat_ids, game_version, image, load_segments):
    selected = validate_selection(cheat_ids)
    if not selected:
        return []
    if game_version != SUPPORTED_VERSION:
        raise ValueError(
            f"These cheats require game update {SUPPORTED_VERSION}; detected {game_version or 'unknown'}")
    caves, hooks = [], []
    for cheat_id in selected:
        for source_offset, encoded, original in CHEATS[cheat_id]["writes"]:
            expected = bytes.fromhex(original)
            candidates = {source_offset}
            if source_offset >= BASE_ADDRESS:
                candidates.add(source_offset - BASE_ADDRESS)
            matches = [
                offset for offset in candidates
                if offset + len(expected) <= len(image)
                and image[offset:offset + len(expected)] == expected
            ]
            if len(matches) != 1:
                raise ValueError(
                    f"Cheat '{CHEATS[cheat_id]['name']}' does not match this game executable "
                    f"at {source_offset:#x}")
            entry = (matches[0], bytes.fromhex(encoded), cheat_id)
            (caves if not any(expected) else hooks).append(entry)

    if len(caves) != len(selected) or len(hooks) != len(selected):
        raise ValueError("Cheat data must contain one code cave and one hook per cheat")

    cave_slots = _allocate_caves(image, load_segments, [len(code) for _, code, _ in caves])
    writes = []
    for (source_cave, code, cheat_id), new_cave in zip(caves, cave_slots):
        if len(code) < 5 or code[-5] != 0xE9:
            raise ValueError(f"Cheat '{cheat_id}' has an invalid code-cave jump")
        old_displacement, = struct.unpack("<i", code[-4:])
        return_address = source_cave + len(code) + old_displacement
        new_displacement = return_address - (new_cave + len(code))
        if not -(1 << 31) <= new_displacement < (1 << 31):
            raise ValueError(f"Cheat '{cheat_id}' trampoline is out of range")
        relocated = code[:-4] + struct.pack("<i", new_displacement)
        writes.append((new_cave, relocated))

    for hook, code, cheat_id in hooks:
        cave = next(
            slot for (source_cave, _cave_code, cave_id), slot in zip(caves, cave_slots)
            if cave_id == cheat_id)
        if len(code) < 5 or code[0] != 0xE9:
            raise ValueError(f"Cheat '{cheat_id}' has an invalid hook")
        displacement = cave - (hook + 5)
        if not -(1 << 31) <= displacement < (1 << 31):
            raise ValueError(f"Cheat '{cheat_id}' hook is out of range")
        hook_code = b"\xE9" + struct.pack("<i", displacement) + b"\x90" * (len(code) - 5)
        writes.append((hook, hook_code))

    writes.sort(key=lambda write: write[0])
    for previous, current in zip(writes, writes[1:]):
        if current[0] < previous[0] + len(previous[1]):
            raise ValueError(
                "The selected cheat patches overlap")
    return writes


def _allocate_caves(image, load_segments, lengths):
    page_size = 0x1000
    protected = [
        (address & ~(page_size - 1),
         (address + size + page_size - 1) & ~(page_size - 1), flags)
        for address, size, flags in load_segments
    ]
    candidates = []
    for page_start in range(0, len(image) - page_size + 1, page_size):
        page_end = page_start + page_size
        if any(page_start < end and start < page_end for start, end, _ in protected):
            continue
        executable_before = any(
            flags & 1 and end <= page_start and page_start - end <= 16 * page_size
            for _start, end, flags in protected)
        segment_after = any(
            start >= page_end and start - page_end <= 16 * page_size
            for start, _end, _flags in protected)
        if executable_before and segment_after:
            candidates.append(page_start)
    if not candidates:
        raise ValueError("No unused executable page is available for cheat trampolines")

    page_start = candidates[0]
    slots, cursor = [], page_start
    for length in lengths:
        cursor = (cursor + 15) & ~15
        if cursor + length > page_start + page_size:
            raise ValueError("Cheat trampolines do not fit in the available executable page")
        slots.append(cursor)
        cursor += length
    return slots


def game_version(game_dir):
    sfo_path = Path(game_dir) / "sce_sys" / "param.sfo"
    try:
        values = _parse_sfo(sfo_path.read_bytes())
    except (OSError, ValueError, struct.error) as error:
        raise ValueError(f"Cannot verify cheat compatibility from {sfo_path}: {error}") from error
    title_id = values.get("TITLE_ID")
    if title_id != "CUSA01159":
        raise ValueError(f"These cheats require CUSA01159; detected {title_id or 'unknown title'}")
    return values.get("APP_VER", "")

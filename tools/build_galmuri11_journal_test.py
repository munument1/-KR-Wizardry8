from pathlib import Path
import hashlib
import json
import shutil
import struct
import zipfile

ROOT = Path("work")
OUT = Path("out")
OUT.mkdir(exist_ok=True)
HANGUL_START = 154
HANGUL_COUNT = 1196
HANGUL_END = HANGUL_START + HANGUL_COUNT
ASCII_FRAME_COUNT = 93  # A-Z, a-z, 0-9, punctuation, space; excludes ` and ~
MAP_PREFIX_COUNT = 154
INK = 44


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_zip(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    with zipfile.ZipFile(src, "r") as zf:
        zf.extractall(dst)


def parse_bdf(path: Path):
    lines = path.read_text("utf-8", errors="strict").splitlines()
    glyphs = {}
    props = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("FONTBOUNDINGBOX "):
            props["FONTBOUNDINGBOX"] = tuple(map(int, line.split()[1:5]))
        elif line.startswith("FONT_ASCENT "):
            props["FONT_ASCENT"] = int(line.split()[1])
        elif line.startswith("FONT_DESCENT "):
            props["FONT_DESCENT"] = int(line.split()[1])
        elif line.startswith("STARTCHAR "):
            enc = None
            bbx = None
            dwidth = None
            bitmap = None
            i += 1
            while i < len(lines) and not lines[i].startswith("ENDCHAR"):
                s = lines[i]
                if s.startswith("ENCODING "):
                    enc = int(s.split()[1])
                elif s.startswith("DWIDTH "):
                    dwidth = int(s.split()[1])
                elif s.startswith("BBX "):
                    bbx = tuple(map(int, s.split()[1:5]))
                elif s == "BITMAP":
                    if bbx is None:
                        raise RuntimeError("BITMAP before BBX")
                    w, h, _xoff, _yoff = bbx
                    bitmap = []
                    for r in range(h):
                        hx = lines[i + 1 + r].strip()
                        total_bits = len(hx) * 4
                        val = int(hx, 16) if hx else 0
                        row = []
                        for x in range(w):
                            row.append((val >> (total_bits - 1 - x)) & 1)
                        bitmap.append(row)
                    i += h
                i += 1
            if enc is not None and bbx is not None and bitmap is not None:
                glyphs[enc] = {"bbx": bbx, "dwidth": dwidth, "bitmap": bitmap}
            continue
        i += 1
    return props, glyphs


def parse_sti(raw: bytes):
    if raw[:4] != b"STCI":
        raise RuntimeError("journal font is not STCI")
    n = struct.unpack_from("<H", raw, 28)[0]
    if n != 3299:
        raise RuntimeError(f"unexpected frame count {n}")
    meta_off = 64 + 256 * 3
    data_start = meta_off + n * 16
    recs = [
        list(struct.unpack_from("<IIhhHH", raw, meta_off + i * 16))
        for i in range(n)
    ]
    return n, meta_off, data_start, recs


def frame_comp(raw: bytes, parsed, idx: int) -> bytes:
    _, _, data_start, recs = parsed
    off, length = recs[idx][0], recs[idx][1]
    return raw[data_start + off : data_start + off + length]


def decode_frame(raw: bytes, parsed, idx: int):
    _, _, _, recs = parsed
    comp = frame_comp(raw, parsed, idx)
    _, _, _, _, h, w = recs[idx]
    rows = []
    p = 0
    for y in range(h):
        row = []
        while True:
            if p >= len(comp):
                raise RuntimeError(f"frame {idx} truncated at row {y}")
            cmd = comp[p]
            p += 1
            if cmd == 0:
                break
            count = cmd & 0x7F
            if not count:
                raise RuntimeError(f"frame {idx} invalid zero run")
            if cmd & 0x80:
                row.extend([0] * count)
            else:
                row.extend(comp[p : p + count])
                p += count
        if len(row) != w:
            raise RuntimeError(f"frame {idx} row {y}: width {len(row)} != {w}")
        rows.append(row)
    if p != len(comp):
        raise RuntimeError(f"frame {idx}: trailing ETRLE bytes {len(comp) - p}")
    return rows


def etrle_encode(rows) -> bytes:
    out = bytearray()
    for row in rows:
        x = 0
        while x < len(row):
            if row[x] == 0:
                j = x
                while j < len(row) and row[j] == 0 and j - x < 127:
                    j += 1
                out.append(0x80 | (j - x))
                x = j
            else:
                j = x
                while j < len(row) and row[j] != 0 and j - x < 127:
                    j += 1
                out.append(j - x)
                out.extend(row[x:j])
                x = j
        out.append(0)
    return bytes(out)


def rebuild_sti(raw: bytes, replacements) -> bytes:
    n, meta_off, data_start, recs = parse_sti(raw)
    comps = [
        etrle_encode(replacements[i])
        if i in replacements
        else frame_comp(raw, (n, meta_off, data_start, recs), i)
        for i in range(n)
    ]
    new = bytearray(raw[:data_start])
    data_off = 0
    for idx, (rec, comp) in enumerate(zip(recs, comps)):
        rec[0] = data_off
        rec[1] = len(comp)
        struct.pack_into("<IIhhHH", new, meta_off + idx * 16, *rec)
        data_off += len(comp)
    new.extend(b"".join(comps))
    struct.pack_into("<I", new, 8, data_off)
    return bytes(new)


def glyph_coords(glyph):
    w, h, xoff, yoff = glyph["bbx"]
    pts = set()
    for r, row in enumerate(glyph["bitmap"]):
        y_bdf = yoff + h - 1 - r
        for c, bit in enumerate(row):
            if bit:
                pts.add((xoff + c, y_bdf))
    return pts


def foreground_coords(raw: bytes, parsed, idx: int):
    rows = decode_frame(raw, parsed, idx)
    return {(x, y) for y, row in enumerate(rows) for x, v in enumerate(row) if v == INK}


def find_hangul_map(exe: bytes):
    for off in range(0, len(exe) - HANGUL_COUNT * 2, 2):
        if exe[off : off + 2] != b"\x00\xac":
            continue
        vals = list(struct.unpack_from("<" + "H" * HANGUL_COUNT, exe, off))
        if all(0xAC00 <= cp <= 0xD7A3 for cp in vals) and all(
            vals[i] < vals[i + 1] for i in range(len(vals) - 1)
        ):
            prefix_off = off - MAP_PREFIX_COUNT * 2
            prefix = list(struct.unpack_from("<" + "H" * MAP_PREFIX_COUNT, exe, prefix_off))
            return off, prefix, vals
    raise RuntimeError("1,196-glyph Hangul Unicode map not found")


def infer_galmuri9_alignment(font_raw: bytes, parsed, cps, g9):
    actual = [
        foreground_coords(font_raw, parsed, HANGUL_START + i)
        for i in range(HANGUL_COUNT)
    ]
    best = None
    for x_origin in range(-3, 6):
        for baseline in range(8, 25):
            exact = 0
            pixel_delta = 0
            present = 0
            for i, cp in enumerate(cps):
                glyph = g9.get(cp)
                if glyph is None:
                    continue
                present += 1
                predicted = {
                    (x_origin + x, baseline - y) for x, y in glyph_coords(glyph)
                }
                if predicted == actual[i]:
                    exact += 1
                pixel_delta += len(predicted ^ actual[i])
            score = (exact, -pixel_delta)
            if best is None or score > best[0]:
                best = (score, x_origin, baseline, present, pixel_delta)
    return best


def build_hangul_rows(cps, g11, x_origin: int, baseline: int):
    replacements = {}
    clipped = []
    missing = []
    for i, cp in enumerate(cps):
        glyph = g11.get(cp)
        if glyph is None:
            missing.append(cp)
            continue
        rows = [[0] * 13 for _ in range(28)]
        for x, y in glyph_coords(glyph):
            sx = x_origin + x
            sy = baseline - y
            if not (0 <= sx < 13 and 0 <= sy < 28):
                clipped.append((cp, sx, sy))
            else:
                rows[sy][sx] = INK
        replacements[HANGUL_START + i] = rows
    if missing:
        raise RuntimeError(f"Galmuri11 missing {len(missing)} Hangul glyphs")
    if clipped:
        raise RuntimeError(f"Galmuri11 Hangul clipped pixels: {clipped[:10]}")
    return replacements


def build_ascii_rows(prefix_cps, g11, parsed, baseline: int):
    # Frames 0..92 are exactly the printable ASCII map used by journal_font.sti,
    # except backtick and tilde. Preserve original frame widths/spacing, but
    # replace antialiased multi-index pixels with monochrome Galmuri11 index 44.
    _, _, _, recs = parsed
    ascii_cps = prefix_cps[:ASCII_FRAME_COUNT]
    expected = set(range(0x20, 0x7F)) - {0x60, 0x7E}
    if set(ascii_cps) != expected or len(set(ascii_cps)) != ASCII_FRAME_COUNT:
        raise RuntimeError("unexpected journal ASCII frame map")
    replacements = {}
    clipped = []
    for idx, cp in enumerate(ascii_cps):
        _off, _length, _xo, _yo, h, w = recs[idx]
        rows = [[0] * w for _ in range(h)]
        if cp != 0x20:
            glyph = g11.get(cp)
            if glyph is None:
                raise RuntimeError(f"Galmuri11 missing ASCII U+{cp:04X}")
            dwidth = glyph["dwidth"] or w
            x_shift = max(0, (w - dwidth) // 2)
            for x, y in glyph_coords(glyph):
                sx = x_shift + x
                sy = baseline - y
                if not (0 <= sx < w and 0 <= sy < h):
                    clipped.append((idx, cp, sx, sy, w, h))
                else:
                    rows[sy][sx] = INK
        replacements[idx] = rows
    if clipped:
        preview = ", ".join(
            f"frame={i} U+{cp:04X}@{x},{y}/{w}x{h}"
            for i, cp, x, y, w, h in clipped[:10]
        )
        raise RuntimeError(f"Galmuri11 ASCII clipped {len(clipped)} pixels: {preview}")
    return replacements, ascii_cps


def replace_mook_with_mukeu(data: bytes):
    raw = bytearray(data)
    pat = "Mook".encode("utf-16le")
    repl = "무크".encode("utf-16le")
    positions = []
    start = 0
    while True:
        pos = bytes(raw).find(pat, start)
        if pos < 0:
            break
        if pos % 2 == 0:
            s = pos
            while s >= 2 and raw[s - 2 : s] != b"\x00\x00":
                s -= 2
            e = pos
            while e + 1 < len(raw) and raw[e : e + 2] != b"\x00\x00":
                e += 2
            text = bytes(raw[s:e]).decode("utf-16le", errors="ignore")
            if any(0xAC00 <= ord(ch) <= 0xD7A3 for ch in text):
                positions.append(pos)
        start = pos + 2
    if len(positions) != 17:
        raise RuntimeError(f"expected 17 Korean-journal Mook occurrences, found {len(positions)}")
    for pos in reversed(positions):
        e = pos
        while e + 1 < len(raw) and raw[e : e + 2] != b"\x00\x00":
            e += 2
        tail = bytes(raw[pos + len(pat) : e])
        new_text = repl + tail
        old_len = e - pos
        if len(new_text) > old_len:
            raise RuntimeError("replacement grew beyond field")
        raw[pos : pos + len(new_text)] = new_text
        raw[pos + len(new_text) : e] = b"\x00" * (old_len - len(new_text))
    bb = bytes(raw)
    if bb.count(repl) != 17:
        raise RuntimeError(f"expected 17 '무크' occurrences, got {bb.count(repl)}")
    return bb, positions


def clean_root_text_files(root: Path) -> None:
    # Keep game CREDITS and font license under subdirectories; remove packaging clutter.
    for p in root.glob("*.txt"):
        p.unlink()
    for p in root.glob("*.TXT"):
        p.unlink()
    for p in root.glob("README*.md"):
        p.unlink()


def zip_tree(root: Path, outzip: Path) -> None:
    with zipfile.ZipFile(outzip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(root).as_posix())


def main() -> None:
    p128 = ROOT / "p128"
    p124 = ROOT / "p124"
    extract_zip(ROOT / "base128.zip", p128)
    extract_zip(ROOT / "base124.zip", p124)

    f128 = (p128 / "Data/JOURNAL/journal_font.sti").read_bytes()
    f124 = (p124 / "Data/JOURNAL/journal_font.sti").read_bytes()
    fact128 = (p128 / "Data/DATABASES/FACT.DBS").read_bytes()
    fact124 = (p124 / "Data/DATABASES/FACT.DBS").read_bytes()
    if f128 != f124 or fact128 != fact124:
        raise RuntimeError("v0.2.8 1.24/1.28 shared journal data differ unexpectedly")

    exe = (p128 / "Wiz8_v128.exe").read_bytes()
    map_off, prefix_cps, hangul_cps = find_hangul_map(exe)
    print(f"map offset=0x{map_off:X}; ASCII frames=0-{ASCII_FRAME_COUNT-1}; Hangul={len(hangul_cps)}")

    props9, g9 = parse_bdf(ROOT / "Galmuri9.bdf")
    props11, g11 = parse_bdf(ROOT / "Galmuri11.bdf")
    parsed = parse_sti(f128)
    best = infer_galmuri9_alignment(f128, parsed, hangul_cps, g9)
    (exact, _neg_delta), x_origin, baseline, present, pixel_delta = best
    print(f"Galmuri9 alignment x={x_origin}, baseline={baseline}, exact={exact}/{present}, delta={pixel_delta}")
    if exact < 1000:
        raise RuntimeError("could not infer existing Hangul baseline reliably")

    replacements = build_hangul_rows(hangul_cps, g11, x_origin, baseline)
    ascii_repl, ascii_cps = build_ascii_rows(prefix_cps, g11, parsed, baseline)
    replacements.update(ascii_repl)
    new_font = rebuild_sti(f128, replacements)
    new_parsed = parse_sti(new_font)

    for idx in range(3299):
        decode_frame(new_font, new_parsed, idx)

    # All visible ASCII and Hangul pixels must be monochrome index 44; this removes
    # the old antialias/shadow palette values that produced green contamination.
    for idx in list(range(ASCII_FRAME_COUNT)) + list(range(HANGUL_START, HANGUL_END)):
        rows = decode_frame(new_font, new_parsed, idx)
        used = {v for row in rows for v in row if v}
        if used - {INK}:
            raise RuntimeError(f"target frame {idx} still has palette values {sorted(used)}")

    target = set(range(ASCII_FRAME_COUNT)) | set(range(HANGUL_START, HANGUL_END))
    changed = {
        i for i in range(3299)
        if frame_comp(new_font, new_parsed, i) != frame_comp(f128, parsed, i)
    }
    if not (set(range(ASCII_FRAME_COUNT - 1)) | set(range(HANGUL_START, HANGUL_END))).issubset(changed):
        raise RuntimeError("not all visible ASCII/Hangul frames changed as expected")
    if changed - target:
        raise RuntimeError(f"non-target frames changed: {sorted(changed-target)[:20]}")

    # Extended Latin placeholders 93..148 and private FFF0..FFF4 frames 149..153
    # remain byte-identical, as do all frames after the Hangul range.
    untouched = list(range(ASCII_FRAME_COUNT, HANGUL_START)) + list(range(HANGUL_END, 3299))
    for idx in untouched:
        if frame_comp(new_font, new_parsed, idx) != frame_comp(f128, parsed, idx):
            raise RuntimeError(f"untouched frame changed: {idx}")

    if new_font[64 : 64 + 768] != f128[64 : 64 + 768]:
        raise RuntimeError("palette changed")
    _, _, _, old_recs = parsed
    _, _, _, new_recs = new_parsed
    for i in range(3299):
        if old_recs[i][2:] != new_recs[i][2:]:
            raise RuntimeError(f"frame geometry changed at {i}")

    new_fact, mook_positions = replace_mook_with_mukeu(fact128)
    print("Mook->무크 occurrences:", len(mook_positions))

    for root, label in [(p128, "1.28"), (p124, "1.24")]:
        (root / "Data/JOURNAL/journal_font.sti").write_bytes(new_font)
        (root / "Data/DATABASES/FACT.DBS").write_bytes(new_fact)
        clean_root_text_files(root)
        outzip = OUT / f"Wizardry8_KoreanPatch_{label}_JournalMonoGalmuri11_v0.2.9-test2.zip"
        zip_tree(root, outzip)
        print(label, outzip, outzip.stat().st_size, sha256_bytes(outzip.read_bytes()))

    if (p128 / "Data/JOURNAL/journal_font.sti").read_bytes() != (p124 / "Data/JOURNAL/journal_font.sti").read_bytes():
        raise RuntimeError("final journal fonts differ")
    if (p128 / "Data/DATABASES/FACT.DBS").read_bytes() != (p124 / "Data/DATABASES/FACT.DBS").read_bytes():
        raise RuntimeError("final FACT.DBS differ")

    report = {
        "base": "v0.2.8",
        "font": "Galmuri11",
        "baseline": baseline,
        "ink_palette_index": INK,
        "shadow": False,
        "ascii_frames_rebuilt": ASCII_FRAME_COUNT,
        "ascii_codepoints": [f"U+{cp:04X}" for cp in ascii_cps],
        "hangul_frames_rebuilt": HANGUL_COUNT,
        "changed_frames": len(changed),
        "mook_to_mukeu_occurrences": len(mook_positions),
        "journal_font_sha256": sha256_bytes(new_font),
        "fact_sha256": sha256_bytes(new_fact),
        "galmuri9_probe": {"exact": exact, "present": present, "pixel_delta": pixel_delta, "props": props9},
        "galmuri11_props": props11,
    }
    (OUT / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

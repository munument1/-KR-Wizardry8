#!/usr/bin/env python3
import argparse
import hashlib
from pathlib import Path
import struct

HANGUL_SLOT_BASE = 154
HANGUL_COUNT = 2350
HEADER_SIZE = 64
PALETTE_SIZE = 256 * 3
DESC_SIZE = 16
FRAME_TOP = 8


def ksc5601_hangul():
    chars = []
    for lead in range(0xB0, 0xC9):
        for trail in range(0xA1, 0xFF):
            chars.append(bytes((lead, trail)).decode('euc_kr'))
    assert len(chars) == HANGUL_COUNT
    return chars


def parse_bdf(path, wanted):
    glyphs = {}
    enc = None
    bbx = None
    bitmap = None
    rows = []
    with open(path, 'r', encoding='utf-8', errors='strict') as f:
        for raw in f:
            line = raw.rstrip('\r\n')
            if line.startswith('STARTCHAR '):
                enc = None; bbx = None; bitmap = None; rows = []
            elif line.startswith('ENCODING '):
                enc = int(line.split()[1])
            elif line.startswith('BBX '):
                p = line.split()
                bbx = tuple(map(int, p[1:5]))
            elif line == 'BITMAP':
                bitmap = True
                rows = []
            elif line == 'ENDCHAR':
                if enc in wanted and bbx is not None and bitmap:
                    w, h, xo, yo = bbx
                    if len(rows) != h:
                        raise ValueError(f'BDF U+{enc:04X}: expected {h} rows, got {len(rows)}')
                    pixels = []
                    bit_width = ((w + 7) // 8) * 8
                    for hx in rows:
                        val = int(hx, 16) if hx else 0
                        pixels.append([1 if (val & (1 << (bit_width - 1 - x))) else 0 for x in range(w)])
                    glyphs[enc] = (w, h, xo, yo, pixels)
                bitmap = None
            elif bitmap:
                rows.append(line.strip())
    missing = wanted - set(glyphs)
    if missing:
        sample = ', '.join(f'U+{x:04X}' for x in sorted(missing)[:20])
        raise ValueError(f'Missing {len(missing)} glyphs in BDF: {sample}')
    return glyphs


def sti_layout(buf):
    if buf[:4] != b'STCI':
        raise ValueError('not STCI')
    count = struct.unpack_from('<H', buf, 28)[0]
    table = HEADER_SIZE + PALETTE_SIZE
    data0 = table + count * DESC_SIZE
    return count, table, data0


def decode_stream(stream, h, w):
    out = []
    pos = 0
    for _ in range(h):
        row = []
        while True:
            if pos >= len(stream):
                raise ValueError('ETRLE stream ended before row terminator')
            c = stream[pos]; pos += 1
            if c == 0:
                break
            n = c & 0x7F
            if n == 0:
                raise ValueError('zero-length ETRLE run')
            if c & 0x80:
                row.extend([0] * n)
            else:
                if pos + n > len(stream):
                    raise ValueError('ETRLE literal overrun')
                row.extend(stream[pos:pos+n]); pos += n
            if len(row) > w:
                raise ValueError(f'ETRLE row overrun {len(row)}>{w}')
        if len(row) < w:
            row.extend([0] * (w - len(row)))
        out.append(row)
    if pos != len(stream):
        raise ValueError(f'ETRLE trailing bytes: {len(stream)-pos}')
    return out


def encode_stream(canvas):
    out = bytearray()
    for row in canvas:
        x = 0
        w = len(row)
        while x < w:
            is_trans = row[x] == 0
            j = x + 1
            while j < w and (row[j] == 0) == is_trans and j - x < 127:
                j += 1
            n = j - x
            if is_trans:
                out.append(0x80 | n)
            else:
                out.append(n)
                out.extend(row[x:j])
            x = j
        out.append(0)
    return bytes(out)


def frame_desc(buf, table, i):
    return struct.unpack_from('<IIhhHH', buf, table + i * DESC_SIZE)


def pick_foreground_index(buf, table, data0):
    off, ln, xo, yo, h, w = frame_desc(buf, table, HANGUL_SLOT_BASE)
    pix = decode_stream(buf[data0+off:data0+off+ln], h, w)
    used = sorted({v for row in pix for v in row if v})
    if not used:
        raise ValueError('reference Hangul glyph is blank')
    palette = buf[HEADER_SIZE:HEADER_SIZE+PALETTE_SIZE]
    def brightness(idx):
        r, g, b = palette[idx*3:idx*3+3]
        return int(r) + int(g) + int(b)
    return max(used, key=brightness), used


def patch_journal_font(path, bdf_glyphs, ksc_chars):
    original = Path(path).read_bytes()
    count, table, data0 = sti_layout(original)
    if count != 3299:
        raise ValueError(f'unexpected subimage count {count}')
    fg, old_ref_colors = pick_foreground_index(original, table, data0)

    old_descs = [frame_desc(original, table, i) for i in range(count)]
    old_streams = []
    for i, (off, ln, xo, yo, h, w) in enumerate(old_descs):
        stream = original[data0+off:data0+off+ln]
        decode_stream(stream, h, w)
        old_streams.append(stream)

    new_streams = list(old_streams)
    for idx, ch in enumerate(ksc_chars):
        slot = HANGUL_SLOT_BASE + idx
        off, ln, xo, yo, h, w = old_descs[slot]
        if (h, w) != (28, 13):
            raise ValueError(f'slot {slot}: unexpected frame {w}x{h}')
        gw, gh, gx, gy, pixels = bdf_glyphs[ord(ch)]
        if (gw, gh) != (11, 11):
            raise ValueError(f'{ch} U+{ord(ch):04X}: Galmuri11 glyph is {gw}x{gh}, expected 11x11')
        left = (w - gw) // 2
        top = FRAME_TOP
        if left < 0 or top < 0 or left + gw > w or top + gh > h:
            raise ValueError('glyph placement out of bounds')
        canvas = [[0] * w for _ in range(h)]
        for sy in range(gh):
            for sx in range(gw):
                if pixels[sy][sx]:
                    canvas[top + sy][left + sx] = fg
        stream = encode_stream(canvas)
        check = decode_stream(stream, h, w)
        if check != canvas:
            raise AssertionError('ETRLE round-trip failed')
        nonzero = {v for row in check for v in row if v}
        if nonzero - {fg}:
            raise AssertionError('shadow/extra palette values present')
        new_streams[slot] = stream

    header_palette = bytearray(original[:table])
    payload_len = sum(map(len, new_streams))
    struct.pack_into('<I', header_palette, 8, payload_len)

    desc_blob = bytearray()
    payload = bytearray()
    cursor = 0
    for desc, stream in zip(old_descs, new_streams):
        _, _, xo, yo, h, w = desc
        desc_blob.extend(struct.pack('<IIhhHH', cursor, len(stream), xo, yo, h, w))
        payload.extend(stream)
        cursor += len(stream)
    assert cursor == payload_len
    rebuilt = bytes(header_palette) + bytes(desc_blob) + bytes(payload)

    c2, t2, d2 = sti_layout(rebuilt)
    if c2 != count or d2 != data0:
        raise AssertionError('STI layout changed unexpectedly')
    for i in range(c2):
        off, ln, xo, yo, h, w = frame_desc(rebuilt, t2, i)
        decode_stream(rebuilt[d2+off:d2+off+ln], h, w)
    if struct.unpack_from('<I', rebuilt, 8)[0] != len(rebuilt) - data0:
        raise AssertionError('uiStoredSize mismatch')
    for i in range(HANGUL_SLOT_BASE, HANGUL_SLOT_BASE + HANGUL_COUNT):
        off, ln, xo, yo, h, w = frame_desc(rebuilt, t2, i)
        pix = decode_stream(rebuilt[d2+off:d2+off+ln], h, w)
        vals = {v for row in pix for v in row if v}
        if not vals or vals != {fg}:
            raise AssertionError(f'slot {i}: invalid palette values {vals}')

    Path(path).write_bytes(rebuilt)
    return {'foreground_index': fg, 'old_ref_colors': old_ref_colors, 'old_size': len(original), 'new_size': len(rebuilt), 'stored_size': payload_len, 'hangul_replaced': HANGUL_COUNT}


def utf16_string_bounds(data, pos):
    if pos % 2:
        raise ValueError(f'UTF-16 occurrence not aligned: {pos}')
    start = pos
    while start >= 2 and data[start-2:start] != b'\x00\x00':
        start -= 2
    end = pos
    while end + 2 <= len(data) and data[end:end+2] != b'\x00\x00':
        end += 2
    if end + 2 > len(data):
        raise ValueError('unterminated UTF-16 string')
    return start, end


def patch_fact(path):
    p = Path(path)
    data = bytearray(p.read_bytes())
    old_size = len(data)
    needle = 'Mook'.encode('utf-16le')
    positions = []
    cur = 0
    while True:
        q = data.find(needle, cur)
        if q < 0: break
        positions.append(q); cur = q + 2
    if len(positions) != 18:
        raise ValueError(f'expected 18 UTF-16LE Mook tokens in v0.2.8 FACT.DBS, got {len(positions)}')

    targets = {}
    for q in positions:
        s, e = utf16_string_bounds(data, q)
        text = bytes(data[s:e]).decode('utf-16le')
        if any('\uac00' <= ch <= '\ud7a3' for ch in text):
            targets[(s, e)] = text
    if len(targets) != 16:
        raise ValueError(f'expected 16 translated journal strings, got {len(targets)}')
    replacements = sum(text.count('Mook') for text in targets.values())
    if replacements != 17:
        raise ValueError(f'expected 17 Mook tokens in Korean strings, got {replacements}')

    changed_spans = []
    for (s, e), text in sorted(targets.items(), reverse=True):
        new = text.replace('Mook', '무크')
        encoded = new.encode('utf-16le')
        capacity = (e - s) + 2
        blob = encoded + b'\x00\x00'
        if len(blob) > capacity:
            raise ValueError('replacement does not fit fixed journal field')
        data[s:s+capacity] = blob + b'\x00' * (capacity - len(blob))
        changed_spans.append((s, e, text, new))

    if len(data) != old_size:
        raise AssertionError('FACT.DBS size changed')
    if data.count(needle) != 1:
        raise AssertionError(f'expected only English Mook token to remain; got {data.count(needle)}')
    if data.count('무크'.encode('utf-16le')) != 17:
        raise AssertionError('expected 17 무크 tokens')
    if data.count('묵'.encode('utf-16le')) != 0:
        raise AssertionError('standalone 묵 remains')
    p.write_bytes(data)
    return changed_spans


def verify_fact_hangul_supported(fact_path, sti_path, ksc_chars):
    data = Path(fact_path).read_bytes()
    chars = set()
    for code in range(0xAC00, 0xD7A4):
        ch = chr(code)
        if ch.encode('utf-16le') in data:
            chars.add(ch)
    kmap = {ch: HANGUL_SLOT_BASE + i for i, ch in enumerate(ksc_chars)}
    unsupported = sorted(chars - set(kmap))
    if unsupported:
        raise AssertionError(f'FACT uses {len(unsupported)} Hangul syllables outside KS X 1001: {"".join(unsupported[:30])}')
    sti = Path(sti_path).read_bytes(); n, tab, d0 = sti_layout(sti)
    blank = []
    for ch in sorted(chars):
        slot = kmap[ch]
        off, ln, xo, yo, h, w = frame_desc(sti, tab, slot)
        pix = decode_stream(sti[d0+off:d0+off+ln], h, w)
        if not any(v for row in pix for v in row): blank.append(ch)
    if blank:
        raise AssertionError(f'blank Hangul glyphs used by FACT: {blank}')
    return len(chars)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def rewrite_readme(root, version_label):
    root = Path(root)
    old = root / 'README_v0.2.8.txt'
    if old.exists(): old.unlink()
    readme = root / 'README_v0.2.9.txt'
    if version_label == '1.28':
        heading = 'Wizardry 8 한국어 패치 v0.2.9 — 저널 글꼴/표기 수정 (Fan Patch 1.28 build 6735)'
        run = 'Fan Patch 1.28에서는 런처 언어를 ENG로 두고 Wiz8_v128.exe를 실행하세요.'
    else:
        heading = 'Wizardry 8 한국어 패치 v0.2.9 — 저널 글꼴/표기 수정 (1.24)'
        run = '압축 내용물을 Wizardry 8 게임 폴더에 덮어씁니다. 설치 전 게임 폴더와 세이브를 백업하세요.'
    text = f'''{heading}\n\n변경 내용\n- 저널의 Mook 표기를 한국어 "무크"로 통일했습니다. (16개 저널 문자열 / 17개 토큰)\n- 내부 식별자와 영문 원문 Mook는 변경하지 않았습니다.\n- Data/JOURNAL/journal_font.sti의 KS X 1001 한글 2,350글리프를 Galmuri11 v2.40.4 픽셀 형태로 교체했습니다.\n- 기존 갈무리9 저널 글리프에 포함되었던 우하단 1px 그림자를 완전히 제거했습니다.\n- 갈무리11 11x11 본체를 기존 13x28 프레임의 라틴 기준선에 맞춰 배치했습니다.\n- 한글 이외의 949글리프는 v0.2.8 압축 데이터를 그대로 유지했습니다.\n- 다른 UI 폰트 6종은 기존 갈무리9 패치를 유지하며, 이번 변경은 저널 폰트만 대상입니다.\n\n검증\n- FACT.DBS 파일 크기 유지 및 한국어 저널 필드 외 Mook 변경 없음\n- 영문 원문 Mook 1개 유지 / 한국어 저널 무크 17개 확인 / 묵 0개 확인\n- journal_font.sti 3,299프레임 전체 ETRLE 재해독 통과\n- 한글 2,350글리프 모두 단일 전경색만 사용(그림자 팔레트 없음)\n- FACT.DBS에서 사용되는 한글 음절이 모두 KS X 1001 슬롯에 존재하고 비어 있지 않음을 확인\n- 1.24 / 1.28 수정 FACT.DBS와 journal_font.sti 바이트 단위 동일\n\n{run}\n\nGalmuri는 SIL Open Font License 1.1에 따라 사용됩니다. 자세한 내용은 licenses/Galmuri_OFL-1.1.txt를 참조하세요.\n'''
    readme.write_text(text, encoding='utf-8')


def regenerate_sha(root):
    root = Path(root)
    lines = []
    for p in sorted(x for x in root.rglob('*') if x.is_file() and x.name != 'SHA256SUMS.txt'):
        rel = p.relative_to(root).as_posix()
        lines.append(f'{sha256(p)}  {rel}')
    (root / 'SHA256SUMS.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bdf', required=True)
    ap.add_argument('--root124', required=True)
    ap.add_argument('--root128', required=True)
    args = ap.parse_args()
    ksc = ksc5601_hangul()
    wanted = {ord(ch) for ch in ksc}
    glyphs = parse_bdf(args.bdf, wanted)
    print(f'Loaded {len(glyphs)} Galmuri11 KS X 1001 Hangul glyphs')
    for label, root in [('1.24', Path(args.root124)), ('1.28', Path(args.root128))]:
        fact = root / 'Data' / 'DATABASES' / 'FACT.DBS'
        sti = root / 'Data' / 'JOURNAL' / 'journal_font.sti'
        spans = patch_fact(fact)
        fontrep = patch_journal_font(sti, glyphs, ksc)
        used = verify_fact_hangul_supported(fact, sti, ksc)
        rewrite_readme(root, label)
        regenerate_sha(root)
        print(f'{label}: FACT journal strings={len(spans)}, font={fontrep}, FACT unique Hangul={used}')
    f124 = Path(args.root124) / 'Data' / 'DATABASES' / 'FACT.DBS'
    f128 = Path(args.root128) / 'Data' / 'DATABASES' / 'FACT.DBS'
    j124 = Path(args.root124) / 'Data' / 'JOURNAL' / 'journal_font.sti'
    j128 = Path(args.root128) / 'Data' / 'JOURNAL' / 'journal_font.sti'
    if f124.read_bytes() != f128.read_bytes():
        raise AssertionError('1.24 and 1.28 patched FACT.DBS differ')
    if j124.read_bytes() != j128.read_bytes():
        raise AssertionError('1.24 and 1.28 patched journal_font.sti differ')
    print('Cross-version patched database/font identity: OK')
    print('FACT SHA256', sha256(f124))
    print('STI  SHA256', sha256(j124))

if __name__ == '__main__':
    main()

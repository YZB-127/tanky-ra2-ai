# -*- coding: utf-8 -*-
"""build_yr.py -- 生成尤里的复仇「皮糙肉厚」rulesmd.ini

基线取自 expandmd01.mix 里那份 rulesmd.ini（YR 的加载顺序是 expandmd* → ra2md → expand*，先命中先用，
所以游戏实际用的是 expandmd01 里这份）。expand01.mix 里那份被改过（有 ;zhuijianspy 之类），不用。

用法: python build_yr.py [-o 输出路径]
"""
import argparse
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
G = Path(r'D:\RAY2\红色警戒2 完整版')
HERE = Path(__file__).resolve().parent
OUT_DEFAULT = HERE.parent / 'tanky-ra2-ai' / 'rulesmd.ini'
HP = 1500

MARK = b'; RULE*.INI'
NUM = re.compile(r'(?im)^(?P<pre>[ \t]*(?P<key>Tree|Bridge)?Strength[ \t]*=[ \t]*)(?P<num>-?\d+)(?P<rest>[^\r\n]*)')
SEC = re.compile(r'(?im)^\[(?P<name>[^\]\r\n]+)\][^\r\n]*')
TYPES = ['InfantryTypes', 'VehicleTypes', 'AircraftTypes', 'BuildingTypes']


def extract_base() -> bytes:
    d = (G / 'expandmd01.mix').read_bytes()
    start = d.find(MARK)
    if start < 0:
        raise SystemExit('expandmd01.mix 里找不到 rulesmd.ini')
    tail = (HERE / 'rulesmd_from_expand01.ini').read_bytes()[-160:]
    end = d.find(tail, start, start + 2_000_000)
    if end < 0:
        raise SystemExit('找不到 rulesmd.ini 的结尾')
    return d[start:end + len(tail)]


def tune(text: str) -> tuple:
    """降负载：MaxDebris<=4, MinDebris<=1, 每类只留 1 套粒子系统, 油桶 DelayKillFrames=15"""
    line_re = re.compile(
        r'^(?P<ind>[ \t]*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)'
        r'(?P<eq>[ \t]*=[ \t]*)(?P<val>[^;\r\n]*?)(?P<sp>[ \t]*)(?P<cmt>;[^\r\n]*)?(?P<cr>\r?)$'
    )
    sec_re = re.compile(r'^\[(?P<name>[^\]]+)\]')
    m = re.search(r'(?mi)^\[BuildingTypes\]\s*$', text)
    btypes = set()
    if m:
        end = text.find('\n[', m.end() + 1)
        body = text[m.end():end if end > 0 else len(text)]
        btypes = {x.group(1).strip().upper() for x in re.finditer(r'(?m)^\s*\d+\s*=\s*([A-Za-z0-9_]+)', body)}

    lines = text.split('\n')
    maxd, cur = {}, ''
    for ln in lines:
        s = sec_re.match(ln.strip())
        if s:
            cur = s.group('name').strip().upper()
            continue
        mm = line_re.match(ln)
        if mm and mm.group('key').upper() == 'MAXDEBRIS':
            try:
                maxd[cur] = min(int(mm.group('val').strip()), 4)
            except ValueError:
                pass

    out, stat, cur = [], {}, ''
    for ln in lines:
        s = sec_re.match(ln.strip())
        if s:
            cur = s.group('name').strip().upper()
            out.append(ln)
            continue
        mm = line_re.match(ln)
        if not mm:
            out.append(ln)
            continue
        key, val = mm.group('key').upper(), mm.group('val').strip()
        new = None
        if key == 'MAXDEBRIS':
            try:
                v = min(int(val), 4)
                if v != int(val):
                    new = str(v)
                    stat['MaxDebris'] = stat.get('MaxDebris', 0) + 1
            except ValueError:
                pass
        elif key == 'MINDEBRIS':
            try:
                v = min(int(val), 1 if maxd.get(cur, 1) > 0 else 0)
                if v != int(val):
                    new = str(v)
                    stat['MinDebris'] = stat.get('MinDebris', 0) + 1
            except ValueError:
                pass
        elif key == 'DAMAGEPARTICLESYSTEMS':
            items = [x.strip() for x in val.split(',') if x.strip()]
            if len(items) > 1:
                pick = ('BigGreySmokeSys' if cur in btypes
                        else ('SmallGreySSys' if 'SmallGreySSys' in items else items[0]))
                if pick != val:
                    new = pick
                    stat['DamageParticleSystems'] = stat.get('DamageParticleSystems', 0) + 1
        elif key == 'DELAYKILLFRAMES' and cur == 'OILEXPLOSIONWH':
            if val != '15':
                new = '15'
                stat['DelayKillFrames'] = stat.get('DelayKillFrames', 0) + 1
        if new is None:
            out.append(ln)
        else:
            out.append(f"{mm.group('ind')}{mm.group('key')}{mm.group('eq')}{new}"
                       f"{mm.group('sp') or ''}{mm.group('cmt') or ''}{mm.group('cr')}")
    return '\n'.join(out), stat


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('-o', '--output', default=str(OUT_DEFAULT))
    args = ap.parse_args()
    out = Path(args.output)

    raw = extract_base()
    text = raw.decode('latin-1')
    print(f'基线: expandmd01.mix 里的 rulesmd.ini  {len(raw)} B')

    counts = {}

    def repl(m):
        key = m.group('key') or 'Strength'
        counts[key] = counts.get(key, 0) + 1
        return f'{m.group("pre")}{HP}{m.group("rest")}'

    text = NUM.sub(repl, text)

    # 类型表里被声明、但段落缺失或没有 Strength 的类型 -> 补 1500
    sections = {}
    for m in SEC.finditer(text):
        nxt = SEC.search(text, m.end())
        body = text[m.end(): nxt.start() if nxt else len(text)]
        sections[m.group('name').strip().lower()] = (m, body)
    missing = []
    for t in TYPES:
        tm = re.search(r'(?im)^\[' + t + r'\][^\r\n]*\r?\n', text)
        if not tm:
            print(f'  !! 没有 [{t}] 段落')
            continue
        nxt = SEC.search(text, tm.end())
        body = text[tm.end(): nxt.start() if nxt else len(text)]
        names = [x.group(1).strip() for x in re.finditer(r'(?m)^[ \t]*\d+[ \t]*=[ \t]*([A-Za-z0-9_]+)', body)]
        for nm in names:
            low = nm.lower()
            if low not in sections:
                missing.append((t, nm, 'no-section'))
            elif not re.search(r'(?im)^[ \t]*Strength[ \t]*=', sections[low][1]):
                missing.append((t, nm, 'no-key'))

    # 有段落的直接插健，没段落的在文件末尾补段落
    inserts = []
    appended = []
    for t, nm, why in missing:
        if why == 'no-key':
            ins = sections[nm.lower()][0].end()
            # 段落头后面紧跟换行 -> 插在换行之后
            nl = text.find('\n', ins)
            inserts.append(nl + 1)
        else:
            appended.append(nm)
    for pos in sorted(inserts, reverse=True):
        text = text[:pos] + f'Strength={HP}\r\n' + text[pos:]
    if appended:
        if not text.endswith('\r\n'):
            text += '\r\n'
        text += ('\r\n; --- TANKY fix: listed in Type lists but section missing ---\r\n'
                 '; --- engine default Strength 0 -> repair divide-by-zero ---\r\n')
        for nm in appended:
            text += f'[{nm}]\r\nStrength={HP}\r\n'

    text, tstat = tune(text)
    print('降负载:', tstat)

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w', encoding='latin-1', newline='') as fh:
        fh.write(text)

    vals = re.findall(r'(?im)^[ \t]*(?:Tree|Bridge)?Strength[ \t]*=[ \t]*(\S+)', text)
    bad = sorted({v for v in vals if v.split(';')[0].strip() != str(HP)})
    print(f'\n输出: {out}  {len(text.encode("latin-1"))} B')
    for k in sorted(counts):
        print(f'  {k:16s} 改 {counts[k]} 处')
    print(f'  补血类型: 有段落补键 {len(inserts)} 个, 末尾补段落 {len(appended)} 个 -> {appended}')
    print(f'  Strength 行数 {len(vals)}，非 {HP} 的: {bad or "none"}')
    for k in ['RepairStep', 'IRepairStep', 'TreeStrength', 'BridgeStrength']:
        m = re.search(r'(?mi)^[ \t]*' + k + r'[ \t]*=[ \t]*(\S+)', text)
        print(f'  {k:14s} {m.group(1) if m else "(无)"}')
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())

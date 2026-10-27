#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
皮糙肉厚的红警 · 一键应用 / 撤销脚本（原版红警2 + 尤里的复仇）

用法:
    python apply_tanky_mod.py "<游戏目录>"                 # 只改原版红警2（rules.ini）
    python apply_tanky_mod.py "<游戏目录>" --yr            # 只改尤里的复仇（rulesmd.ini）
    python apply_tanky_mod.py "<游戏目录>" --all           # 两个都改
    python apply_tanky_mod.py "<游戏目录>" --undo          # 还原（默认还原原版那边）
    python apply_tanky_mod.py "<游戏目录>" --all --undo    # 还原两个
    python apply_tanky_mod.py "<游戏目录>" --check         # 只看状态

做了什么（每种模式都是同一套三板斧）:
    1. 规则文件 -> 覆盖（原版 rules.ini / 尤里 rulesmd.ini：全部血量 1500 + 降负载）
    2. 任务包/地图里的"地图自带血量覆盖"按三条规则处理：
         * 原本就 > 1500（任务刻意做硬的目标/地标）  -> 保留任务原值
         * 类型在规则文件里有段落                    -> 注释掉该行（等长），回落到 1500
         * 任务图内自带类型                          -> 原地/等长挪字节写成 1500
       全部等长替换，MIX 索引、偏移、文件大小一个字节都不变。
    3. 打包里那份规则（尤里在 expandmd01.mix、原版在 ra2.mix 里）**不用动**：
       往游戏目录放一份散装 rules.ini / rulesmd.ini 就会盖住它 —— 引擎本来就支持
       （rulesmd.ini 抬头原话：If placed in game directory, it will override built in values）

    原版额外: 盟军第一关的自由女神像 [CANEWY04] -> 99999（守得住）
    原版额外: game.exe 若被 5 血维修补丁改过，自动从备份还原（1500 血绝不能打那个补丁）

被修改的文件会先备份到 <游戏目录>\\_tanky_backup\\（原版）或 _tankymd_backup\\（尤里）
"""
import argparse
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

HP = 1500

MODES = {
    'ra2': dict(
        label='原版红警2',
        exe='game.exe',
        rules='rules.ini',
        backup='_tanky_backup',
        mixes=['maps01.mix', 'maps02.mix', 'MULTI.MIX', 'EB2.mmx', 'Hills.mmx'],
        statue=('maps01.mix', 'CANEWY04', '99999'),
        exe_patch=True,
    ),
    'yr': dict(
        label='尤里的复仇',
        exe='gamemd.exe',
        rules='rulesmd.ini',
        backup='_tankymd_backup',
        mixes=['mapsmd03.mix', 'multimd.mix', 'DeepFrze.yro', 'HighExpR.yro',
               'MonsterM.yro', 'Transylv.yro'],
        statue=None,
        exe_patch=False,
    ),
}

EXE_OFFSET = 0x2DAEDC                      # 5 血模式用的维修补丁（只对 game.exe 有效）
EXE_VANILLA = bytes.fromhex('f7b934130000')
EXE_5HP = bytes.fromhex('b80500000090')

STR_LINE = re.compile(rb'(?im)^([ \t]*)Strength([ \t]*=[ \t]*)(\d+)')
SEC_NAME = re.compile(rb'(?im)^[ \t]*\[([^\]\r\n]{1,40})\]')
ACTIVE = re.compile(rb'(?im)^[ \t]*(?:Tree|Bridge)?Strength[ \t]*=[ \t]*(\S+)')
MANIFEST = 'manifest.txt'


# ---------------------------------------------------------------- helpers
def read_ini_sections(path: Path) -> set:
    with open(path, 'r', encoding='latin-1', newline='') as fh:
        text = fh.read()
    return {m.group(1).strip().lower() for m in re.finditer(r'(?im)^\[([^\]\r\n]+)\]', text)}


def backup_of(path: Path, bak_dir: Path) -> bool:
    bak_dir.mkdir(exist_ok=True)
    dst = bak_dir / path.name
    if not dst.exists():
        shutil.copy2(path, dst)
        return True
    return False


def apply_policy(path: Path, rules_sections: set) -> dict:
    data = path.read_bytes()
    out = bytearray(data)
    res = dict(kept=[], commented=0, rewritten=0, skipped=[], size=len(data))
    for m in STR_LINE.finditer(data):
        rel = re.search(rb'Strength', m.group(0), re.I)
        idx = m.start() + rel.start()
        secs = SEC_NAME.findall(data, 0, m.start())
        sec = secs[-1].decode('latin-1').strip() if secs else ''
        digits = m.group(3)
        if out[idx:idx + 1] == b';':
            continue
        if int(digits) > HP:
            res['kept'].append(f'[{sec}]={digits.decode()}')
        elif sec.lower() in rules_sections:
            out[idx:idx + 1] = b';'
            res['commented'] += 1
        elif len(digits) >= 4:
            out[m.start(3):m.end(3)] = str(HP).encode() + b' ' * (len(digits) - 4)
            res['rewritten'] += 1
        else:
            res['skipped'].append(f'[{sec}]={digits.decode()}')
    if len(out) != len(data):
        raise SystemExit(f'!! {path.name}: 长度变化，已中止')
    if res['commented'] or res['rewritten']:
        path.write_bytes(bytes(out))
    return res


def short_overrides(path: Path, rules_sections: set) -> list:
    """任务图内自带类型、值只有 1~3 位：值扩成 1500，在同一段落内删等量字节，净长度不变"""
    data = path.read_bytes()
    edits, report = [], []
    for m in STR_LINE.finditer(data):
        digits = m.group(3)
        if len(digits) >= 4:
            continue
        secs = SEC_NAME.findall(data, 0, m.start())
        sec = secs[-1].decode('latin-1').strip() if secs else ''
        if sec.lower() in rules_sections or int(digits) > HP:
            continue
        need = 4 - len(digits)
        hdr = None
        for h in re.finditer(rb'(?im)^\[[^\]\r\n]{1,40}\]', data[:m.start()]):
            hdr = h
        nxt = re.search(rb'(?im)^\[[^\]\r\n]{1,40}\]', data[m.end():])
        lo = hdr.start() if hdr else 0
        hi = m.end() + nxt.start() if nxt else len(data)
        cands = []
        for bm in re.finditer(rb'\r\n\r\n', data[lo:hi]):
            if lo + bm.start() >= m.end():
                cands.append((lo + bm.start(), 2))
        for cm in re.finditer(rb'\r\n;[^\r\n]*\r\n', data[lo:hi]):
            if lo + cm.start() + 2 > m.end():
                cands.append((lo + cm.start() + 2, cm.end() - cm.start() - 2))
        for dm in re.finditer(rb',([A-Za-z0-9_]{2,}),\1(?=[,\r\n])', data[lo:hi]):
            if lo + dm.start() + len(dm.group(1)) + 1 > m.end():
                cands.append((lo + dm.start() + len(dm.group(1)) + 1, len(dm.group(1)) + 1))
        got = 0
        for pos, n in sorted(cands, key=lambda c: abs(c[0] - m.start())):
            if got >= need:
                break
            take = min(n, need - got)
            edits.append((pos, take, b''))
            got += take
        if got < need:
            report.append(f'!! [{sec}]={digits.decode()} 需要 {need}B，只找到 {got}B，跳过')
            continue
        edits.append((m.start(1), m.end(3) - m.start(1),
                      m.group(1) + b'Strength' + m.group(2) + str(HP).encode()))
        report.append(f'[{sec}]={digits.decode()} -> {HP}（等长挪字节 {got}B）')
    if edits:
        out = bytearray(data)
        for pos, n, rep in sorted(edits, key=lambda e: -e[0]):
            out[pos:pos + n] = rep
        if len(out) == len(data):
            path.write_bytes(bytes(out))
        else:
            report.append('!! 长度变化，已放弃')
    return report


def buff_statue(game: Path, cfg: dict) -> str:
    if not cfg.get('statue'):
        return '（尤里模式无此项）'
    fname, section, value = cfg['statue']
    p = game / fname
    if not p.is_file():
        return '文件不存在，跳过'
    data = p.read_bytes()
    out = bytearray(data)
    hits = 0
    for m in STR_LINE.finditer(data):
        secs = SEC_NAME.findall(data, 0, m.start())
        if not secs or secs[-1].decode('latin-1').strip().upper() != section:
            continue
        old = m.group(3)
        if old == value.encode():
            continue
        if len(old) != len(value):
            return f'[{section}] 原值 {old.decode()} 位数不同，跳过'
        out[m.start(3):m.end(3)] = value.encode()
        hits += 1
    if hits:
        p.write_bytes(bytes(out))
    return f'[{section}] -> {value}（改 {hits} 处）'


def exe_state(exe: Path, cfg: dict) -> str:
    if not cfg.get('exe_patch'):
        return '不依赖 exe 补丁（1500 血无需改动）'
    with open(exe, 'rb') as fh:
        fh.seek(EXE_OFFSET)
        raw = fh.read(6)
    if raw == EXE_VANILLA:
        return '原版未打补丁'
    if raw == EXE_5HP:
        return '被 5 血维修补丁改过'
    return f'未知({raw.hex()})'


def report(game: Path, cfg: dict) -> None:
    rules = game / cfg['rules']
    if rules.is_file():
        vals = sorted({m.group(1) for m in ACTIVE.finditer(rules.read_bytes())})
        print(f'  {cfg["rules"]:13s} {rules.stat().st_size:>9} B   Strength 取值 {len(vals)} 种: {vals[:6]}')
    else:
        print(f'  {cfg["rules"]:13s} （没有散装文件，游戏用打包里那份）')
    for name in cfg['mixes']:
        p = game / name
        if not p.is_file():
            print(f'    {name:14s} 不存在')
            continue
        data = p.read_bytes()
        vals = sorted({m.group(1).decode() for m in ACTIVE.finditer(data)})
        print(f'    {name:14s} {len(data):>9} B   覆盖取值 {vals}')
    exe = game / cfg['exe']
    if exe.is_file():
        print(f'  {cfg["exe"]:13s} {exe.stat().st_size:>9} B   {exe_state(exe, cfg)}')


def apply_mode(game: Path, mode: str) -> int:
    cfg = MODES[mode]
    bak = game / cfg['backup']
    src_rules = HERE / cfg['rules']
    print(f'== {cfg["label"]} ==')
    if not src_rules.is_file():
        print(f'  !! 脚本旁边没有 {cfg["rules"]}')
        return 1
    rules_sections = read_ini_sections(src_rules)

    dst_rules = game / cfg['rules']
    bak.mkdir(exist_ok=True)
    man = bak / MANIFEST
    first_time = True
    if man.is_file():
        first_time = not any(line.startswith(cfg['rules'] + '\t')
                             for line in man.read_text(encoding='utf-8').splitlines())
    if first_time:                      # 只在第一次记录"原来有没有这个文件"，重复安装不再备份自己的产物
        created = not dst_rules.is_file()
        if not created:
            backup_of(dst_rules, bak)
        with open(man, 'a', encoding='utf-8') as fh:
            fh.write(f'{cfg["rules"]}\t{"created" if created else "replaced"}\n')
    shutil.copy2(src_rules, dst_rules)
    print(f'  [1/4] {cfg["rules"]} 已写入（全部血量 {HP} + 降负载）')

    print('  [2/4] 任务包/地图里的地图自带覆盖:')
    for name in cfg['mixes']:
        p = game / name
        if not p.is_file():
            print(f'        {name:14s} 不存在，跳过')
            continue
        backup_of(p, bak)
        res = apply_policy(p, rules_sections)
        print(f'        {name:14s} 注释 {res["commented"]:3d}  原地改 {HP} {res["rewritten"]:3d}'
              f'  保留原值 {len(res["kept"]):2d}  ({res["size"]} 字节)')
        for k in res['kept']:
            print(f'                    保留 {k}')
        for line in short_overrides(p, rules_sections):
            print(f'                    {line}')

    print(f'  [3/4] 加厚目标: {buff_statue(game, cfg)}')

    exe = game / cfg['exe']
    if not exe.is_file():
        print(f'  [4/4] !! 找不到 {cfg["exe"]}')
    else:
        state = exe_state(exe, cfg)
        if state.startswith('被') and (bak / cfg['exe']).is_file():
            shutil.copy2(bak / cfg['exe'], exe)
            print(f'  [4/4] {cfg["exe"]}: 之前被打过 5 血补丁 -> 已还原原版')
        else:
            print(f'  [4/4] {cfg["exe"]}: {state}')
    print()
    return 0


def undo_mode(game: Path, mode: str) -> int:
    cfg = MODES[mode]
    bak = game / cfg['backup']
    print(f'== 撤销 {cfg["label"]} ==')
    if not bak.is_dir():
        print(f'  没有备份目录 {bak}，跳过\n')
        return 0
    created = False
    man = bak / MANIFEST
    if man.is_file():
        for line in man.read_text(encoding='utf-8').splitlines():
            if line.startswith(cfg['rules'] + '\t'):
                created = line.endswith('created')
    n = 0
    for f in sorted(bak.iterdir()):
        if f.is_file() and f.name != MANIFEST:
            shutil.copy2(f, game / f.name)
            n += 1
            print(f'  还原 {f.name}')
    if created and (game / cfg['rules']).is_file():
        (game / cfg['rules']).unlink()
        print(f'  删除（原本没有的）{cfg["rules"]}')
    print(f'  共处理 {n} 个文件\n')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description='皮糙肉厚的红警 一键应用/撤销')
    ap.add_argument('gamedir')
    ap.add_argument('--yr', action='store_true', help='只处理尤里的复仇')
    ap.add_argument('--all', action='store_true', help='原版 + 尤里 都处理')
    ap.add_argument('--undo', action='store_true', help='从备份还原')
    ap.add_argument('--check', action='store_true', help='只看状态')
    args = ap.parse_args()

    game = Path(args.gamedir)
    if not game.is_dir():
        print(f'找不到目录: {game}')
        return 1
    modes = ['ra2', 'yr'] if args.all else (['yr'] if args.yr else ['ra2'])

    if args.check:
        print(f'目录: {game}')
        for m in modes:
            print(f'\n[{MODES[m]["label"]}]')
            report(game, MODES[m])
        return 0

    if args.undo:
        for m in modes:
            undo_mode(game, m)
        return 0

    rc = 0
    for m in modes:
        rc |= apply_mode(game, m)
    print('完成。请完全退出游戏后重开（规则、地图覆盖都只在加载时读一次）。')
    print('注意: 存档自带当时的场景数据，应用后请【新开】关卡/遭遇战，别读旧存档。')
    print(f'撤销: python {Path(__file__).name} "{game}" --undo')
    return rc


if __name__ == '__main__':
    raise SystemExit(main())

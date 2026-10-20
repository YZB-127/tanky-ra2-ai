#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
皮糙肉厚的红警 · 一键应用 / 撤销脚本

用法:
    python apply_tanky_mod.py "<游戏目录>"            # 应用
    python apply_tanky_mod.py "<游戏目录>" --undo     # 从备份还原原版
    python apply_tanky_mod.py "<游戏目录>" --check    # 只看状态，不改任何东西

做了什么:
    1. rules.ini                     -> 覆盖（全部血量 1500 + 降负载，见 README）
    2. 5 个原版任务包/地图里的"地图自带血量覆盖" -> 按下面三条规则处理：
         * 原本就 > 1500（任务刻意做硬的目标/地标）  -> 保留任务原值，不动
         * 类型在 rules.ini 里有段落                -> 注释掉该行（等长），回落到 1500
         * 任务图内自带类型                        -> 原地/等长挪字节写成 1500
       全部等长替换，MIX 索引、偏移、文件大小一个字节都不变。
    3. 盟军第一关的自由女神像 [CANEWY04] -> 99999（全员 1500 血后进攻方很难清，
       10000 的任务原值守不住，所以单独加厚）
    4. game.exe 必须是原版未打补丁（5 血那次的维修补丁会让 1500 血的维修费翻 5 倍）；
       若发现被打了补丁，自动从备份还原

被修改的文件会先备份到 <游戏目录>\\_tanky_backup\\
"""
import argparse
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKUP_DIRNAME = '_tanky_backup'

# 原版红警2 的 5 个任务包 / 地图（尤里的复仇相关文件一律不碰）
MIX_FILES = ['maps01.mix', 'maps02.mix', 'MULTI.MIX', 'EB2.mmx', 'Hills.mmx']

HP = 1500
STATUE_SECTION = 'CANEWY04'
STATUE_HP = '99999'
STATUE_FILE = 'maps01.mix'

EXE_OFFSET = 0x2DAEDC
EXE_VANILLA = bytes.fromhex('f7b934130000')
EXE_5HP = bytes.fromhex('b80500000090')

STR_LINE = re.compile(rb'(?im)^([ \t]*)Strength([ \t]*=[ \t]*)(\d+)')
SEC_NAME = re.compile(rb'(?im)^[ \t]*\[([^\]\r\n]{1,40})\]')
ACTIVE = re.compile(rb'(?im)^[ \t]*Strength[ \t]*=[ \t]*(\d+)')


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


def short_overrides(path: Path, rules_sections: set) -> tuple:
    """任务图内自带类型、且值只有 1~3 位数的覆盖：值扩成 1500，并在同一段落内
    删掉等量字节（空行 / 注释行 / 重复的逗号列表项），保证文件长度不变。"""
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
        spent = []
        for pos, n in sorted(cands, key=lambda c: abs(c[0] - m.start())):
            if got >= need:
                break
            take = min(n, need - got)
            edits.append((pos, take, b''))
            spent.append(pos)
            got += take
        if got < need:
            report.append(f'   !! [{sec}]={digits.decode()} 需要 {need}B，只找到 {got}B，跳过')
            continue
        edits.append((m.start(1), m.end(3) - m.start(1),
                      m.group(1) + b'Strength' + m.group(2) + str(HP).encode()))
        report.append(f'   [{sec}]={digits.decode()} -> {HP}（等长挪字节 {got}B）')
    if edits:
        out = bytearray(data)
        for pos, n, rep in sorted(edits, key=lambda e: -e[0]):
            out[pos:pos + n] = rep
        if len(out) == len(data):
            path.write_bytes(bytes(out))
        else:
            report.append('   !! 长度变化，已放弃')
    return report


def apply_policy(path: Path, rules_sections: set) -> dict:
    """按三条规则处理一个包里的所有血量覆盖"""
    data = path.read_bytes()
    out = bytearray(data)
    res = dict(kept=[], commented=0, rewritten=0, skipped=[], size=len(data))
    for m in STR_LINE.finditer(data):
        rel = re.search(rb'Strength', m.group(0), re.I)
        idx = m.start() + rel.start()          # the 'S' of Strength
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
        res['size'] = len(out)
    return res


def buff_statue(game: Path) -> str:
    """盟军第一关的自由女神像 -> 99999（等长替换）"""
    p = game / STATUE_FILE
    if not p.is_file():
        return '文件不存在，跳过'
    data = p.read_bytes()
    out = bytearray(data)
    hits = 0
    for m in STR_LINE.finditer(data):
        secs = SEC_NAME.findall(data, 0, m.start())
        if not secs or secs[-1].decode('latin-1').strip().upper() != STATUE_SECTION:
            continue
        old = m.group(3)
        if old == STATUE_HP.encode():
            continue
        if len(old) != len(STATUE_HP):
            return f'[{STATUE_SECTION}] 原值 {old.decode()} 位数不同，需手工处理，跳过'
        out[m.start(3):m.end(3)] = STATUE_HP.encode()
        hits += 1
    if hits:
        p.write_bytes(bytes(out))
    return f'[{STATUE_SECTION}] -> {STATUE_HP}（改 {hits} 处）'


def exe_state(exe: Path) -> str:
    with open(exe, 'rb') as fh:
        fh.seek(EXE_OFFSET)
        raw = fh.read(6)
    if raw == EXE_VANILLA:
        return '原版未打补丁'
    if raw == EXE_5HP:
        return '被 5 血维修补丁改过'
    return f'未知({raw.hex()})'


def report(game: Path) -> None:
    ini = game / 'rules.ini'
    if ini.is_file():
        vals = sorted({m.group(1).decode() for m in ACTIVE.finditer(ini.read_bytes())})
        print(f'  rules.ini     {ini.stat().st_size:>9} B   Strength 取值: {vals}')
    print('  任务包/地图:')
    for name in MIX_FILES:
        p = game / name
        if not p.is_file():
            print(f'    {name:14s} 不存在')
            continue
        data = p.read_bytes()
        vals = sorted({m.group(1).decode() for m in ACTIVE.finditer(data)})
        print(f'    {name:14s} {len(data):>9} B   覆盖取值: {vals}')
    exe = game / 'game.exe'
    if exe.is_file():
        print(f'  game.exe      {exe.stat().st_size:>9} B   {exe_state(exe)}')


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description='皮糙肉厚的红警 一键应用/撤销')
    ap.add_argument('gamedir')
    ap.add_argument('--undo', action='store_true', help='从 _tanky_backup 还原')
    ap.add_argument('--check', action='store_true', help='只看状态')
    args = ap.parse_args()

    game = Path(args.gamedir)
    if not game.is_dir():
        print(f'找不到目录: {game}')
        return 1
    bak = game / BACKUP_DIRNAME

    if args.check:
        print(f'目录: {game}\n')
        report(game)
        return 0

    if args.undo:
        if not bak.is_dir():
            print(f'没有备份目录 {bak}，无法撤销')
            return 1
        n = 0
        for f in sorted(bak.iterdir()):
            if f.is_file():
                shutil.copy2(f, game / f.name)
                n += 1
                print(f'  还原 {f.name}')
        print(f'\n已还原 {n} 个文件（原版状态）。记得重启游戏。')
        return 0

    print(f'目标目录: {game}\n备份目录: {bak}\n')
    rules_sections = read_ini_sections(HERE / 'rules.ini')

    # 1) rules.ini
    src_ini = HERE / 'rules.ini'
    if not src_ini.is_file():
        print('!! 脚本旁边没有 rules.ini')
        return 1
    backup_of(game / 'rules.ini', bak)
    shutil.copy2(src_ini, game / 'rules.ini')
    print(f'  [1/4] rules.ini 已覆盖（全部血量 {HP} + 降负载）')

    # 2) 任务包/地图
    print('  [2/4] 任务包/地图里的地图自带覆盖:')
    for name in MIX_FILES:
        p = game / name
        if not p.is_file():
            print(f'        {name:14s} 不存在，跳过')
            continue
        backup_of(p, bak)
        res = apply_policy(p, rules_sections)
        print(f'        {name:14s} 注释 {res["commented"]:3d}  原地改 1500 {res["rewritten"]:3d}'
              f'  保留原值 {len(res["kept"]):2d}  ({res["size"]} 字节)')
        for k in res['kept']:
            print(f'                    保留 {k}')
        if res['skipped']:
            for line in short_overrides(p, rules_sections):
                print(f'                    {line.strip()}')

    # 3) 自由女神像
    print(f'  [3/4] 自由女神像: {buff_statue(game)}')

    # 4) exe
    exe = game / 'game.exe'
    if not exe.is_file():
        print('  [4/4] !! 找不到 game.exe')
    else:
        state = exe_state(exe)
        if state.startswith('被') and (bak / 'game.exe').is_file():
            shutil.copy2(bak / 'game.exe', exe)
            print(f'  [4/4] game.exe 之前被打过 5 血补丁 -> 已还原原版')
        else:
            print(f'  [4/4] game.exe: {state}（无需处理）')

    print('\n完成。请完全退出游戏后重开（规则、地图覆盖都只在加载时读一次）。')
    print(f'想撤销: python {Path(__file__).name} "{game}" --undo')
    print('注意: 存档自带当时的场景数据，应用后请【新开】关卡/遭遇战，别读旧存档。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

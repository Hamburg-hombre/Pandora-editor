"""NSLBC level-script disassembler (GoldenEye: Rogue Agent DS).

Layout (all little-endian):
  header 8*u32: version=3, nA, nB, nC, nD, code_size, str_size, 0
  A: nA * 16 { name_hash, kind, link, 0xffffffff-or-value }
  B: nB * 16 { name_hash, index, value, 0xffffffff-or-value }
  C: nC * 16 { callback_hash, code_offset, code_length, 0 }
  D: nD * 4  { name_hash }
  code: code_size bytes; strings: str_size bytes, NUL-separated

Bytecode is u32 words; an instruction is an opcode word followed by N operands
(N from ARITY).  This table parses all 1979 callback blocks in the 18 levels.
The names in nslbc_names.txt were cracked by cross-referencing Global.LVL,
CodeStrings.CHR, the CRT tables and the scripts themselves.
"""
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sound import java31
from nslbc_lib import parse, WORK
from objects_lp import CodeStrings

ARITY = {0: 1, 1: 2, 2: 1, 3: 2, 4: 3, 5: 2, 6: 1, 7: 2,
         0x10000: 1, 0x10001: 1, 0x10002: 3, 0x10003: 1,
         0x20000: 2, 0x20001: 1, 0x20002: 1, 0x20004: 1, 0x20005: 0,
         0x20007: 1, 0x20008: 1, 0x20009: 1, 0x30000: 1, 0x30001: 0}
# Names reflect the semantics established in 2026-11-24 (see NSLBC_Format.md).
# 0x30000 takes ONE operand: the following 0x30001 is a separate opcode.
OPNAME = {0: 'STR', 1: 'CALL1', 2: 'ACTION', 3: 'SETPROP', 4: 'TIMER',
          5: 'KILL_AT', 6: 'STATE', 7: 'ACTION_AT',
          0x10000: 'CFG_BEGIN', 0x10001: 'CFG_APPLY',
          0x10002: 'CFG_FOGPARAM', 0x10003: 'CFG_FOGCOL',
          0x20000: 'USE_STR', 0x20001: 'EVENT', 0x20002: 'FX_START',
          0x20004: 'LEVEL_ID', 0x20005: 'CFG_END',
          0x20007: 'SND_AMBIENT', 0x20008: 'SND_PLAY', 0x20009: 'SND_STOP',
          0x30000: 'FLAG', 0x30001: 'FLAG_END'}

CALLBACKS = {}
for nm in ('ONSTARTUP', 'ONDEATH', 'ONACTION', 'ONGAMESTARTUP', 'ONOPEN', 'ONCLOSE',
           'UNLOCK', 'ONCREATE', 'STARTTRAP', 'LOCATE', 'KILL', 'SHOW', 'HITRED',
           'HITBLUE', 'PLAYER', 'LEVEL', 'OBJECTIVE', 'COLLIDE', 'STAND', 'ONKILL',
           'DISABLE', 'AI_RAND', 'MESSAGE', 'DOOR_OPEN', 'DOOR_CLOSE', 'DELETE',
           'CROUCH', 'EXPLOSION', 'ONUSE', 'ONENTER', 'ONEXIT', 'ONUPDATE',
           'ONTHINK', 'ONTIMER', 'ONPICKUP', 'ONSHOT', 'ONDAMAGE', 'ONSPAWN',
           'ONTRIGGER', 'ONLOOK', 'ONLOAD', 'ONINIT', 'INCREMENT', 'CHECKPOINT',
           'SHIELD', 'INVULNERABLE', 'DIRECTION', 'TIMER', 'TELEPORT', 'PROTECT',
           'LOCK', 'ENABLE', 'RESET', 'ATTACH'):
    CALLBACKS[java31(nm)] = nm

HASHNAMES = dict(CALLBACKS)
# Cracked 2026-11-24: java31 of the uppercase name, verified by co-occurring debug
# strings / SETPROP enum values.  These win over the older name file (which has a
# few accidental collisions from brute-forcing length >= 6).
CRACKED = {
    0x000008E8: 'GO', 0x942B571C: 'TURNON', 0xF13F8BB2: 'TURNOFF',
    0x9195D11C: 'SETFOG', 0x4434C682: 'ONTIMEOUT', 0x436C8638: 'LEVELOVER',
    0x88B4A252: 'BEHAVIOR', 0x73A03716: 'ASLEEP', 0x8C3F164C: 'PATROL',
    0x002237D4: 'IDLE', 0x6C4356E1: 'RETRACT', 0x4CA8E844: 'DEADHEAD',
    0x7A297B87: 'ENDCUT', 0x94464923: 'TWOWAY', 0xC0995E45: 'GRENADE_EXP1',
    0x2C79E36F: 'WHITEBLAST', 0xF245B8CB: 'CLEARROOMS',
    0xD8FE0306: 'DISABLE_SAILSMAN5',
    0x95411E2F: 'DT_FLAMES', 0x8E9FAE8C: 'DT_BORING',
    0x3551F32A: 'DT_TRAPDOOR', 0x273C466F: 'STARTTRAP',
    0x86290352: 'LOCATE', 0x0021C73A: 'HELI',
}
crack = os.path.join(HERE, 'nslbc_names.txt')
if os.path.isfile(crack):
    for line in open(crack, encoding='utf-8', errors='replace'):
        m = re.match(r'([0-9a-f]{8}) (\S+)\s+\d+:', line)
        if m:
            HASHNAMES.setdefault(int(m.group(1), 16), m.group(2))
HASHNAMES.update(CRACKED)


def load_names(work, lvl):
    rev = dict(HASHNAMES)
    chrpath = os.path.join(work, 'Levels', lvl, 'CodeStrings.CHR')
    if os.path.isfile(chrpath):
        cs = CodeStrings(open(chrpath, 'rb').read())
        for nm in cs.strings:
            rev.setdefault(java31(nm.upper()), 'CHR:' + nm)
            rev.setdefault(java31(nm), 'CHR:' + nm)
    lvlpath = os.path.join(work, 'Global.LVL')
    if os.path.isfile(lvlpath):
        lvltext = open(lvlpath, 'rb').read().decode('latin-1')
        for m in re.finditer(r'BEGIN "([^"]+)"(.*?)END', lvltext, re.S):
            for _i, nm in re.findall(r'(\d+)\s+"([^"]*)"', m.group(2)):
                rev.setdefault(java31(nm.upper()), nm)
    return rev


def operand_annotation(a, strmap, rev, op=0):
    if op in (0, 0x20000, 0x10000) and a in strmap:
        return 'STR"%s"' % strmap[a].replace('"', '\\"')
    if a in rev:
        return rev[a]
    if a > 0x10000:
        return '0x%08x' % a
    return '%d' % a


def disassemble(work, lvl, out=None):
    p = os.path.join(work, 'Levels', lvl, 'Script.NSLBC')
    r = parse(open(p, 'rb').read())
    rev = load_names(work, lvl)
    strmap = {o - r['s']['str']: s for o, s in r['strs']}
    lines = []
    hdr = r['s']['hdr']
    lines.append('# %s: %d bytes, header %s' % (lvl, len(r['raw']),
                                                ' '.join('%08x' % x for x in hdr)))
    lines.append('# A=%d B=%d C=%d D=%d code@%x(%d) strings@%x(%d)' %
                 (hdr[1], hdr[2], hdr[3], hdr[4], r['s']['code'], len(r['code']),
                  r['s']['str'], len(r['raw']) - r['s']['str']))
    for j, (h, off, ln, flag) in enumerate(r['C']):
        cb = rev.get(h, '%08x' % h)
        lines.append('\n=== C#%-3d %-24s off=%-5d len=%-4d ===' % (j, cb, off, ln))
        if ln % 4 or off + ln > len(r['code']):
            lines.append('   (invalid block)')
            continue
        w = struct.unpack_from('<%dI' % (ln // 4), r['code'], off)
        i = 0
        while i < len(w):
            op = w[i]
            if op not in ARITY:
                lines.append('  %04x: UNKNOWN opcode %08x' % (off + 4 * i, op))
                break
            n = ARITY[op]
            args = w[i + 1:i + 1 + n]
            i += 1 + n
            a = ', '.join(operand_annotation(x, strmap, rev, op) for x in args)
            lines.append('  %04x: %-8s %s' % (off + 4 * (i - 1 - n),
                                              OPNAME.get(op, 'OP%04x' % op), a))
    text = '\n'.join(lines)
    if out:
        open(out, 'w', encoding='utf-8').write(text)
    return text


if __name__ == '__main__':
    outdir = os.path.join(HERE, 'dis')
    os.makedirs(outdir, exist_ok=True)
    for lvl in ('MP_MiningPit', '1_1_Intro', '1_0_FortKnox'):
        txt = disassemble(WORK, lvl, os.path.join(outdir, lvl + '.txt'))
        print(txt[:2000])

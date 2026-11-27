#!/usr/bin/env python3
"""Definitive Objects.LP template -> model map for the 3D level viewer.

An Objects.LP record has two hashes:

    w0 = java31(CodeStrings.CHR[h1].upper())   # the instance name
    w1 = java31(TEMPLATE_NAME.upper())         # the object template

w0 was verified against all 2,887 placements in all 18 levels (100% match).
For placed props the level designer named the instance after the model the
game draws, so the instance name is direct evidence of the model.  The tables
below are derived from that evidence, not from fuzzy name matching.

Priority in the viewer is:

    user override  >  these tables  >  CHR-name guess  >  template propagation

Templates in NO_MODEL_W1 are logical markers (waypoints, spawn points, hint
panels, ...): they draw no model, so a name guess must never be propagated to
them.  Templates in VARIABLE_W1 place several different models; their model
comes from each instance's own name (decorations, zip lines).

The full evidence table is in ../mapping/objects_to_models.md.
"""
import os

# w1 -> model file stem (lowercase, matches work/Models/<stem>.nsbmd).
W1_MODEL = {
    # breakable props
    0x745a53a6: 'barrel',          # Barrel (barrel, barrelgold, barrelXX, ...)
    0x03d66ba3: 'crate',           # Destructable Crate (crate, crate01, ...)
    0xa0847f0a: 'crate_tall',      # Destructable Crate, tall (crate_tall*)
    0x88b4ef80: 'bottle',          # Destructable Bottle (Bottle01, ...)
    0x8de900b2: 'bottle2',         # Destructable Bottle2 (Bottle04, ...)
    0x180c3abf: 'monitor01',       # Monitor 01 (breakable)
    0x068f4140: 'monitor02',       # Monitor 02 (breakable)
    0xf51247c1: 'monitor03',       # Monitor 03 (breakable)
    0xdbcd914d: 'gl_brkable',      # Glass Divider (gl_Brkable*)
    0x5637dcff: 'foo_laserglass',  # Glass Divider (LaserGlass*)
    0xd5062078: 'v2_gls',          # Glass Divider (foo_v2gls*)
    0x8960801b: 'airfilter',       # Airfilter (Auric)
    # doors
    0x628e403e: 'fortknoxdr',      # Fort Knox Door (fortKnoxDr*)
    0x1c7be2cd: 'volcdoor',        # Volcano Door (VolcDoor*)
    0x40e992c4: 'funhous_dr',      # funhousedoor (funhous_DR*)
    0x185d218f: 'vaulthdoor',      # Vault Hall Door (vaultHDoor*)
    0x11481d64: 'trackdoor',       # Transit Door (TrackDoor*)
    0x4e19655b: 'atl_door',        # Atlantis Door (ATL_door*)
    0x75134745: 'atl_ddoor',       # Atlantis Door2 (ATL_DDoor*)
    0x73640fa8: 'casino_dr',       # Midas Casino Door2 (CAS_door*)
    0x0f1f6e92: 'bh_doors',        # Bathhouse Door (bh_door*)
    0x17992ffa: 'lz_door',         # Landing Zone Door (LZDoor)
    0xa4571e1c: 'ck_blstdoor',     # CK blast door
    0xa4571e3b: 'ck_bigblstdr',    # CK Big blast door
    0xa4571e5a: 'ck_smblstdr',     # CK Small blast door
    0x6023d7a4: 'ck_gdoor',        # CK garage door (Gdoor*)
    # pickups and placed characters / vehicles
    0x03ba691f: 'armor',           # Armor Pick-Up (w_armor*)
    0xfd433075: 'goldengun',       # GoldenGun* (display label is a generic "Spawn Point")
    0x0accd60d: 'goldengun',       # Golden Gun Pick-Up (w_gg)
    0xdc684d2e: 'foo_killertank',  # tankheadkill (Foo_killertank*)
    0xc1d5809e: 'tanktop',         # TankTop* (AI template slot used as a prop)
    0x9354aa36: 'oddjob',          # Oddjob2
    0xe11a27c9: 'goldfinger',      # Goldfinger
    0x6eb011c5: 'rpg',             # RPG Pick-Up (w_RPG*)
    0x6eb00763: 'p90',             # P90 Pick-Up (w_P90*)
    0x82a58c34: 'mamba',           # Mamba Pick-Up (w_mamba*)
    0x80081516: 'railgun',         # Rail Gun Pick-Up (w_railgun*)
    0x5249c064: 'detonator',       # Detonator Pick-Up (w_det*)
    0x4ea50f2a: 'grenade',         # Grenade Pick-Up (w_grenade*)
    0x6eafd21f: 'ar4_commando',    # AR4 Commando (w_AR4*)
    0x2832035b: 'minigun',         # Mini Gun Pick-Up (w_MiniGun*)
    0xfd99f78a: 'longbow',         # Long Bow Pick-Up (w_LB/w_longbow)
    0xcce906ce: 'jackal_347',      # Jackal Pick-Up (w_Jackal*)
    0x9047c2e4: 'prox_mine',       # Prox Mine Pick-Up (w_Mine/w_prox*)
    0x6750be8b: 'omen',            # Omen Pick-Up (w_omen*)
    0x83280e66: 'viper',           # Viper Pick-Up (w_viper)
    0x55a6e142: 'dt_button_stand', # Deathtrap Button (stand) (STR_BUTTON*)
    0x0b101b9f: 'mini_game_panel', # Mini Game Panel (STR_Button*)
}

# ------------------------------------------------------------------ CRT table
AI_MODEL_STRINGS_OFF = 0x90B5       # 95 NUL-separated model names
AI_HASHES_OFF = 0xB60               # 95 x {u32 hash, u32 kind, u32 flags, u32 idx}
AI_TABLE_COUNT = 95


def load_ai_model_table(work):
    """Return {w1: model stem} from Templates.CRT's parallel arrays.

    The Nth AI template hash at 0xB60+16*N corresponds to the Nth NUL string at
    0x90B5, which is the character model resource used for that template.
    This is the game's authoritative mapping (verified: every hash-resolvable
    template name lands on its matching model string at the same index).
    """
    import struct
    out = {}
    try:
        path = os.path.join(work, 'Templates.CRT')
        crt = open(path, 'rb').read()
        strings = []
        off = AI_MODEL_STRINGS_OFF
        for _ in range(AI_TABLE_COUNT):
            end = crt.find(b'\x00', off)
            if end < 0:
                break
            strings.append(crt[off:end].decode('latin-1').lower())
            off = end + 1
        if len(strings) != AI_TABLE_COUNT:
            return out
        for i, nm in enumerate(strings):
            h = struct.unpack_from('<I', crt, AI_HASHES_OFF + 16 * i)[0]
            if h and nm:
                out[h] = nm
    except Exception:
        pass
    return out


# AI template hash -> character model.  These 8 template names are themselves
# character resource names (Global.LVL / CRT strings), so the link is direct.
AI_W1_MODEL = {
    # Full fallback table from Templates.CRT's AI model-name array at 0x90B5.
    # The dynamic loader overrides this when Templates.CRT is available.
    0xfa83f179: 'gft1', 0x07b148c5: 'gft1', 0x87248fb4: 'gft1',
    0x0394644d: 'gft1', 0x90bd1591: 'gft1', 0x5fb059ed: 'gft1',
    0x4811eed1: 'gft1', 0x274f48c0: 'gft1', 0xea42449d: 'gft1',
    0xa847487e: 'gft1', 0xcfa084d7: 'gft1', 0xf210a519: 'gft1',
    0x3f867a12: 'gft1', 0xedaa34c7: 'gft1', 0xc907d7f7: 'gft1',
    0x4e4f4fdc: 'gft1', 0xe177fa8e: 'gft1',
    0x0259d73b: 'oddjob', 0xc8e3bde8: 'oddjob', 0x89069ea6: 'oddjob',
    0x87b7ae94: 'oddjob', 0x86ea9510: 'oddjob', 0x9c9d2a92: 'oddjob',
    0x2e1f7db3: 'oddjob', 0x5eec7b74: 'oddjob', 0xa11a8130: 'oddjob',
    0xf2b34862: 'oddjob', 0xb0562c01: 'oddjob', 0x05575531: 'oddjob',
    0xa7b0ad12: 'oddjob', 0xab8852bc: 'oddjob', 0xff459df3: 'oddjob',
    0x9f49a721: 'oddjob', 0x6671c458: 'oddjob', 0x06f17894: 'oddjob',
    0x8aad202e: 'oddjob', 0x0c548bd0: 'oddjob', 0x102ba417: 'oddjob',
    0xfb68cf59: 'oddjob', 0x9354aa36: 'oddjob', 0x838ca0ef: 'oddjob',
    0xa2db5056: 'dnl1', 0x3822005d: 'dnl1', 0x790c75a7: 'dnl1', 0x63b5a338: 'dnl1',
    0xa2bb2844: 'dnl3', 0x3801d84b: 'dnl3', 0x7ffc3ed7: 'dnl3', 0x63b5a319: 'dnl3',
    0x7bb08b30: 'dr_no', 0xf7a09ff8: 'dr_no', 0x54e5400c: 'dr_no',
    0xe3e7dce0: 'goldfinger', 0x4d6f4148: 'goldfinger',
    0xa33edded: 'goldfinger', 0xe11a27c9: 'goldfinger',
    0x23fef26b: 'goldfinger', 0xa4aea584: 'goldfinger',
    0x12d3e3ae: 'goldfinger', 0xf07ace4e: 'goldfinger',
    0x53b1f586: 'goldeneye', 0x8122fb4d: 'goldeneye',
    0x34b856b5: 'goldeneye', 0x34b1e379: 'goldeneye', 0x624dd34f: 'goldeneye',
    0xb093d063: 'octoguard', 0xb08d5d27: 'octoguard',
    0x52a4c2be: 'octoguard', 0x512ddafc: 'octoguard',
    0x713f28be: 'pussy_galore', 0x84ce7997: 'pussy_galore',
    0xc6eae5b4: 'scaramanga', 0x09cfb056: 'scaramanga',
    0x8a7f636f: 'scaramanga', 0xca955d31: 'scaramanga',
    0x39b28fe5: 'spectre_guard', 0xba6242fe: 'spectre_guard', 0x8125bbef: 'spectre_guard',
    0xc1d5809e: 'tank',
    0xe6abb636: 'triad_informant', 0x85f359aa: 'triad_informant', 0xb8c4ae31: 'triad_informant',
    0x6c88109a: 'triad_thug', 0xed37c3b3: 'triad_thug',
    0xb88ab09a: 'v_miniosp',
    0x97700945: 'vt_bot', 0x586eb04c: 'vt_bot',
    0xdbe1a2fd: 'xenia_onatopp', 0x8f27ab9b: 'xenia_onatopp',
    0xd2dd7722: 'xenia_onatopp', 0x2e3110f8: 'xenia_onatopp', 0xa12445c6: 'xenia_onatopp',
}

# The other ~80 AI templates are weapon/archetype variants ("P90 Example",
# "Mamba Example", ...) whose resolved CRT records carry only an animation-set
# hash (+0x10) and a weapon hash (+0x14) - no character-model hash.  Their
# appearance is encoded in the spawn's instance name instead, with a very
# regular scheme (verified across all 706 AI spawns):
AI_NAME_MODEL = [
    ('finalocto', 'octoguard'),
    ('tanktop', 'tanktop'),
    ('oddjob', 'oddjob'),
    ('goldfinger', 'goldfinger'),
    ('e_r10_xenia', 'xenia_onatopp'),
    ('e_drno', 'dr_no'),
    ('e_miniosp', 'v_miniosp'),
    ('rocketman', 'foo_rocket'),
    ('cine_osprey', 'v_osprey'),
    ('e_r9_tt_informant', 'triad_informant'),
    # Instance-name prefixes are NOT reliable model indicators: the CRT table
    # above is authoritative.  Only keep patterns that are direct evidence.
    ('e_gfe_', 'gft1'),             # fallback only, when the CRT table is absent
]

# Templates that place different models depending on the instance name.
VARIABLE_W1 = {
    0x8960a32f: 'decoration',      # "No Collision": bkd_*, zip_line*, airlock, ...
    0x1a0d9a75: 'zipline',         # zip_line00..07
}

# These place no .nsbmd at all: AI paths, spawns, script hints, ladders.
# A name guess here is what made the old viewer draw blinky/dt_freeze/gunslinger
# at every waypoint.
NO_MODEL_W1 = {
    0x5866f801,  # Waypoint
    0x1d6115f5,  # Spawn Point (dm_start_*)
    0x58cccac7,  # Spawn Point
    0x58cccaa8,  # Spawn Point
    0x1f05a5fa,  # Debug Player Warp
    0x3973bb0b,  # Help Hint Panel
    0x85643822,  # Ladder (level geometry, no global model)
}

# Instance-name -> model for names that are not literally a model file name.
# Keys are lowercase with digits removed (see _key).
NAME_HINTS = {
    'w_armor': 'armor', 'w_goldengun': 'goldengun', 'w_gg': 'goldengun',
    'w_rpg': 'rpg', 'w_p': 'p90', 'w_mamba': 'mamba', 'w_railgun': 'railgun',
    'w_det': 'detonator', 'w_gren': 'grenade', 'w_ar': 'ar4_commando',
    'w_minigun': 'minigun', 'w_lb': 'longbow', 'w_longbow': 'longbow',
    'w_jackal': 'jackal_347', 'w_mine': 'prox_mine', 'w_prox': 'prox_mine',
    'w_omen': 'omen', 'w_viper': 'viper',
    'barrelgold': 'barrel', 'barrelweapon': 'barrel',
    'glbrkable': 'gl_brkable', 'laserglass': 'foo_laserglass',
    'foovgls': 'v2_gls',
    'fortknoxdr': 'fortknoxdr', 'volcdr': 'volcdoor', 'funhousdr': 'funhous_dr',
    'vaulthdoor': 'vaulthdoor', 'trackdoor': 'trackdoor', 'atldoor': 'atl_door',
    'atlddoor': 'atl_ddoor', 'casdoor': 'casino_dr', 'bhdor': 'bh_doors',
    'lzdoor': 'lz_door', 'ckblstdoor': 'ck_blstdoor', 'ckbigblstdr': 'ck_bigblstdr',
    'cksmblstdr': 'ck_smblstdr', 'boredrframe': 'bore_drframe',
    'strbutton': 'dt_button_stand', 'strhint': 'info_box',
    'goldengun': 'goldengun', 'tanktop': 'tanktop', 'oddjob': 'oddjob',
    'finalocto': 'octoguard', 'cart': 'cart01',
}

# zip1 -> zip_line01, zip2 -> zip_line02, ... (only 00..07 exist in this ROM)
def zipline_stem(name, index):
    import re
    m = re.search(r'zip_?line?_?(\d+)', name.lower())
    if not m:
        m = re.search(r'zip(\d+)', name.lower())
    if not m:
        return None
    n = int(m.group(1))
    if n > 7:
        n = 7
    stem = 'zip_line%02d' % n
    return stem if stem in index else None


def _key(name):
    import re
    return re.sub(r'[^\w]', '', str(name).lower())


def builtin(w1, name, index, is_ai=False, ai_table=None):
    """Return (stem, how) or (None, None).

    `index` is the resolver's dict of available model stems -> path.
    """
    if w1 in NO_MODEL_W1:
        return None, 'none'
    if w1 in W1_MODEL:
        stem = W1_MODEL[w1]
        return (stem, 'builtin') if stem in index else (None, None)
    if is_ai:
        stem = (ai_table or {}).get(w1) or AI_W1_MODEL.get(w1)
        if stem and stem in index:
            return stem, 'builtin'
        low = str(name).lower()
        for pattern, stem in AI_NAME_MODEL:
            if pattern in low and stem in index:
                return stem, 'ai-name'
        return None, None
    hint = NAME_HINTS.get(_key(name))
    if hint and hint in index:
        return hint, 'builtin'
    kind = VARIABLE_W1.get(w1)
    if kind == 'zipline':
        stem = zipline_stem(name, index)
        return (stem, 'builtin') if stem else (None, None)
    if kind == 'decoration':
        k = _key(name)
        hint = NAME_HINTS.get(k)
        if hint and hint in index:
            return hint, 'builtin'
        # try the lowercased instance name itself, then digit-stripped forms
        for c in (name.lower(), name.lower().rstrip('0123456789'),
                  ''.join(ch for ch in name.lower() if not ch.isdigit())):
            if c in index:
                return c, 'builtin'
        # decorations are named after level models: longest stem that is a
        # prefix of the instance name (e.g. airLockHall01 -> airlock).
        pref = [s for s in index if len(s) >= 4 and len(c) >= 4
                and (c == s or c.startswith(s) or s.startswith(c))]
        if pref:
            return max(pref, key=len), 'builtin'
        return None, None
    return None, None

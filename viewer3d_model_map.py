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


def builtin(w1, name, index, is_ai=False):
    """Return (stem, how) or (None, None).

    `index` is the resolver's dict of available model stems -> path.
    """
    if w1 in NO_MODEL_W1:
        return None, 'none'
    if w1 in W1_MODEL:
        stem = W1_MODEL[w1]
        return (stem, 'builtin') if stem in index else (None, None)
    if is_ai:
        # Enemy appearance comes from the AI template record, not the spawn
        # name.  Only explicit overrides above are trusted.
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

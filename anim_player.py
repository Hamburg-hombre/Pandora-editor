"""Research prototype: evaluate ONE frame of a NITRO (NDS) model + skeleton
animation and emit textured triangles for a software rasterizer.

Location: this file deliberately lives in the temp folder.  It only *reads*
from the Pandora-editor project (imports `nitro`, `nitro_anim`, `scene`).

Public interface
----------------
    model_anim_pairs(work) -> [(model_path, anim_path, anim_ext), ...]
    frame_triangles(model_path, anim_path, frame, shared_tex=None,
                    apply_aux=True) -> (triangles, info)

    triangles : [(verts, uvs, image), ...]
        verts = [(x, y, z), (x, y, z), (x, y, z)]  world-space floats
        uvs   = [(u, v), (u, v), (u, v)]          already through MatInfo.uv
        image = PIL RGBA Image, or None for untextured materials
    info : dict(frames=N, name=..., errors=[...], ...)

What is applied
---------------
* NSBCA: translation (tx/ty/tz), rotation (pivot + basis matrices) and scale
  (sx/sy/sz) curves.  Channel values are absolute (they replace the static
  node value).  Channels that are absent from a track keep the node's static
  value, exactly as in the original model.
* The node hierarchy is walked with the *same* rules as `nitro.shape_draws`,
  including `scaling_rule == 1` (Maya inverse-scale cancellation).
* NSBTA (optional sibling): UV translation channels 3/4 (sampled flag 0x1000
  and fixed flag 0x3000) and UV scale channels 0/1 (fixed flag 0x3000),
  added onto / multiplied into MatInfo's tm_trans / tm_scale.
* NSBTP (optional sibling): texture and palette swaps keyed by material name
  (last key with frame <= requested frame).  A fresh MatInfo is built for the
  swapped names so image()/uv() use the replacement texture.

What is NOT applied (documented limitations)
--------------------------------------------
* No interpolation between sampled keys: the nearest (floor) sample is used.
* NSBMA material animation (diffuse/ambient/specular/emission/alpha) is
  ignored -- the output triangle tuple carries no colour field.
* NSBTA rotation channel 2 is ignored (no animated sample in this dataset;
  it is always the "no animation" value 0x2000).
* Skinned meshes that rely on GX MTX_RESTORE (vertex `restore_idx`) and
  billboard SBC commands (BB, NODEMIX) are ignored, as in `shape_draws`.
* Rotation curves are treated as absolute orientations, not bind-relative;
  this is correct for the rigid-node animations in the tested files.
* Samples beyond a curve's declared end clamp to the last sample; frames >=
  `frames` wrap modulo the animation length before sampling.
"""
import os
import sys

PROJECT = r'C:\Users\Heumanyoung\Documents\gera-tools\Pandora-editor'
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

import nitro
import nitro_anim
from scene import MatInfo

MODEL_EXT = '.nsbmd'
ANIM_EXT = '.nsbca'
AUX_EXTS = ('.nsbta', '.nsbtp', '.nsbma')


# ---------------------------------------------------------------------------
# discovery
def model_anim_pairs(work):
    """List (model_path, anim_path, anim_ext) pairs that share a filename stem.

    `work` is normally a directory (searched recursively).  A single file is
    also accepted; its directory is searched.  Matching is case-insensitive;
    returned paths preserve the on-disk spelling.  Extension is lower-cased.
    """
    root = work if os.path.isdir(work) else os.path.dirname(os.path.abspath(work))
    models, anims = {}, {}
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            path = os.path.join(dirpath, fn)
            stem, ext = os.path.splitext(fn)
            ext = ext.lower()
            key = stem.lower()
            if ext == MODEL_EXT:
                models.setdefault(key, []).append(path)
            elif ext == ANIM_EXT:
                anims.setdefault(key, []).append(path)
    out = []
    for key in sorted(set(models) & set(anims)):
        for mp in sorted(models[key]):
            for ap in sorted(anims[key]):
                out.append((mp, ap, ANIM_EXT))
    return out


# ---------------------------------------------------------------------------
# curve evaluation
def _sample_value(curve, frame):
    """Return the animation value of one curve at `frame` (step sampling)."""
    if curve.get('const'):
        return curve.get('value')
    samples = curve.get('samples') or []
    if not samples:
        return None
    start = curve.get('start', 0)
    rate = curve.get('rate', 1) or 1
    i = (frame - start) // rate
    if i < 0:
        i = 0
    elif i >= len(samples):
        i = len(samples) - 1
    return samples[i].get('value')


def _sample_rot_idx(curve, frame):
    if curve.get('const'):
        return curve.get('rot_idx')
    samples = curve.get('samples') or []
    if not samples:
        return None
    start = curve.get('start', 0)
    rate = curve.get('rate', 1) or 1
    i = (frame - start) // rate
    if i < 0:
        i = 0
    elif i >= len(samples):
        i = len(samples) - 1
    return samples[i].get('rot_idx')


def _rotation_matrix(anim, idx, errors):
    """Decode a rotation index (bit15 = pivot, else basis) into file-order R."""
    if idx is None:
        return None
    mats = anim.get('matrices', {})
    if idx & 0x8000:
        key = idx & 0x7FFF
        p = dict(mats.get('pivot', [])).get(key)
        if p is None:
            errors.append('pivot rotation %d missing' % key)
            return None
        return nitro.pivot_matrix(p['form'], p['neg_i'], p['neg_c'],
                                  p['neg_d'], p['a'], p['b'])
    key = idx & 0x7FFF
    b = dict(mats.get('basis', [])).get(key)
    if b is None:
        errors.append('basis rotation %d missing' % key)
        return None
    # decode_basis() returns A, B, C as *rows* of the file-order matrix;
    # verified against the adjacent pivot rotations in a_dgn_head.nsbca.
    return [list(b['A']), list(b['B']), list(b['C'])]


def _animated_local_matrices(mdl, anim, frame, errors):
    """Build one 4x4 local matrix per node, mirroring `shape_draws`."""
    locals_ = []
    for ni, (_name, nd) in enumerate(mdl.nodes):
        T = list(nd['T'])
        R = [list(row) for row in nd['R']]
        S = list(nd['S'])
        for track in anim.get('tracks', []):
            if track['target'] != ni:
                continue
            chans = dict(track.get('channels', []))
            for comp, axis in (('tx', 0), ('ty', 1), ('tz', 2),
                               ('sx', 0), ('sy', 1), ('sz', 2)):
                c = chans.get(comp)
                if c is None:
                    continue
                val = _sample_value(c, frame)
                if val is None:
                    errors.append('%s: node %d %s has no sample at frame %d'
                                  % (anim.get('name'), ni, comp, frame))
                    continue
                (T if comp[0] == 't' else S)[axis] = val
            if 'rot' in chans:
                r = _rotation_matrix(anim, _sample_rot_idx(chans['rot'], frame), errors)
                if r is not None:
                    R = r
        # DS column-major transpose, exactly as in nitro.shape_draws().
        M = [[R[c][r] * S[c] for c in range(3)] + [T[r]] for r in range(3)]
        M.append([0, 0, 0, 1])
        locals_.append(M)
    return locals_


def _animated_draws(mdl, locals_):
    """Walk model.sbc exactly like nitro.shape_draws(), with animated locals."""
    world = [nitro.mat_ident() for _ in mdl.nodes]
    cur_node = 0
    cur_mat = 0
    out = []
    for c in mdl.sbc:
        k = c[0]
        if k == 'NODEDESC':
            _, node, parent, _store, _restore = c
            if parent == node:
                world[node] = locals_[node]
            else:
                P = world[parent]
                if mdl.scaling_rule == 1:            # Maya: cancel parent's scale
                    IS = mdl.nodes[parent][1]['IS']
                    P = nitro.mat_mul(P, [[IS[0], 0, 0, 0],
                                          [0, IS[1], 0, 0],
                                          [0, 0, IS[2], 0],
                                          [0, 0, 0, 1]])
                world[node] = nitro.mat_mul(P, locals_[node])
            cur_node = node
        elif k == 'NODE':
            cur_node = c[1]
        elif k == 'MAT':
            cur_mat = c[1]
        elif k == 'SHP':
            out.append(dict(si=c[1], mi=cur_mat, W=world[cur_node],
                            node=cur_node,
                            node_name=mdl.nodes[cur_node][0]))
    return out


# ---------------------------------------------------------------------------
# auxiliary animations
def _pick_anim(parsed, stem, errors, label):
    if not parsed or not parsed.get('anims'):
        return None
    for a in parsed['anims']:
        if a.get('name', '').lower() == stem.lower():
            return a
    return parsed['anims'][0]


def _bta_overlays(bta, frame, stem, errors):
    """material name -> (uv_scale_s, uv_scale_t, uv_trans_s, uv_trans_t)."""
    anim = _pick_anim(bta, stem, errors, 'BTA')
    out = {}
    if anim is None:
        return out
    for t in anim.get('tracks', []):
        chans = t.get('channels', [])
        if len(chans) < 5:
            continue
        def cv(ch):
            flags = ch.get('flags', 0)
            if flags == 0x3000:                       # fixed 1.3.12
                return nitro_anim.f_s12(int(ch.get('field', 0)) & 0xFFFF)
            if flags == 0x1000:                       # sampled 1.3.12
                samples = ch.get('samples') or []
                if not samples:
                    return None
                i = frame if 0 <= frame < len(samples) else len(samples) - 1
                return samples[max(0, i)]
            return None
        ss, st = cv(chans[0]), cv(chans[1])
        ts, tt = cv(chans[3]), cv(chans[4])
        out[t['name']] = (ss, st, ts, tt)
    return out


def _btp_swaps(btp, frame, stem, errors):
    """material name -> (texture name, palette name) after key lookup."""
    anim = _pick_anim(btp, stem, errors, 'BTP')
    out = {}
    if anim is None:
        return out
    texnames = anim.get('texnames', [])
    palnames = anim.get('palnames', [])
    for t in anim.get('tracks', []):
        keys = sorted(t.get('keys') or [], key=lambda k: k['frame'])
        if not keys:
            continue
        key = keys[0]
        for k in keys:
            if k['frame'] <= frame:
                key = k
            else:
                break
        tex = texnames[key['tex']] if key['tex'] < len(texnames) else None
        pal = palnames[key['pal']] if key['pal'] < len(palnames) else None
        if tex or pal:
            out[t['name']] = (tex, pal)
    return out


def _coerce_texblock(shared_tex):
    if shared_tex is None:
        return None
    if hasattr(shared_tex, 'tex_by_name'):            # TexBlock
        return shared_tex
    if getattr(shared_tex, 'tex', None) is not None:  # NSBMD / NSBTX
        return shared_tex.tex
    return None


# ---------------------------------------------------------------------------
# main entry point
def frame_triangles(model_path, anim_path, frame, shared_tex=None, apply_aux=True):
    """Evaluate `frame` of `anim_path` on `model_path`.

    Returns (triangles, info); see module docstring for the exact shapes.
    """
    errors = []
    frames = 0
    name = ''
    model_bytes = open(model_path, 'rb').read()
    anim_bytes = open(anim_path, 'rb').read()
    nsbmd = nitro.NSBMD(model_bytes)
    bca = nitro_anim.parse_nsbca(anim_bytes)
    bca_anim = _pick_anim(bca, os.path.splitext(os.path.basename(anim_path))[0],
                          errors, 'BCA')
    if bca_anim is None:
        return [], dict(frames=0, name='', errors=['no animation in NSBCA'])
    frames = bca_anim['frames']
    name = bca_anim['name']
    if frame < 0:
        errors.append('negative frame %d clamped to 0' % frame)
        frame = 0
    if frames > 0 and frame >= frames:
        frame %= frames

    base = os.path.splitext(os.path.basename(model_path))[0]
    stem = os.path.splitext(os.path.basename(anim_path))[0]
    anim_dir = os.path.dirname(os.path.abspath(anim_path))

    # optional siblings
    bta = btp = None
    if apply_aux:
        for ext, label, parser in (('.nsbta', 'BTA', nitro_anim.parse_nsbta),
                                   ('.nsbtp', 'BTP', nitro_anim.parse_nsbtp)):
            sp = os.path.join(anim_dir, stem + ext)
            if os.path.exists(sp):
                try:
                    parsed = parser(open(sp, 'rb').read())
                    if label == 'BTA':
                        bta = parsed
                    else:
                        btp = parsed
                except Exception as e:
                    errors.append('%s %s: %s: %s' % (label, os.path.basename(sp),
                                                     type(e).__name__, e))
            else:
                errors.append('%s sibling %s not found (ignored)'
                              % (label, os.path.basename(sp)))
        # NSBMA is parsed only to report that it is intentionally unused.
        bmap = os.path.join(anim_dir, stem + '.nsbma')
        if os.path.exists(bmap):
            errors.append('NSBMA sibling present but material animation is ignored')

    bta_ov = _bta_overlays(bta, frame, stem, errors) if bta else {}
    btp_sw = _btp_swaps(btp, frame, stem, errors) if btp else {}

    triangles = []
    img_cache = {}
    used_mats = set()
    draw_count = 0
    for mdl in nsbmd.models:
        tb = nsbmd.tex or _coerce_texblock(shared_tex)
        infos = [MatInfo(base, m, tb) for m in mdl.materials]
        locals_ = _animated_local_matrices(mdl, bca_anim, frame, errors)
        draws = _animated_draws(mdl, locals_)
        draw_count += len(draws)
        for dr in draws:
            si, mi = dr['si'], dr['mi']
            if mi < len(infos):
                info = infos[mi]
            else:
                errors.append('draw shape %d: material %d out of range'
                              % (si, mi))
                info = MatInfo(base, dict(name='none', tex=None, pal=None,
                                          wrap=0, tm_scale=(1, 1),
                                          tm_trans=(0, 0)), tb)
            mname = info.mat.get('name', '') if info.mat else ''
            # aux overlays are per material name; rebuild MatInfo when swapped
            if mname in btp_sw:
                texname, palname = btp_sw[mname]
                swapped = dict(info.mat)
                if texname and texname in (tb.tex_by_name if tb else {}):
                    swapped['tex'] = texname
                elif texname:
                    errors.append('BTP texture %r not in texture bank' % texname)
                if palname and (tb is None or palname in tb.palettes):
                    swapped['pal'] = palname
                elif palname:
                    errors.append('BTP palette %r not in texture bank' % palname)
                info = MatInfo(base, swapped, tb)
                mname = swapped.get('name', mname)
            if mname in bta_ov:
                ss, st, ts, tt = bta_ov[mname]
                if ss is not None and ss != 0:
                    info.sx = (info.sx or 1.0) * ss
                if st is not None and st != 0:
                    info.sy = (info.sy or 1.0) * st
                if ts is not None:
                    info.tx += ts
                if tt is not None:
                    info.ty += tt
            W = dr['W']
            key = (id(mdl), mi, mname, info.tex.name if info.tex else None,
                   info.palname, info.sx, info.sy, info.tx, info.ty)
            if key not in img_cache:
                image = None
                if info.tex is not None:
                    try:
                        image = info.image()
                    except Exception as e:
                        errors.append('texture %s: %s: %s'
                                      % (info.tex.name, type(e).__name__, e))
                        image = None
                img_cache[key] = image
            image = img_cache[key]
            for tri in nitro.run_display_list(mdl.shapes[si]['dl'], mdl.pos_scale):
                verts = [nitro.mat_apply(W, v[:3]) for v in tri]
                uvs = [info.uv(v[3], v[4]) for v in tri]
                triangles.append((verts, uvs, image))
                used_mats.add(mname)

    info = dict(frames=frames, name=name, errors=errors,
                model=model_path, anim=anim_path, frame=frame,
                draws=draw_count, materials=sorted(used_mats),
                aux_applied=dict(bta=bool(bta_ov), btp=bool(btp_sw)))
    return triangles, info


if __name__ == '__main__':
    work = sys.argv[1] if len(sys.argv) > 1 else r'C:\Users\Heumanyoung\Documents\gera-tools\work'
    for mp, ap, ext in model_anim_pairs(work):
        tris, info = frame_triangles(mp, ap, 0)
        print(os.path.basename(mp), 'frames=%d tris=%d errors=%d'
              % (info['frames'], len(tris), len(info['errors'])))

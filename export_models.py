#!/usr/bin/env python3
"""Export Rogue Agent NSBMD models to OBJ + MTL + PNG textures.
usage: export_models.py <extracted_dir_or_nsbmd> <out_dir>

 - Every drawn shape becomes its own OBJ object named  shpNNN_<node>  (keep the shpNNN
   prefix when you rename/edit in Blender: the importer uses it to put geometry back).
 - One PNG per (texture, palette, mirror-mode); mirrored-repeat textures are baked into a
   2x tile and the UVs are adjusted to match. Per-material UV scale/offset is applied.
 - Vertex colours are written as extra 'v x y z r g b' values (Blender 3.x+ imports them).
"""
import os, sys
from PIL import Image
from nitro import *
from scene import MatInfo

def export(path, outdir, shared=None):
    n = NSBMD(open(path, 'rb').read())
    tb = n.tex or shared
    base = os.path.splitext(os.path.basename(path))[0]
    os.makedirs(outdir, exist_ok=True)
    obj, mtl = [], []
    voff = 1; made = set(); stats = dict(tris=0, tex=0)
    for mdl in n.models:
        infos = [MatInfo(base, m, tb) for m in mdl.materials]
        for dr in shape_draws(mdl):
            si, mi, W = dr['si'], dr['mi'], dr['W']
            info = infos[mi] if mi < len(infos) else MatInfo(base, dict(name='none', tex=None, pal=None, wrap=0,
                                                                       tm_scale=(1, 1), tm_trans=(0, 0)), tb)
            if info.mname not in made:
                made.add(info.mname)
                mtl.append('newmtl %s\nKd 1 1 1' % info.mname)
                if info.tex is not None:
                    try:
                        fp = os.path.join(outdir, info.png_name)
                        if not os.path.exists(fp):
                            info.image().save(fp)
                        mtl.append('map_Kd %s' % info.png_name); stats['tex'] += 1
                    except Exception as e:
                        print('  tex fail', info.tex.name, e)
            obj.append('o shp%03d_%s' % (si, dr['node_name']))
            obj.append('usemtl %s' % info.mname)
            for t in run_display_list(mdl.shapes[si]['dl'], mdl.pos_scale):
                ids = []
                for v in t:
                    x, y, z = mat_apply(W, v[:3])
                    u, vv = info.uv(v[3], v[4])
                    nx, ny, nz = mat_apply_dir(W, v[5:8])
                    r, g, b = v[9]
                    obj.append('v %.6f %.6f %.6f %.3f %.3f %.3f' % (x, y, z, r, g, b))
                    obj.append('vt %.6f %.6f' % (u, vv))
                    obj.append('vn %.4f %.4f %.4f' % (nx, ny, nz))
                    ids.append(voff); voff += 1
                obj.append('f ' + ' '.join('%d/%d/%d' % (i, i, i) for i in ids))
                stats['tris'] += 1
    open(os.path.join(outdir, base + '.obj'), 'w').write('mtllib %s.mtl\n' % base + '\n'.join(obj) + '\n')
    open(os.path.join(outdir, base + '.mtl'), 'w').write('\n'.join(mtl) + '\n')
    return stats

if __name__ == '__main__':
    src, out = sys.argv[1], sys.argv[2]
    files = []
    if os.path.isfile(src): files = [src]
    else:
        for r, _, fs in os.walk(src):
            files += [os.path.join(r, f) for f in fs if f.lower().endswith('.nsbmd')]
    shared_cache = {}
    bad = 0
    for p in sorted(files):
        d = os.path.dirname(p)
        if d not in shared_cache:
            sh = [f for f in os.listdir(d) if f.lower().endswith('.nsbtx')]
            shared_cache[d] = NSBTX(open(os.path.join(d, sh[0]), 'rb').read()).tex if sh else None
        rel = os.path.relpath(d, src) if not os.path.isfile(src) else ''
        try:
            export(p, os.path.join(out, rel), shared_cache[d])
        except Exception as e:
            bad += 1; print('FAIL', p, e)
    print('done', len(files), 'files,', bad, 'failed')

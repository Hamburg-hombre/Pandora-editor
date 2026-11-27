"""Headless scene loader (no OpenGL / Qt): turns a level folder into numpy arrays.

Everything here is plain Python + numpy + Pillow, so it can be tested without a GPU.
Coordinates: level units (the 12.12 fixed point values divided by 4096), Y up - the same
space the OBJ exporter writes, so exported OBJs line up with what the viewer shows.
"""
import os, re, json, math, struct, shutil
import numpy as np
from nitro import NSBMD, NSBTX, shape_draws, run_display_list, mat_apply, mat_apply_dir
from scene import MatInfo
import objects_lp as lpmod
import collision as colmod
import viewer3d_model_map as model_map
import viewer3d_paths as vpaths

FILE_TYPES = ('nsbmd', 'cvhm', 'roo', 'lp', 'nsbtx')

# ------------------------------------------------------------------ small helpers
def euler_matrix(rx, ry, rz, order='YXZ', sign=1.0):
    """3x3 rotation from three angles in radians. `order` lists the axes in the order they are
    applied to a point (first letter first)."""
    if isinstance(sign, (int, float)):
        sign = (sign, sign, sign)
    rx, ry, rz = rx * sign[0], ry * sign[1], rz * sign[2]
    def R(axis, a):
        c, s = math.cos(a), math.sin(a)
        if axis == 'X':
            return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float64)
        if axis == 'Y':
            return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)
    ang = {'X': rx, 'Y': ry, 'Z': rz}
    m = np.eye(3)
    for ax in order:                     # applied first -> rightmost
        m = R(ax, ang[ax]) @ m
    return m


def brads_to_rad(v):
    return v * (2.0 * math.pi / 4096.0)


def trs_matrix(pos, euler_brads, order='YXZ', sign=1.0):
    r = euler_matrix(*(brads_to_rad(a) for a in euler_brads), order=order, sign=sign)
    m = np.eye(4)
    m[:3, :3] = r
    m[:3, 3] = pos
    return m


# ------------------------------------------------------------------ model meshes
class Batch:
    """All triangles of one model that share a material."""
    def __init__(self, name):
        self.name = name
        self.verts = np.zeros((0, 11), np.float32)   # x y z | u v | r g b | nx ny nz
        self.image = None                            # HxWx4 uint8, top row first (or None)
        self.tex_key = None
        self.repeat_s = self.repeat_t = True
        self.cull = 'back'                           # 'none' | 'back' | 'front'
        self.alpha = 1.0
        self.lit = False
        self.translucent = False
        self.cutout = False


class ModelData:
    def __init__(self, name, path):
        self.name, self.path = name, path
        self.batches = []
        self.bmin = np.array([0, 0, 0], np.float64)
        self.bmax = np.array([0, 0, 0], np.float64)
        self.tri_count = 0
        self.error = None


def _cull_mode(polyattr):
    back, front = bool(polyattr & 0x40), bool(polyattr & 0x80)
    if back and front:
        return 'none'
    if front:
        return 'back'          # draw front faces only -> cull back faces
    if back:
        return 'front'
    return 'back'


def load_model(path, shared_tex=None, use_textures=True):
    """Parse an .nsbmd into ModelData (positions already in the model's own world space)."""
    base = os.path.splitext(os.path.basename(path))[0]
    md = ModelData(base, path)
    try:
        n = NSBMD(open(path, 'rb').read())
    except Exception as e:                                    # keep going, show nothing
        md.error = '%s: %s' % (type(e).__name__, e)
        return md
    tb = n.tex or shared_tex
    groups = {}
    for mdl in n.models:
        infos = [MatInfo(base, m, tb) for m in mdl.materials]
        for dr in shape_draws(mdl):
            si, mi, W = dr['si'], dr['mi'], dr['W']
            if mi >= len(infos):
                continue
            info = infos[mi]
            key = (id(mdl), mi)
            g = groups.get(key)
            if g is None:
                b = Batch(info.mname)
                b.repeat_s, b.repeat_t = info.repeat_s, info.repeat_t
                pa = info.mat['polyattr'] if info.mat else 0
                b.cull = _cull_mode(pa)
                a5 = (pa >> 16) & 0x1F
                b.alpha = 1.0 if a5 == 0 else a5 / 31.0   # 0 means wireframe on DS: draw opaque
                b.lit = bool(pa & 0x0F)
                if use_textures and info.tex is not None:
                    try:
                        im = np.asarray(info.image(), dtype=np.uint8)
                        b.image = im
                        b.tex_key = (tb.base if tb else 0, info.png_name)
                        al = im[..., 3]
                        b.translucent = bool(((al > 8) & (al < 247)).any())
                        b.cutout = bool((al <= 8).any())
                    except Exception:
                        b.image = None
                g = groups[key] = (b, [])
            b, chunks = g
            rows = []
            for t in run_display_list(mdl.shapes[si]['dl'], mdl.pos_scale):
                for v in t:
                    x, y, z = mat_apply(W, v[:3])
                    u, vv = info.uv(v[3], v[4])
                    nx, ny, nz = mat_apply_dir(W, v[5:8])
                    r, gg, bb = v[9]
                    rows.append((x, y, z, u, vv, r, gg, bb, nx, ny, nz))
            if rows:
                chunks.append(np.array(rows, np.float32))
    lo = np.array([1e30] * 3)
    hi = np.array([-1e30] * 3)
    for b, chunks in groups.values():
        if not chunks:
            continue
        b.verts = np.concatenate(chunks)
        md.tri_count += len(b.verts) // 3
        lo = np.minimum(lo, b.verts[:, :3].min(0))
        hi = np.maximum(hi, b.verts[:, :3].max(0))
        md.batches.append(b)
    if md.batches:
        md.bmin, md.bmax = lo, hi
    return md


# ------------------------------------------------------------------ resolver
class ModelResolver:
    """Finds the .nsbmd that an Objects.LP placement should show.

    Resolution order: user overrides, the verified built-in table in
    viewer3d_model_map, the instance (CHR) name, then a per-template guess.
    Logical markers (waypoints, spawns, hints) and AI spawns are never given a
    guessed model - they are drawn as markers instead.
    """
    OVERRIDES = '_viewer3d_overrides.json'


    def __init__(self, work, level_dir):
        self.work, self.level_dir = work, level_dir
        self.level_name = os.path.basename(os.path.normpath(level_dir))
        self.index = {}
        for d in (os.path.join(work, 'Models'), level_dir):          # level folder overrides
            if os.path.isdir(d):
                for f in os.listdir(d):
                    if f.lower().endswith('.nsbmd'):
                        self.index[os.path.splitext(f)[0].lower()] = os.path.join(d, f)
        self.by_name, self.by_w1 = {}, {}
        p = vpaths.read_path(self.OVERRIDES, work)
        if os.path.isfile(p):
            try:
                j = json.load(open(p))
                self.by_name = {k.lower(): v.lower() for k, v in j.get('by_name', {}).items()}
                self.by_w1 = {k.upper(): v.lower() for k, v in j.get('by_w1', {}).items()}
            except Exception:
                pass
        self.auto_w1 = {}
        # Authoritative AI template -> model table from Templates.CRT.
        self.ai_model_map = model_map.load_ai_model_table(work)

    def save_overrides(self):
        with open(vpaths.save_path(self.OVERRIDES), 'w') as f:
            json.dump({'by_name': self.by_name, 'by_w1': self.by_w1}, f, indent=1, sort_keys=True)

    @staticmethod
    def _candidates(name):
        n = name.lower()
        out = [n, re.sub(r'\d+$', '', n), re.sub(r'_?\d+[a-z]?$', '', n),
               re.sub(r'_(top|bottom)$', '', n),
               re.sub(r'\d+', '', n)]          # corridor01_LP -> corridor_lp
        return [c for i, c in enumerate(out) if c and c not in out[:i]]

    def by_chr_name(self, name):
        for c in self._candidates(name):
            if c in self.index:
                return c
        return None

    def prepare(self, records):
        """records: [(w1, chr_name)] or [(w1, chr_name, is_ai)] - builds the
        fallback w1 propagation table.  Logical markers, AI spawns and
        variable-model templates are excluded: one member's model must not be
        copied to every placement sharing the template."""
        self.auto_w1 = {}
        for rec in records:
            w1, name = rec[0], rec[1]
            is_ai = rec[2] if len(rec) > 2 else False
            if (w1 == 0 or is_ai or w1 in model_map.NO_MODEL_W1
                    or w1 in model_map.VARIABLE_W1):
                continue
            m = self.by_chr_name(name)
            if m:
                self.auto_w1.setdefault(w1, m)

    def resolve(self, w1, name, is_ai=False):
        """-> (model stem or None, how) with how in
        override/builtin/name/template/None."""
        m = self.by_name.get(name.lower())
        if m is None:
            m = self.by_w1.get('%08X' % w1)
        if m and m in self.index:
            return m, 'override'
        m, how = model_map.builtin(w1, name, self.index, is_ai,
                                   self.ai_model_map, self.level_name)
        if how == 'none':
            return None, None            # logical marker: draw a marker only
        if m:
            return m, how
        if is_ai:
            return None, None
        m = self.by_chr_name(name)
        if m:
            return m, 'name'
        m = self.auto_w1.get(w1)
        if m:
            return m, 'template'
        return None, None

    def path_of(self, stem):
        return self.index.get(stem)


# ------------------------------------------------------------------ level
class ObjectInst:
    def __init__(self, idx, rec, name):
        self.idx, self.rec, self.name = idx, rec, name
        self.kind = 'trigger' if rec.is_trigger else ('ai' if rec.is_ai else 'static')
        self.model = None           # model stem
        self.how = None
        self.visible = True
        self.dirty = False

    @property
    def pos(self):
        return np.array(self.rec.position, np.float64)

    @property
    def euler(self):
        return struct.unpack('<3h', self.rec.tail6)

    def set_pos(self, p):
        self.rec.set_position(*[float(v) for v in p])
        self.dirty = True

    def set_euler(self, e):
        e = [((int(round(v)) + 2048) % 4096) - 2048 for v in e]
        self.rec.tail6 = struct.pack('<3h', *e)
        self.dirty = True

    def trigger_box(self):
        if not self.rec.is_trigger or not self.rec.extra6 or len(self.rec.extra6) != 24:
            return None
        x0, y0, z0, x1, y1, z1 = struct.unpack('<6i', self.rec.extra6)
        p = self.rec.pos
        f = 1.0 / 4096.0
        return (np.array([(p[0] + x0) * f, (p[1] + y0) * f, (p[2] + z0) * f]),
                np.array([(p[0] + x1) * f, (p[1] + y1) * f, (p[2] + z1) * f]))


class FileEntry:
    def __init__(self, kind, path, label=None):
        self.kind, self.path = kind, path
        self.label = label or os.path.basename(path)
        self.visible = True
        self.info = ''


class Level:
    def __init__(self, work, name):
        self.work, self.name = work, name
        self.dir = os.path.join(work, 'Levels', name)
        self.files = []                  # FileEntry for every relevant file
        self.models = {}                 # level-folder room models: path -> ModelData
        self.sky = None                  # ModelData or None
        self.collision = {}              # path -> (lines Nx3 float32 (pairs), colors Nx3)
        self.rooms = []                  # [{'name','bmin','bmax','flags'}]
        self.objects = []
        self.chr = None
        self.chr_path = None
        self.chr_dirty = False
        self.lp = None
        self.lp_path = None
        self.template_names = {}         # w1 -> readable template name
        self.paths = []                  # [(nodes Nx3, edges [(a,b)])]
        self.resolver = None
        self.bmin = np.array([-10.0] * 3)
        self.bmax = np.array([10.0] * 3)
        self.spawn = None                # (pos, yaw_rad)
        self.warnings = []

    # ---- loading
    @classmethod
    def load(cls, work, name, model_cache=None, log=None):
        lv = cls(work, name)
        lg = log or (lambda *a: None)
        cache = model_cache if model_cache is not None else {}
        d = lv.dir
        if not os.path.isdir(d):
            raise FileNotFoundError(d)
        shared = None
        for f in sorted(os.listdir(d)):
            if f.lower().endswith('.nsbtx'):
                try:
                    shared = NSBTX(open(os.path.join(d, f), 'rb').read()).tex
                    e = FileEntry('nsbtx', os.path.join(d, f))
                    e.info = '%d textures' % len(shared.textures) if shared else ''
                    lv.files.append(e)
                except Exception as ex:
                    lv.warnings.append('%s: %s' % (f, ex))
        lv.shared_tex = shared
        for f in sorted(os.listdir(d)):
            p, low = os.path.join(d, f), f.lower()
            if low.endswith('.nsbmd'):
                md = cache.get(p)
                if md is None:
                    md = cache[p] = load_model(p, shared)
                e = FileEntry('nsbmd', p)
                e.info = md.error or '%d tris' % md.tri_count
                lv.files.append(e)
                if md.error:
                    lv.warnings.append('%s: %s' % (f, md.error))
                elif low.startswith('sky'):
                    lv.sky = md
                else:
                    lv.models[p] = md
            elif low.endswith('.cvhm'):
                try:
                    c = colmod.parse(p)
                    mesh = c.meshes[-1]                       # detailed H_ mesh for version 2
                    lv.collision[p] = _collision_lines(mesh)
                    e = FileEntry('cvhm', p)
                    e.info = '%d tris' % len(mesh.tris)
                    lv.files.append(e)
                except Exception as ex:
                    lv.warnings.append('%s: %s' % (f, ex))
            elif low == 'global.roo':
                try:
                    roo = colmod.Roo(open(p, 'rb').read(), p)
                    for r in roo.rooms:
                        b = r['box']
                        lv.rooms.append(dict(name=r['name'] or 'room%d' % r['index'],
                                             bmin=np.array(b[:3], np.float64),
                                             bmax=np.array(b[3:], np.float64), flags=r['flags']))
                    e = FileEntry('roo', p)
                    e.info = '%d rooms' % len(lv.rooms)
                    lv.files.append(e)
                except Exception as ex:
                    lv.warnings.append('%s: %s' % (f, ex))
        lp_p, chr_p = os.path.join(d, 'Objects.LP'), os.path.join(d, 'CodeStrings.CHR')
        if os.path.isfile(lp_p):
            try:
                lv.lp = lpmod.parse(lp_p)
                lv.lp_path = lp_p
                lv.template_names = {d['w1']: d['name'] for d in lpmod.load_template_details(
                    os.path.join(work, 'Templates.CRT'), os.path.join(work, 'Global.LVL'))}
                lv.chr_path = chr_p if os.path.isfile(chr_p) else None
                lv.chr = lpmod.load_chr(chr_p) if lv.chr_path else None
                lv.resolver = ModelResolver(work, d)
                names = [lv.chr.strings[r.h1] if lv.chr and r.h1 < len(lv.chr.strings) else 'h1_%d' % r.h1
                         for r in lv.lp.records()]
                lv.resolver.prepare([(r.w1, n, r.is_ai)
                                      for r, n in zip(lv.lp.records(), names)])
                for i, (r, n) in enumerate(zip(lv.lp.records(), names)):
                    o = ObjectInst(i, r, n)
                    o.model, o.how = lv.resolver.resolve(r.w1, n, r.is_ai)
                    lv.objects.append(o)
                for p in lv.lp.paths():
                    nodes = np.array([p.node_pos(i) for i in range(p.node_count)], np.float64)
                    lv.paths.append((nodes.reshape(-1, 3), [(e[0], e[1]) for e in p.edges]))
                e = FileEntry('lp', lp_p)
                e.info = '%d placements, %d paths' % (len(lv.objects), len(lv.paths))
                lv.files.append(e)
            except Exception as ex:
                lv.warnings.append('Objects.LP: %s' % ex)
        lv.compute_bounds()
        lv.find_spawn()
        return lv

    def compute_bounds(self):
        """Bounds used by 'frame all': models + collision (rooms can be huge outliers)."""
        los, his = [], []
        for md in self.models.values():
            if md.batches:
                los.append(md.bmin); his.append(md.bmax)
        for lines, _c in self.collision.values():
            if len(lines):
                los.append(lines.min(0)); his.append(lines.max(0))
        if not los and self.rooms:
            los = [r['bmin'] for r in self.rooms]; his = [r['bmax'] for r in self.rooms]
        if not los and self.objects:
            ps = np.array([o.pos for o in self.objects])
            los, his = [ps.min(0)], [ps.max(0)]
        if los:
            self.bmin, self.bmax = np.minimum.reduce(los), np.maximum.reduce(his)

    def find_spawn(self):
        pick = None
        for pref in ('playerstart', 'player_start', 'dm_start_01', 'spawn'):
            for o in self.objects:
                if o.name.lower().startswith(pref):
                    pick = o
                    break
            if pick:
                break
        if pick is None:
            for o in self.objects:
                if o.kind == 'ai':
                    pick = o
                    break
        if pick is not None:
            self.spawn = (pick.pos.copy(), brads_to_rad(pick.euler[1]), pick)
        else:
            c = (self.bmin + self.bmax) / 2
            self.spawn = (c, 0.0, None)

    # ---- saving
    def save_lp(self, log=print):
        """Write Objects.LP (first write keeps a pristine copy in <work>/_backup)."""
        if self.lp is None:
            return False
        rel = os.path.relpath(self.lp_path, self.work)
        dest = os.path.join(self.work, '_backup', rel)
        if not os.path.exists(dest):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(self.lp_path, dest)
        data = self.lp.to_bytes()
        with open(self.lp_path, 'wb') as f:
            f.write(data)
        for o in self.objects:
            o.dirty = False
        log('saved %s (%d bytes)' % (self.lp_path, len(data)))
        return True

    def save_chr(self, log=print):
        """Write CodeStrings.CHR after a name edit (same _backup convention)."""
        if self.chr is None or not self.chr_path:
            return False
        rel = os.path.relpath(self.chr_path, self.work)
        dest = os.path.join(self.work, '_backup', rel)
        if not os.path.exists(dest):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(self.chr_path, dest)
        data = self.chr.to_bytes()
        with open(self.chr_path, 'wb') as f:
            f.write(data)
        self.chr_dirty = False
        log('saved %s (%d bytes)' % (self.chr_path, len(data)))
        return True


def _collision_lines(mesh):
    """Triangle edges as a flat (N*2,3) float32 array + per-vertex colour by surface slope."""
    v = np.array(mesh.verts, np.float64) / 4096.0
    t = np.array(mesh.tris, np.int64).reshape(-1, 3)
    if len(t) == 0:
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32)
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    n = np.cross(b - a, c - a)
    ln = np.linalg.norm(n, axis=1)
    ny = np.where(ln > 1e-12, n[:, 1] / np.maximum(ln, 1e-12), 0.0)
    col = np.empty((len(t), 3))
    col[:] = (0.75, 0.75, 0.80)                       # walls
    col[ny > 0.7] = (0.25, 0.9, 0.35)                 # floors (up-facing)
    col[ny < -0.7] = (0.35, 0.55, 1.0)                # ceilings
    segs = np.stack([a, b, b, c, c, a], axis=1).reshape(-1, 3)
    cols = np.repeat(col, 6, axis=0)
    return segs.astype(np.float32), cols.astype(np.float32)


# ------------------------------------------------------------------ misc
def list_levels(work):
    lv = os.path.join(work, 'Levels')
    if not os.path.isdir(lv):
        return []
    return sorted(d for d in os.listdir(lv) if os.path.isdir(os.path.join(lv, d)))


def box_lines(bmin, bmax):
    """12 edges of an axis-aligned box as (24,3)."""
    x0, y0, z0 = bmin
    x1, y1, z1 = bmax
    c = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    e = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
    return np.array([c[i] for pair in e for i in pair], np.float32)

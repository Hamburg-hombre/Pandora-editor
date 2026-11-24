"""OpenGL 3.3 scene widget: QOpenGLWidget + moderngl. All drawing, camera input and picking."""
import math, time
import numpy as np
import moderngl
from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from viewer3d_camera import DEFAULT_PAN_SPEED, Camera, ray_box, closest_on_axis, normalize
import viewer3d_scene_data as sd

TEX_VS = '''
#version 330
uniform mat4 mvp; uniform mat4 model;
in vec3 in_pos; in vec2 in_uv; in vec3 in_col; in vec3 in_nrm;
out vec2 uv; out vec3 col; out vec3 nrm; out vec3 wpos;
void main(){
  vec4 w = model * vec4(in_pos,1.0);
  wpos = w.xyz; uv = in_uv; col = in_col; nrm = mat3(model) * in_nrm;
  gl_Position = mvp * vec4(in_pos,1.0);
}'''
TEX_FS = '''
#version 330
uniform sampler2D tex; uniform bool use_tex; uniform bool use_vcol; uniform bool lit; uniform bool cutout;
uniform vec3 ambient; uniform vec3 light_col; uniform vec3 light_dir;
uniform bool fog_on; uniform vec3 fog_col; uniform float fog_near; uniform float fog_far; uniform vec3 eye;
uniform float alpha; uniform vec3 tint; uniform float tint_amt; uniform bool flat_col;
in vec2 uv; in vec3 col; in vec3 nrm; in vec3 wpos;
out vec4 frag;
void main(){
  vec4 c = vec4(1.0);
  if (use_tex) c = texture(tex, uv);
  if (cutout && c.a < 0.5) discard;
  vec3 rgb = c.rgb;
  if (use_vcol) rgb *= col;
  if (lit) {
    float nd = max(dot(normalize(nrm), -normalize(light_dir)), 0.0);
    rgb *= (ambient + light_col * nd);
  }
  if (fog_on) { float f = clamp((distance(wpos, eye) - fog_near) / max(fog_far - fog_near, 0.001), 0.0, 1.0); rgb = mix(rgb, fog_col, f); }
  rgb = mix(rgb, tint, tint_amt);
  frag = vec4(rgb, c.a * alpha);
}'''
LINE_VS = '''
#version 330
uniform mat4 mvp; uniform mat4 model;
in vec3 in_pos; in vec3 in_col; out vec3 col; out vec3 wpos;
void main(){ vec4 w = model * vec4(in_pos,1.0); wpos = w.xyz; col = in_col; gl_Position = mvp * vec4(in_pos,1.0); }'''
LINE_FS = '''
#version 330
uniform float alpha; uniform bool fog_on; uniform vec3 fog_col; uniform float fog_near; uniform float fog_far; uniform vec3 eye;
in vec3 col; in vec3 wpos; out vec4 frag;
void main(){
  vec3 rgb = col;
  if (fog_on) { float f = clamp((distance(wpos, eye) - fog_near) / max(fog_far - fog_near, 0.001), 0.0, 1.0); rgb = mix(rgb, fog_col, f*0.6); }
  frag = vec4(rgb, alpha);
}'''

KIND_COLORS = {'static': (0.45, 0.9, 0.5), 'ai': (1.0, 0.45, 0.35), 'trigger': (0.35, 0.6, 1.0),
               'spawn': (0.3, 0.95, 1.0), 'waypoint': (1.0, 0.85, 0.3), 'pickup': (1.0, 0.6, 0.2)}
AXIS_COL = [(1.0, 0.25, 0.25), (0.3, 1.0, 0.3), (0.35, 0.5, 1.0)]


def mat_bytes(m):
    return np.ascontiguousarray(np.asarray(m, np.float64).T, dtype='f4').tobytes()


def marker_kind(o):
    n = o.name.lower()
    if o.kind == 'trigger':
        return 'trigger'
    if o.kind == 'ai':
        return 'ai'
    if 'start' in n or n.startswith('spawn') or n.startswith('dm_'):
        return 'spawn'
    if 'wp' in n or 'waypoint' in n or n.startswith('ai_') or n.startswith('vtm'):
        return 'waypoint'
    if n.startswith('w_'):
        return 'pickup'
    return 'static'


class Env:
    def __init__(self):
        self.clear = (0.125, 0.141, 0.157)          # Pandora's canvas colour #202428
        self.ambient = 0.6
        self.light_col = 0.5
        self.light_dir = (-0.35, -1.0, -0.25)
        self.fog_on = False
        self.fog_col = (0.55, 0.62, 0.7)
        self.fog_near = 80.0
        self.fog_far = 400.0

    def to_dict(self):
        return dict(self.__dict__)

    def from_dict(self, d):
        for k, v in d.items():
            if hasattr(self, k):
                setattr(self, k, tuple(v) if isinstance(v, list) else v)


class GLView(QOpenGLWidget):
    objectSelected = Signal(int)        # object index or -1
    objectEdited = Signal(int)
    editBegan = Signal(int)
    cameraChanged = Signal()
    status = Signal(str)
    resized = Signal()
    orthoChanged = Signal(bool)

    @staticmethod
    def default_format():
        f = QSurfaceFormat()
        f.setVersion(3, 3)
        f.setProfile(QSurfaceFormat.CoreProfile)
        f.setDepthBufferSize(24)
        f.setSamples(4)
        f.setSwapInterval(1)
        return f

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.cam = Camera()
        self.level = None
        self.env = Env()
        # toggles
        self.show_type = {t: True for t in sd.FILE_TYPES}
        self.show_files = {}                 # path -> bool (missing = True)
        self.opt = dict(wireframe=False, cull=True, textures=True, vcolors=True, lighting=False, grid=True,
                        axes=True, sky=True, fog=False, markers=True, paths=True, labels=True,
                        translucent=True)
        self.euler_order, self.euler_sign = 'YXZ', (1.0, -1.0, 1.0)   # Y runs the other way
        self.tool = 'select'                 # select | move | rotate
        self.selected = -1
        self.ctx = None
        self._gl_models = {}                 # path -> [(batch, vao, tex)]
        self._tex_cache = {}
        self._line_cache = {}                # path -> (vao, count)
        self._marker_vao = None
        self._marker_n = 0
        self.markers_dirty = True
        self._obj_mats = {}
        self._drag = None
        self._press_pos = None
        self._moved = False
        self._frames = 0
        self._fps_t = time.time()
        self.fps = 0.0
        self.tri_drawn = 0
        self._hover_handle = -1
        self._cycle = None
        self.facing_offset = -90.0           # degrees, only for facing arrows / spawn camera (panel-angle units)

    # ------------------------------------------------------------------ level
    def set_level(self, level):
        self.makeCurrent() if self.ctx else None
        self._release_level_gpu()
        self.level = level
        self.selected = -1
        self._obj_mats.clear()
        self.show_files = {}
        self.markers_dirty = True
        self.ensure_models()                 # object models must load AFTER self.level is the new level
        self.markers_dirty = True
        self.cam.set_scene(level.bmin, level.bmax)
        self.go_spawn()
        self.update()

    def _release_level_gpu(self):
        for vao_n in self._line_cache.values():
            try:
                vao_n[0].release(); vao_n[1].release()
            except Exception:
                pass
        self._line_cache.clear()
        if self._marker_vao is not None:
            try:
                self._marker_vao[0].release(); self._marker_vao[1].release()
            except Exception:
                pass
            self._marker_vao = None

    def go_spawn(self):
        if not self.level or not self.level.spawn:
            return
        pos, yaw, _o = self.level.spawn
        yaw = yaw + math.radians(self.facing_offset)
        self.cam.set_scene(self.level.bmin, self.level.bmax)
        self.cam.look_from(pos, yaw)
        self.cameraChanged.emit()
        self.update()

    def frame_all(self):
        if self.level:
            self.cam.frame(self.level.bmin, self.level.bmax)
            self.cameraChanged.emit(); self.update()

    def focus_selected(self):
        o = self.sel_obj()
        if o is None:
            return self.frame_all()
        lo, hi = self._obj_bounds_world(o)
        self.cam.frame(lo, hi)
        self.cameraChanged.emit(); self.update()

    def sel_obj(self):
        if self.level and 0 <= self.selected < len(self.level.objects):
            return self.level.objects[self.selected]
        return None

    def select(self, idx, emit=True):
        self.selected = idx
        if emit:
            self.objectSelected.emit(idx)
        self.update()

    def object_changed(self, idx=None):
        if idx is None:
            self._obj_mats.clear()
        else:
            self._obj_mats.pop(idx, None)
        self.markers_dirty = True
        self.update()

    def set_facing_offset(self, deg):
        self.facing_offset = float(deg)
        self.markers_dirty = True
        self.update()

    def set_euler(self, order, sign):
        self.euler_order, self.euler_sign = order, sign
        self._obj_mats.clear()
        self.update()

    # ------------------------------------------------------------------ GL setup
    def initializeGL(self):
        self.ctx = moderngl.create_context(require=330)
        self.tex_prog = self.ctx.program(vertex_shader=TEX_VS, fragment_shader=TEX_FS)
        self.line_prog = self.ctx.program(vertex_shader=LINE_VS, fragment_shader=LINE_FS)
        self.white = self.ctx.texture((1, 1), 4, b'\xff\xff\xff\xff')
        self.dyn = self.ctx.buffer(reserve=4 * 6 * 4096, dynamic=True)
        self.dyn_vao = self.ctx.vertex_array(self.line_prog, [(self.dyn, '3f 3f', 'in_pos', 'in_col')])

    def _get_tex(self, batch):
        if batch.image is None:
            return None
        key = (batch.tex_key, batch.repeat_s, batch.repeat_t)
        t = self._tex_cache.get(key)
        if t is None:
            h, w = batch.image.shape[:2]
            t = self.ctx.texture((w, h), 4, np.ascontiguousarray(np.flipud(batch.image)).tobytes())
            t.filter = (moderngl.NEAREST, moderngl.NEAREST)      # the DS does not filter
            t.repeat_x, t.repeat_y = batch.repeat_s, batch.repeat_t
            self._tex_cache[key] = t
        return t

    def _gpu_model(self, md):
        g = self._gl_models.get(md.path)
        if g is None:
            g = []
            for b in md.batches:
                vbo = self.ctx.buffer(np.ascontiguousarray(b.verts, 'f4').tobytes())
                vao = self.ctx.vertex_array(self.tex_prog, [(vbo, '3f 2f 3f 3f', 'in_pos', 'in_uv', 'in_col', 'in_nrm')])
                g.append((b, vao, self._get_tex(b)))
            self._gl_models[md.path] = g
        return g

    def _lines(self, key, pts, cols):
        c = self._line_cache.get(key)
        if c is None:
            data = np.concatenate([pts, cols], axis=1).astype('f4')
            vbo = self.ctx.buffer(np.ascontiguousarray(data).tobytes())
            vao = self.ctx.vertex_array(self.line_prog, [(vbo, '3f 3f', 'in_pos', 'in_col')])
            c = self._line_cache[key] = (vao, vbo, len(pts))
        return c

    # ------------------------------------------------------------------ helpers
    def _file_on(self, entry_path, kind):
        return self.show_type.get(kind, True) and self.show_files.get(entry_path, True)

    def obj_matrix(self, o):
        m = self._obj_mats.get(o.idx)
        if m is None:
            m = sd.trs_matrix(o.pos, o.euler, self.euler_order, self.euler_sign)
            self._obj_mats[o.idx] = m
        return m

    def _model_for(self, o):
        if not o.model or not self.level.resolver:
            return None
        p = self.level.resolver.path_of(o.model)
        if not p:
            return None
        md = self._model_cache.get(p) if hasattr(self, '_model_cache') else None
        return md

    def attach_model_cache(self, cache):
        self._model_cache = cache

    def ensure_models(self):
        """Load (CPU side) every model the level's placements need."""
        if not self.level or not self.level.resolver:
            return
        for o in self.level.objects:
            if o.model:
                p = self.level.resolver.path_of(o.model)
                if p and p not in self._model_cache:
                    self._model_cache[p] = sd.load_model(p, None)

    def _obj_local_bounds(self, o):
        md = self._model_for(o)
        if md and md.batches:
            return md.bmin, md.bmax
        r = 0.4
        return np.array([-r] * 3), np.array([r] * 3)

    def _obj_bounds_world(self, o):
        tb = o.trigger_box()
        if tb is not None:
            return tb
        lo, hi = self._obj_local_bounds(o)
        m = self.obj_matrix(o)
        cs = np.array([[x, y, z, 1.0] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        w = (m @ cs.T).T[:, :3]
        return w.min(0), w.max(0)

    # ------------------------------------------------------------------ marker geometry
    def _build_markers(self):
        lv = self.level
        pts, cols = [], []
        if lv:
            for o in lv.objects:
                if not o.visible or not self._file_on(lv.lp_path, 'lp'):
                    continue
                kind = marker_kind(o)
                col = KIND_COLORS.get(kind, KIND_COLORS['static'])
                tb = o.trigger_box()
                if tb is not None:
                    seg = sd.box_lines(*tb)
                    pts.append(seg); cols.append(np.tile(col, (len(seg), 1)))
                    continue
                if o.model and self._model_for(o) is not None:
                    continue
                m = self.obj_matrix(o)
                r = 0.35
                local = np.array([[-r, 0, 0], [r, 0, 0], [0, -r, 0], [0, r, 0], [0, 0, -r], [0, 0, r]], np.float64)
                if o.kind == 'ai' or kind in ('spawn',):       # facing arrow along local -Z
                    arrow = np.array([[0, 0, 0], [0, 0, -r * 2.5], [0, 0, -r * 2.5], [r * 0.5, 0, -r * 1.8],
                                      [0, 0, -r * 2.5], [-r * 0.5, 0, -r * 1.8]], np.float64)
                    fr = sd.euler_matrix(0, math.radians(self.facing_offset), 0, 'YXZ', (1.0, self.euler_sign[1], 1.0))
                    local = np.vstack([local, arrow @ fr.T])
                w = (m @ np.hstack([local, np.ones((len(local), 1))]).T).T[:, :3]
                pts.append(w); cols.append(np.tile(col, (len(w), 1)))
            for nodes, edges in lv.paths:
                if len(nodes) == 0:
                    continue
                seen = set()
                seg = []
                for a, b in edges:
                    k = (min(a, b), max(a, b))
                    if k in seen or a >= len(nodes) or b >= len(nodes):
                        continue
                    seen.add(k); seg += [nodes[a], nodes[b]]
                for n in nodes:
                    for d in ((0.2, 0, 0), (0, 0.2, 0), (0, 0, 0.2)):
                        seg += [n - d, n + d]
                if seg:
                    s = np.array(seg)
                    pts.append(s); cols.append(np.tile((0.3, 0.85, 1.0), (len(s), 1)))
        if pts:
            P = np.vstack(pts).astype('f4'); C = np.vstack(cols).astype('f4')
            self._marker_n = len(P)
            data = np.ascontiguousarray(np.concatenate([P, C], axis=1))
            if self._marker_vao is not None:
                self._marker_vao[0].release(); self._marker_vao[1].release()
            vbo = self.ctx.buffer(data.tobytes())
            self._marker_vao = (self.ctx.vertex_array(self.line_prog, [(vbo, '3f 3f', 'in_pos', 'in_col')]), vbo)
        else:
            self._marker_n = 0
        self._marker_split = None
        self.markers_dirty = False

    # ------------------------------------------------------------------ painting
    def _set_common(self, prog, mvp, model, eye):
        prog['mvp'].write(mat_bytes(mvp))
        prog['model'].write(mat_bytes(model))
        prog['fog_on'].value = bool(self.opt['fog'])
        prog['fog_col'].value = tuple(self.env.fog_col)
        prog['fog_near'].value = float(self.env.fog_near)
        prog['fog_far'].value = float(self.env.fog_far)
        prog['eye'].value = tuple(float(v) for v in eye)

    def _draw_model(self, md, model_m, vp, eye, translucent_pass, tint=None):
        e = self.env
        prog = self.tex_prog
        mvp = vp @ model_m
        self._set_common(prog, mvp, model_m, eye)
        prog['ambient'].value = (e.ambient,) * 3
        prog['light_col'].value = (e.light_col,) * 3
        prog['light_dir'].value = tuple(e.light_dir)
        prog['tint'].value = tuple(tint) if tint else (1.0, 0.85, 0.2)
        prog['tint_amt'].value = 0.35 if tint else 0.0
        prog['use_vcol'].value = bool(self.opt['vcolors'])
        prog['lit'].value = False
        for b, vao, tex in self._gpu_model(md):
            is_trans = b.translucent or b.alpha < 0.99
            if is_trans != translucent_pass:
                continue
            if translucent_pass and not self.opt['translucent']:
                continue
            use_tex = tex is not None and self.opt['textures']
            if use_tex:
                tex.use(0)
                prog['tex'].value = 0
            prog['use_tex'].value = use_tex
            prog['lit'].value = bool(self.opt['lighting'] and b.lit)
            prog['cutout'].value = bool(b.cutout and use_tex)
            prog['alpha'].value = float(b.alpha)
            cull = b.cull if self.opt['cull'] else 'none'
            if cull == 'none' or self.opt['wireframe']:
                self.ctx.disable(moderngl.CULL_FACE)
            else:
                self.ctx.enable(moderngl.CULL_FACE)
                self.ctx.cull_face = 'back' if cull == 'back' else 'front'
            vao.render(moderngl.TRIANGLES)
            self.tri_drawn += len(b.verts) // 3

    def _draw_lines(self, vao, n, vp, model=None, alpha=1.0, mode=moderngl.LINES, eye=(0, 0, 0)):
        model = np.eye(4) if model is None else model
        self._set_common(self.line_prog, vp @ model, model, eye)
        self.line_prog['alpha'].value = alpha
        vao.render(mode, vertices=n)

    def _draw_dyn(self, pts, cols, vp, eye, alpha=1.0, overlay=False, mode=None):
        pts = np.asarray(pts, 'f4').reshape(-1, 3)
        cols = np.asarray(cols, 'f4').reshape(-1, 3)
        data = np.ascontiguousarray(np.concatenate([pts, cols], axis=1))
        if data.nbytes > self.dyn.size:
            self.dyn.orphan(data.nbytes * 2)
            self.dyn_vao = self.ctx.vertex_array(self.line_prog, [(self.dyn, '3f 3f', 'in_pos', 'in_col')])
        self.dyn.write(data.tobytes())
        if overlay:
            self.ctx.disable(moderngl.DEPTH_TEST)
        self._draw_lines(self.dyn_vao, len(pts), vp, alpha=alpha, eye=eye, mode=mode or moderngl.LINES)
        if overlay:
            self.ctx.enable(moderngl.DEPTH_TEST)

    def paintGL(self):
        ctx = self.ctx
        dpr = self.devicePixelRatioF()
        w, h = max(int(self.width() * dpr), 1), max(int(self.height() * dpr), 1)
        fbo = ctx.detect_framebuffer(self.defaultFramebufferObject())
        fbo.use()
        ctx.viewport = (0, 0, w, h)
        ctx.wireframe = False
        c = self.env.clear
        ctx.clear(c[0], c[1], c[2], 1.0, depth=1.0)
        lv = self.level
        if lv is None:
            return
        if self.markers_dirty:
            self._build_markers()
        cam = self.cam
        aspect = w / h
        view, proj = cam.view(), cam.proj(aspect)
        vp = proj @ view
        eye = cam.pos
        self.tri_drawn = 0
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA

        # sky: drawn around the camera, no depth writes, no fog
        if self.opt['sky'] and lv.sky is not None and self._file_on(lv.sky.path, 'nsbmd') and not cam.is_ortho:
            sky_m = np.eye(4); sky_m[:3, 3] = eye
            ctx.depth_mask = False
            fog_was = self.opt['fog']; self.opt['fog'] = False
            self._draw_model(lv.sky, sky_m, vp, eye, False)
            self.opt['fog'] = fog_was
            ctx.depth_mask = True
            ctx.clear(depth=1.0)

        if self.opt['wireframe']:
            ctx.wireframe = True
        ident = np.eye(4)
        # opaque pass: room models, then object models
        if self.show_type['nsbmd']:
            for p, md in lv.models.items():
                if self.show_files.get(p, True):
                    self._draw_model(md, ident, vp, eye, False)
        sel = self.sel_obj()
        draw_objs = []
        if self.show_type['lp'] and self.show_files.get(lv.lp_path, True):
            for o in lv.objects:
                if o.visible and o.model:
                    md = self._model_for(o)
                    if md is not None and md.batches:
                        draw_objs.append((o, md))
        for o, md in draw_objs:
            self._draw_model(md, self.obj_matrix(o), vp, eye, False, tint=(1, 0.85, 0.2) if o is sel else None)
        # translucent pass
        ctx.depth_mask = False
        if self.show_type['nsbmd']:
            for p, md in lv.models.items():
                if self.show_files.get(p, True):
                    self._draw_model(md, ident, vp, eye, True)
        for o, md in draw_objs:
            self._draw_model(md, self.obj_matrix(o), vp, eye, True, tint=(1, 0.85, 0.2) if o is sel else None)
        ctx.depth_mask = True
        ctx.wireframe = False
        ctx.disable(moderngl.CULL_FACE)

        # lines: collision (wireframe), room boxes, LP markers/paths
        if self.show_type['cvhm']:
            for p, (segs, cols) in lv.collision.items():
                if self.show_files.get(p, True) and len(segs):
                    vao, _vbo, n = self._lines(p, segs, cols)
                    self._draw_lines(vao, n, vp, alpha=0.85, eye=eye)
        if self.show_type['roo'] and lv.rooms and self.show_files.get(lv.rooms and self._roo_path(), True):
            pts = np.vstack([sd.box_lines(r['bmin'], r['bmax']) for r in lv.rooms])
            cols = np.tile((1.0, 0.8, 0.25), (len(pts), 1))
            self._draw_dyn(pts, cols, vp, eye, alpha=0.9)
        if self.opt['markers'] and self._marker_n and self.show_type['lp']:
            self._draw_lines(self._marker_vao[0], self._marker_n, vp, eye=eye)
        # grid + axes
        if self.opt['grid']:
            gp, gc = self._grid_lines()
            self._draw_dyn(gp, gc, vp, eye, alpha=0.5)
        if self.opt['axes']:
            L = max(cam.scene_size * 0.03, 1.0)
            self._draw_dyn([(0, 0, 0), (L, 0, 0), (0, 0, 0), (0, L, 0), (0, 0, 0), (0, 0, L)],
                           [AXIS_COL[0]] * 2 + [AXIS_COL[1]] * 2 + [AXIS_COL[2]] * 2, vp, eye, overlay=True)
        # selection box + tool handles
        if sel is not None:
            lo, hi = self._obj_bounds_world(sel)
            seg = sd.box_lines(lo, hi)
            self._draw_dyn(seg, np.tile((1.0, 0.9, 0.2), (len(seg), 1)), vp, eye, overlay=True)
            if self.tool in ('move', 'rotate'):
                hp, hc = self._handle_lines(sel)
                tp, tc = self._thick(hp, hc, sel)
                self._draw_dyn(tp, tc, vp, eye, overlay=True, mode=moderngl.TRIANGLES)
        ctx.disable(moderngl.BLEND)

        self._frames += 1
        t = time.time()
        if t - self._fps_t >= 0.5:
            self.fps = self._frames / (t - self._fps_t)
            self._frames, self._fps_t = 0, t

    def _roo_path(self):
        for f in self.level.files:
            if f.kind == 'roo':
                return f.path
        return None

    def _grid_lines(self):
        lv = self.level
        size = max(self.cam.scene_size, 10.0)
        step = 10 ** math.floor(math.log10(size / 10.0))
        if size / step > 40:
            step *= 5
        half = int(size / step / 2) + 1
        cx = round(((lv.bmin[0] + lv.bmax[0]) / 2) / step) * step
        cz = round(((lv.bmin[2] + lv.bmax[2]) / 2) / step) * step
        y = float(lv.bmin[1])
        pts, cols = [], []
        for i in range(-half, half + 1):
            for a, b in (((cx + i * step, y, cz - half * step), (cx + i * step, y, cz + half * step)),
                         ((cx - half * step, y, cz + i * step), (cx + half * step, y, cz + i * step))):
                pts += [a, b]
                cols += [(0.28, 0.32, 0.36)] * 2
        return np.array(pts), np.array(cols)

    # ------------------------------------------------------------------ tool handles
    def _handle_len(self, o):
        d = float(np.linalg.norm(o.pos - self.cam.pos)) if not self.cam.is_ortho else self.cam.ortho_half * 2
        return max(d * 0.14, 0.5)

    def _handle_lines(self, o):
        p = o.pos
        L = self._handle_len(o)
        pts, cols = [], []
        axes = np.eye(3)
        if self.tool == 'rotate':
            m = sd.trs_matrix([0, 0, 0], o.euler, self.euler_order, self.euler_sign)[:3, :3]
            axes = m.T if False else np.eye(3)
        for i in range(3):
            a = axes[i]
            pts += [p, p + a * L]
            cols += [AXIS_COL[i]] * 2
            if self.tool == 'move':                      # little arrow heads
                side = np.cross(a, (0, 1, 0)) if i != 1 else np.cross(a, (1, 0, 0))
                side = normalize(side) * L * 0.07
                for s in (side, -side):
                    pts += [p + a * L, p + a * L * 0.88 + s]
                    cols += [AXIS_COL[i]] * 2
            else:                                        # rotate: a ring around the axis
                u = normalize(np.cross(a, (0, 1, 0) if i != 1 else (1, 0, 0)))
                v = np.cross(a, u)
                prev = None
                for k in range(25):
                    t = k / 24.0 * 2 * math.pi
                    q = p + (u * math.cos(t) + v * math.sin(t)) * L * 0.8
                    if prev is not None:
                        pts += [prev, q]; cols += [AXIS_COL[i]] * 2
                    prev = q
        return np.array(pts), np.array(cols)

    def _thick(self, pts, cols, o):
        """Turn line pairs into screen-facing quads (about 3 px wide at any distance) so the handles
        stay visible against busy scenes. Returns (triangle vertices, colours)."""
        pts = np.asarray(pts, np.float64).reshape(-1, 2, 3)
        cols = np.asarray(cols, np.float64).reshape(-1, 2, 3)
        a, b = pts[:, 0], pts[:, 1]
        d = b - a
        if self.cam.is_ortho:
            v = np.tile(self.cam.forward(), (len(a), 1))
            scale = np.full((len(a), 1), self.cam.ortho_half * 2.0)
        else:
            v = (a + b) / 2.0 - self.cam.pos
            scale = np.linalg.norm(v, axis=1, keepdims=True)
        side = np.cross(d, v)
        side = side / np.maximum(np.linalg.norm(side, axis=1, keepdims=True), 1e-9) * scale * 0.004
        tri = np.stack([a - side, a + side, b + side, a - side, b + side, b - side], axis=1).reshape(-1, 3)
        col = np.stack([cols[:, 0], cols[:, 0], cols[:, 1], cols[:, 0], cols[:, 1], cols[:, 1]], axis=1).reshape(-1, 3)
        return tri, col

    def _pick_handle(self, o, mx, my):
        w, h = self.width(), self.height()
        p = o.pos
        L = self._handle_len(o)
        best, bi = 10.0, -1
        m = np.array([mx, my])
        for i in range(3):
            a = np.eye(3)[i]
            if self.tool == 'move':
                p0 = np.array(self.cam.project(p, w, h)[:2])
                p1 = np.array(self.cam.project(p + a * L, w, h)[:2])
                d = _seg_dist(m, p0, p1)
            else:
                u = normalize(np.cross(a, (0, 1, 0) if i != 1 else (1, 0, 0)))
                v = np.cross(a, u)
                d = 1e9
                prev = None
                for k in range(25):
                    t = k / 24.0 * 2 * math.pi
                    q = np.array(self.cam.project(p + (u * math.cos(t) + v * math.sin(t)) * L * 0.8, w, h)[:2])
                    if prev is not None:
                        d = min(d, _seg_dist(m, prev, q))
                    prev = q
            if d < best:
                best, bi = d, i
        return bi

    # ------------------------------------------------------------------ picking
    def pick_all(self, mx, my):
        """Every placement under the cursor: solid ones nearest-first, then trigger volumes."""
        lv = self.level
        if lv is None or not self.show_type['lp'] or not self.show_files.get(lv.lp_path, True):
            return []
        o_, d_ = self.cam.ray(mx, my, self.width(), self.height())
        solid, trig = [], []
        for o in lv.objects:
            if not o.visible:
                continue
            tb = o.trigger_box()
            if tb is not None:
                t = ray_box(o_, d_, tb[0], tb[1])
                if t is not None:
                    trig.append((t, o.idx))
                continue
            lo, hi = self._obj_local_bounds(o)
            try:
                inv = np.linalg.inv(self.obj_matrix(o))
            except np.linalg.LinAlgError:
                continue
            t = ray_box(inv[:3, :3] @ o_ + inv[:3, 3], inv[:3, :3] @ d_, lo, hi)
            if t is not None:
                solid.append((t, o.idx))
        return [i for _t, i in sorted(solid)] + [i for _t, i in sorted(trig)]

    def pick_object(self, mx, my):
        hits = self.pick_all(mx, my)
        return hits[0] if hits else -1

    def click_select(self, mx, my):
        """Click selects the nearest placement; clicking the same spot again cycles through
        everything stacked under the cursor."""
        hits = self.pick_all(mx, my)
        if not hits:
            self._cycle = None
            self.select(-1)
            return
        last = self._cycle
        if (last and last['hits'] == hits and abs(mx - last['x']) <= 6 and abs(my - last['y']) <= 6
                and self.selected in hits):
            i = (hits.index(self.selected) + 1) % len(hits)
        else:
            i = 0
        self._cycle = dict(hits=hits, x=mx, y=my)
        self.select(hits[i])
        if len(hits) > 1:
            self.status.emit('%d placements under the cursor (%d/%d) - click again to cycle' % (len(hits), i + 1, len(hits)))

    # ------------------------------------------------------------------ input
    def mousePressEvent(self, e):
        self.setFocus()
        self._press_pos = e.position()
        self._moved = False
        self._drag = None
        mods = e.modifiers()
        if e.button() == Qt.LeftButton:
            o = self.sel_obj()
            if o is not None and self.tool in ('move', 'rotate') and not (mods & Qt.AltModifier):
                ax = self._pick_handle(o, e.position().x(), e.position().y())
                if ax >= 0:
                    self.editBegan.emit(o.idx)
                    ray = self.cam.ray(e.position().x(), e.position().y(), self.width(), self.height())
                    axis = np.eye(3)[ax]
                    self._drag = dict(kind=self.tool, axis=ax, pos0=o.pos.copy(), euler0=list(o.euler),
                                      t0=closest_on_axis(ray[0], ray[1], o.pos, axis), x0=e.position().x())
                    return
            self._drag = dict(kind='click')
        elif e.button() == Qt.RightButton:
            self._drag = dict(kind='orbit' if (mods & Qt.AltModifier) else 'look')
        elif e.button() == Qt.MiddleButton:
            self._drag = dict(kind='pan')
        self._last = e.position()

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._drag is None:
            o = self.sel_obj()
            if o is not None and self.tool in ('move', 'rotate'):
                a = self._pick_handle(o, pos.x(), pos.y())
                self.setCursor(Qt.PointingHandCursor if a >= 0 else Qt.ArrowCursor)
            return
        if self._press_pos is not None and (pos - self._press_pos).manhattanLength() > 4:
            self._moved = True
        k = self._drag['kind']
        dx, dy = pos.x() - self._last.x(), pos.y() - self._last.y()
        if k == 'look':
            self.cam.look(dx, dy)
        elif k == 'orbit':
            self.cam.orbit(dx, dy)
        elif k == 'pan':
            self.cam.pan(dx, dy, self.height(), DEFAULT_PAN_SPEED if (e.modifiers() & Qt.ShiftModifier) else None)
        elif k == 'move':
            o = self.sel_obj()
            ray = self.cam.ray(pos.x(), pos.y(), self.width(), self.height())
            axis = np.eye(3)[self._drag['axis']]
            t = closest_on_axis(ray[0], ray[1], self._drag['pos0'], axis)
            delta = t - self._drag['t0']
            if e.modifiers() & Qt.ControlModifier:
                delta = round(delta)
            o.set_pos(self._drag['pos0'] + axis * delta)
            self.object_changed(o.idx)
            self.objectEdited.emit(o.idx)
        elif k == 'rotate':
            o = self.sel_obj()
            ang = (pos.x() - self._drag['x0']) * (4096.0 / 600.0)
            if e.modifiers() & Qt.ControlModifier:
                ang = round(ang / 256.0) * 256.0          # 22.5 degree steps
            eu = list(self._drag['euler0'])
            eu[self._drag['axis']] += ang
            o.set_euler(eu)
            self.object_changed(o.idx)
            self.objectEdited.emit(o.idx)
        self._last = pos
        self.cameraChanged.emit()
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._drag and self._drag['kind'] == 'click' and not self._moved:
            self.click_select(e.position().x(), e.position().y())
        self._drag = None

    def wheelEvent(self, e):
        notches = e.angleDelta().y() / 120.0
        w, h = max(self.width(), 1), max(self.height(), 1)
        p = e.position()
        ndc = (2.0 * p.x() / w - 1.0, 1.0 - 2.0 * p.y() / h)
        self.cam.dolly(notches, bool(e.modifiers() & Qt.ShiftModifier), ndc, w / h)
        self.cameraChanged.emit()
        self.update()

    def keyPressEvent(self, e):
        k, mods = e.key(), e.modifiers()
        pad = bool(mods & Qt.KeypadModifier)
        ctrl = bool(mods & Qt.ControlModifier)
        if k == Qt.Key_F:
            self.focus_selected()
        elif k == Qt.Key_Home:
            self.frame_all()
        elif pad and k == Qt.Key_5:
            self.toggle_ortho()
        elif pad and k in (Qt.Key_1, Qt.Key_3, Qt.Key_7):
            name = {Qt.Key_1: ('Front', 'Back'), Qt.Key_3: ('Right', 'Left'), Qt.Key_7: ('Top', 'Bottom')}[k][1 if ctrl else 0]
            self.snap_view(name)
        elif pad and k in (Qt.Key_2, Qt.Key_8, Qt.Key_4, Qt.Key_6):
            dy = {Qt.Key_4: -15, Qt.Key_6: 15}.get(k, 0)
            dp = {Qt.Key_8: 15, Qt.Key_2: -15}.get(k, 0)
            self.cam.step_orbit(dy, dp)
            self.cameraChanged.emit(); self.update()
        elif pad and k == Qt.Key_Period:
            self.focus_selected()
        elif k == Qt.Key_Escape:
            self.select(-1)
        else:
            super().keyPressEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()

    # ------------------------------------------------------------------ view commands
    def set_ortho(self, on):
        on = bool(on)
        if on == self.cam.is_ortho:
            return
        self.cam.is_ortho = on
        if on:
            self.cam.ortho_half = max(self.cam.pivot_dist * math.tan(math.radians(self.cam.fov) / 2), 0.1)
        self.orthoChanged.emit(on)
        self.cameraChanged.emit()
        self.update()

    def toggle_ortho(self):
        self.set_ortho(not self.cam.is_ortho)

    def snap_view(self, name):
        """Cube-net click / numpad: look down an axis in a true orthographic projection."""
        self.cam.snap(name)
        self.set_ortho(True)
        self.cameraChanged.emit()
        self.update()


def _seg_dist(p, a, b):
    ab = b - a
    t = float(np.dot(p - a, ab)) / max(float(np.dot(ab, ab)), 1e-9)
    t = max(0.0, min(1.0, t))
    return float(np.linalg.norm(p - (a + ab * t)))

#!/usr/bin/env python3
"""
GoldenEye: Rogue Agent (DS) - Pandora editor GUI
Run:  python pandora_editor.py
Needs: Python 3.8+, Pillow and numpy (pip install pillow numpy). Tkinter ships with Python on
Windows/macOS (Linux: sudo apt install python3-tk).
Keep this file in the same folder as nitro.py, scene.py, nitro_write.py, nds_unpack.py,
nds_repack.py, export_models.py, collision.py, objects_lp.py and extra_files.py.
"""
import os, sys, math, threading, queue, traceback, subprocess, platform

LOGFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pandora_editor_error.log')


def _fatal(msg):
    """Show a startup problem in the console AND a dialog, and keep the window open."""
    print('\n' + msg + '\n')
    try:
        with open(LOGFILE, 'a') as f:
            f.write(msg + '\n')
    except Exception:
        pass
    try:
        import tkinter as _tk
        from tkinter import messagebox as _mb
        r = _tk.Tk(); r.withdraw()
        _mb.showerror('Pandora editor', msg)
    except Exception:
        pass
    try:
        input('Press Enter to close...')
    except Exception:
        pass
    sys.exit(1)


if sys.version_info < (3, 8):
    _fatal('This tool needs Python 3.8 or newer (you have %s).' % sys.version.split()[0])

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from PIL import Image
except ImportError:
    _fatal('The "pillow" package is missing.\nOpen a command prompt and run:\n    pip install pillow numpy')

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, colorchooser
    import tkinter.font as tkfont
    from PIL import ImageTk
except ImportError:          # headless use (tests); main() explains the problem
    tk = None

try:
    from nitro import *          # noqa
    import nds_unpack, nds_repack, export_models, collision
    import objects_lp as lpmod
    import extra_files
except Exception as _e:
    _fatal('Could not load the tool files (%s: %s).\nKeep ALL the .py files from the zip in the same folder.'
           % (type(_e).__name__, _e))

# the importers need numpy; everything else works without it
try:
    import nitro_write
    NW_ERROR = None
except Exception as _e:
    nitro_write = None
    NW_ERROR = '%s: %s' % (type(_e).__name__, _e)

FMT_NAMES = {1: 'A3I5', 2: '4-colour', 3: '16-colour', 4: '256-colour',
             5: '4x4 compressed', 6: 'A5I3', 7: 'direct colour'}


# ====================================================== helpers (no GUI needed)
def find_shared_tex(path, with_path=False):
    d = os.path.dirname(path)
    for f in sorted(os.listdir(d)):
        if f.lower().endswith('.nsbtx'):
            try:
                tex = NSBTX(open(os.path.join(d, f), 'rb').read()).tex
                return (tex, os.path.join(d, f)) if with_path else tex
            except Exception:
                pass
    return (None, None) if with_path else None


def read_text(path):
    raw = open(path, 'rb').read()
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        try:
            return raw.decode('utf-16').replace('\r\n', '\n')
        except UnicodeDecodeError:
            pass
    try:
        return raw.decode('utf-8-sig').replace('\r\n', '\n')
    except UnicodeDecodeError:
        return raw.decode('latin-1').replace('\r\n', '\n')


def read_text_doc(path):
    """Read a text file. Returns (text with \\n line endings, info for writing it back)."""
    raw = open(path, 'rb').read()
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        enc = 'utf-16-le' if raw[:2] == b'\xff\xfe' else 'utf-16-be'
        bom, body = raw[:2], raw[2:]
    elif raw.startswith(b'\xef\xbb\xbf'):
        enc, bom, body = 'utf-8', raw[:3], raw[3:]
    else:
        bom, body = b'', raw
        try:
            body.decode('utf-8')
            enc = 'utf-8'
        except UnicodeDecodeError:
            enc = 'latin-1'
    text = body.decode(enc, errors='replace')
    nl = '\r\n' if '\r\n' in text else ('\r' if '\r' in text else '\n')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return text, {'encoding': enc, 'bom': bom, 'newline': nl}


def encode_text_doc(text, info):
    body = text.replace('\n', info['newline']).encode(info['encoding'])
    return info['bom'] + body


def backup_file(work_root, path):
    """Keep one pristine copy of a file before the first edit."""
    import shutil
    try:
        rel = os.path.relpath(path, work_root)
        if rel.startswith('..'):
            raise ValueError
        dest = os.path.join(work_root, '_backup', rel)
    except ValueError:
        dest = path + '.orig'
    if not os.path.exists(dest):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(path, dest)
    return dest


def backup_path(work_root, path):
    try:
        rel = os.path.relpath(path, work_root)
        if not rel.startswith('..'):
            return os.path.join(work_root, '_backup', rel)
    except ValueError:
        pass
    return path + '.orig'


class Loaded:
    """A parsed .nsbmd / .nsbtx ready for previewing."""
    def __init__(self, path):
        self.path = path
        self.models, self.tex, self.tex_source = [], None, ''
        self.tex_path = path
        self.tex_pal = {}                      # texture name -> palette name used by a material
        data = open(path, 'rb').read()
        low = path.lower()
        if low.endswith('.nsbmd'):
            n = NSBMD(data)
            self.models, self.tex = n.models, n.tex
            if self.tex:
                self.tex_source = 'embedded in this file'
            else:
                self.tex, self.tex_path = find_shared_tex(path, True)
                if self.tex:
                    self.tex_source = 'shared .nsbtx from the same folder'
            for m in self.models:
                for mt in m.materials:
                    if mt['tex'] and mt['pal']:
                        self.tex_pal.setdefault(mt['tex'], mt['pal'])
        elif low.endswith('.nsbtx'):
            self.tex = NSBTX(data).tex
            self.tex_source = 'this file'
        else:
            raise NitroError('no preview for this file type')
        self._tris = None

    def triangles(self):
        if self._tris is None:
            tris = []
            for mdl in self.models:
                for si, mi, W in build_world_matrices(mdl):
                    for t in run_display_list(mdl.shapes[si]['dl'], mdl.pos_scale):
                        tris.append(tuple(mat_apply(W, v[:3]) for v in t))
            self._tris = tris
        return self._tris


def bounds(tris):
    if not tris:
        return (0, 0, 0), 1.0
    xs = [v[0] for t in tris for v in t]
    ys = [v[1] for t in tris for v in t]
    zs = [v[2] for t in tris for v in t]
    c = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2)
    r = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) / 2 or 1.0
    return c, r


def _camera(center, radius, yaw, pitch, zoom, w, h):
    """Shared 3D -> 2D transform for the preview canvas.  Returns (tf, screen)."""
    cy, sy_ = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    scale = zoom * min(w, h) / (2.2 * radius)
    cx0, cy0, cz0 = center

    def tf(v):
        x, y, z = v[0] - cx0, v[1] - cy0, v[2] - cz0
        x2 = x * cy + z * sy_
        z2 = -x * sy_ + z * cy
        y2 = y * cp - z2 * sp
        z3 = y * sp + z2 * cp
        return x2, y2, z3

    def screen(p):
        return w / 2 + p[0] * scale, h / 2 - p[1] * scale

    return tf, screen


def project(tris, center, radius, yaw, pitch, zoom, w, h, step=1):
    """Return [(depth, [x1,y1,x2,y2,x3,y3], shade 0..1)] sorted far -> near."""
    tf, screen = _camera(center, radius, yaw, pitch, zoom, w, h)

    out = []
    for i in range(0, len(tris), step):
        a, b, c = (tf(v) for v in tris[i])
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        ln = math.sqrt(nx * nx + ny * ny + nz * nz)
        shade = 0.3 + 0.7 * (abs(nz / ln) if ln else 0.5)
        pts = []
        for p in (a, b, c):
            pts += list(screen(p))
        out.append(((a[2] + b[2] + c[2]) / 3, pts, shade))
    out.sort(key=lambda t: t[0])
    return out


def box_edges(box):
    """The 12 edges of an axis-aligned box stored as [x0, y0, z0, x1, y1, z1]."""
    x0, y0, z0, x1, y1, z1 = box
    c = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    return [(c[a], c[b]) for a, b in ((0, 1), (1, 2), (2, 3), (3, 0),
                                      (4, 5), (5, 6), (6, 7), (7, 4),
                                      (0, 4), (1, 5), (2, 6), (3, 7))]


def boxes_bounds(boxes):
    """Center/radius of a list of [x0, y0, z0, x1, y1, z1] boxes, for the 3D view."""
    if not boxes:
        return (0, 0, 0), 1.0
    lo = [min(b[k] for b in boxes) for k in range(3)]
    hi = [max(b[k + 3] for b in boxes) for k in range(3)]
    center = tuple((lo[k] + hi[k]) / 2 for k in range(3))
    radius = max(hi[k] - lo[k] for k in range(3)) / 2 or 1.0
    return center, radius


def texture_image(tex, pal):
    im = Image.new('RGBA', (tex.w, tex.h))
    im.putdata(tex.decode(pal))
    return im


def on_checker(im, scale):
    """Scale up with nearest-neighbour and put it on a grey checkerboard (to show transparency)."""
    im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    bg = Image.new('RGBA', im.size, (200, 200, 200, 255))
    sq = max(4, scale * 4)
    px = bg.load()
    for y in range(im.height):
        for x in range(im.width):
            if ((x // sq) + (y // sq)) & 1:
                px[x, y] = (150, 150, 150, 255)
    bg.alpha_composite(im)
    return bg


def open_folder(path):
    try:
        if platform.system() == 'Windows':
            os.startfile(path)
        elif platform.system() == 'Darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])
    except Exception:
        pass


# ============================================================================ GUI
if tk is not None:
    class MapDialog(tk.Toplevel):
        """Ask which of the model's materials each OBJ material group should use."""
        MAXROWS = 12

        def __init__(self, parent, info):
            super().__init__(parent)
            self.title('Replace model with OBJ')
            self.result = None
            self.info = info
            self.names = []
            for tg in info['targets']:
                tx = ('  [%s %dx%d]' % (tg['tex'], tg['size'][0], tg['size'][1])) if tg['tex'] else '  [no texture]'
                self.names.append('%d: %s%s' % (tg['idx'], tg['name'], tx))
            ttk.Label(self, wraplength=560, justify='left', padding=8, text=(
                'The model has %d shape slot(s) and %d material(s). Choose which of the model\'s own materials '
                'each part of your OBJ should use (the texture comes from that material).' %
                (info['slots'], len(info['targets'])))).grid(row=0, column=0, columnspan=2, sticky='w')
            withtex = sum(1 for g in info['groups'] if g['tex'])
            if withtex:
                note = ('Your OBJ brings %d texture image(s) for %d part(s); they are copied into the model\'s '
                        'texture slots when the box below is ticked.' % (len({g['tex'] for g in info['groups'] if g['tex']}),
                                                                        withtex))
            else:
                note = ('Your OBJ does not name any texture image (its .mtl has no map_Kd, or the images are not '
                        'next to the OBJ), so the parts can only use the textures this model already has.')
            ttk.Label(self, wraplength=560, justify='left', padding=(8, 0, 8, 6), foreground=('black' if withtex else '#a00000'),
                      text=note).grid(row=1, column=0, columnspan=2, sticky='w')
            self.vars = {}
            groups = sorted(info['groups'], key=lambda g: -g['tris'])
            for r, g in enumerate(groups[:self.MAXROWS], start=2):
                lab = '%s  (%d triangles%s)' % (g['name'] or '(no material)', g['tris'],
                                                 ', ' + os.path.basename(g['tex']) if g['tex'] else ', no image')
                ttk.Label(self, text=lab).grid(row=r, column=0, sticky='w', padx=8, pady=2)
                v = tk.StringVar(value=self.names[info['auto'][g['name']]])
                self.vars[g['name']] = v
                ttk.Combobox(self, textvariable=v, values=self.names, state='readonly', width=46).grid(
                    row=r, column=1, padx=8, pady=2)
            r = min(len(groups), self.MAXROWS) + 2
            if len(groups) > self.MAXROWS:
                ttk.Label(self, foreground='gray', text='(+%d smaller parts use the automatic choice)' %
                          (len(groups) - self.MAXROWS)).grid(row=r, column=0, columnspan=2, sticky='w', padx=8)
                r += 1
            self.tex_var = tk.BooleanVar(value=True)
            ttk.Checkbutton(self, variable=self.tex_var, text="Also copy the OBJ's textures (from its .mtl) into those "
                            "materials' texture slots (resized, palette-limited)").grid(
                row=r, column=0, columnspan=2, sticky='w', padx=8, pady=(6, 0))
            self.bb_var = tk.BooleanVar(value=True)
            ttk.Checkbutton(self, variable=self.bb_var, text='Turn off billboard nodes that receive new geometry '
                            '(stops those parts spinning around with the camera)').grid(
                row=r + 1, column=0, columnspan=2, sticky='w', padx=8, pady=(0, 6))
            bf = ttk.Frame(self)
            bf.grid(row=r + 2, column=0, columnspan=2, sticky='e', padx=8, pady=8)
            ttk.Button(bf, text='Cancel', command=self.destroy).pack(side='right', padx=4)
            ttk.Button(bf, text='Replace model', command=self.ok).pack(side='right')
            self.transient(parent)
            self.grab_set()
            parent.wait_window(self)

        def ok(self):
            mapping = {g: int(v.get().split(':')[0]) for g, v in self.vars.items()}
            self.result = (mapping, bool(self.tex_var.get()), bool(self.bb_var.get()))
            self.destroy()


    class CollisionDialog(tk.Toplevel):
        """Choose how an OBJ is placed before it becomes collision geometry."""

        def __init__(self, parent, col, obj_path):
            super().__init__(parent)
            self.title('Generate collision from OBJ')
            self.result = None
            self.mode = tk.StringVar(value='as-is')
            self.off = [tk.StringVar(value='0') for _ in range(3)]

            ob = collision.read_obj(obj_path)
            xs = [v[0] for v in ob.verts]
            ys = [v[1] for v in ob.verts]
            zs = [v[2] for v in ob.verts]
            nb = ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))
            ntris = sum(len(f) for _n, f in ob.groups)

            def box(b):
                return 'X %.2f..%.2f   Y %.2f..%.2f   Z %.2f..%.2f' % (
                    b[0][0], b[1][0], b[0][1], b[1][1], b[0][2], b[1][2])

            ttk.Label(self, wraplength=560, justify='left', padding=8, text=(
                'Collision file: %s  (version %d, %d mesh(es), %d triangles)\n%s\n\n'
                'Your OBJ: %s  (%d triangles)\n%s\n\n'
                'Exported models and collision are both in the level\'s world coordinates, so the\n'
                'normal choice is to keep them. Choose how to place the OBJ:'
                % (os.path.basename(col.path or '?'), col.version, len(col.meshes),
                   sum(len(m.tris) for m in col.meshes),
                   box(col.bounds()) if col.bounds() else '(empty)',
                   os.path.basename(obj_path), ntris, box(nb)))).pack(anchor='w')

            ttk.Radiobutton(self, variable=self.mode, value='as-is', text=(
                'Keep the OBJ coordinates (recommended - this is what the model importer does)')).pack(
                anchor='w', padx=12)
            ttk.Radiobutton(self, variable=self.mode, value='auto', text=(
                'Move the OBJ onto the old collision (only for a model you built around the origin)')).pack(
                anchor='w', padx=12)

            of = ttk.Frame(self)
            of.pack(anchor='w', padx=12, pady=(8, 0))
            ttk.Label(of, text='Extra offset (X Y Z, in level units):').pack(side='left')
            for v in self.off:
                ttk.Entry(of, textvariable=v, width=7).pack(side='left', padx=2)

            bf = ttk.Frame(self)
            bf.pack(fill='x', padx=8, pady=8)
            ttk.Button(bf, text='Cancel', command=self.destroy).pack(side='right', padx=4)
            ttk.Button(bf, text='Generate', command=self.ok).pack(side='right')
            self.transient(parent)
            self.grab_set()
            parent.wait_window(self)

        def ok(self):
            try:
                off = tuple(float(v.get()) for v in self.off)
            except ValueError:
                return messagebox.showerror('Generate collision', 'The offset must be three numbers.', parent=self)
            self.result = (self.mode.get(), off)
            self.destroy()


    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title('Pandora editor (GOLDENEYE: ROGUE AGENT DS)')
            self.geometry('1280x820')
            self.minsize(1000, 640)
            self.q = queue.Queue()
            self.busy = False
            self.loaded = None
            self.collision = None
            self.roo = None
            self.roo_room = None
            self.roo_base_view = None
            self.room_vars = [tk.StringVar() for _ in range(6)]
            self.placement = None
            self.lp_sel = None           # selected LPRecord, or (path_obj, node_index), or LPPath
            self.lp_view_tris = []
            self.lp_vars = [tk.StringVar() for _ in range(6)]   # x,y,z, + angle/halfx/halfz/height
            self.lp_filter = tk.StringVar()
            self.lp_kind = tk.StringVar(value='Everything')
            self.lp_path_var = tk.StringVar()
            self.view_tris = []
            self.cur_path = None
            self.tree_paths = {}
            self.yaw, self.pitch, self.zoom = 0.6, 0.35, 1.0
            self.drag = None
            self.drag_r = None
            self.view = None
            self.base_view = None
            self.photo = None
            self.room_canvas = None
            self._last_tex = None
            self.text_path = None
            self.text_info = None
            self.text_dirty = False
            self.data = None
            self.data_raw = b''
            self.data_hex_off = 0
            self.data_palette = None
            self.data_char_raw = None
            self.data_swatches = []
            self.data_photo = None
            self._build()
            self.after(100, self._poll)

        # ------------------------------------------------------------- theme
        def _apply_mktb_theme(self):
            """Classic light-grey Windows chrome, white data grids and a black viewport."""
            style = ttk.Style(self)
            try:
                style.theme_use('clam')
            except tk.TclError:
                pass
            try:
                for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont',
                             'TkHeadingFont', 'TkCaptionFont', 'TkTooltipFont'):
                    tkfont.nametofont(name).configure(family='Segoe UI', size=9)
            except Exception:
                pass
            bg, border = '#f0f0f0', '#a0a0a0'
            style.configure('.', background=bg, foreground='#000000',
                            font=('Segoe UI', 9), borderwidth=1)
            style.configure('TFrame', background=bg)
            style.configure('TLabel', background=bg, foreground='#000000')
            style.configure('Hint.TLabel', background=bg, foreground='#5f5f5f')
            style.configure('Status.TLabel', background=bg, foreground='#333333',
                            font=('Segoe UI', 8))
            style.configure('DockHeader.TLabel', background='#e4e4e4', foreground='#000000',
                            font=('Segoe UI', 8, 'bold'), padding=(6, 2))
            style.configure('TButton', background='#e1e1e1', foreground='#000000',
                            padding=(8, 3), relief='raised', borderwidth=1,
                            focusthickness=0)
            style.map('TButton',
                      background=[('disabled', '#f3f3f3'), ('pressed', '#c9e3f7'),
                                  ('active', '#e8f4fc')],
                      relief=[('pressed', 'sunken'), ('active', 'raised')])
            style.configure('Tool.TButton', padding=(6, 2), font=('Segoe UI', 8))
            style.configure('Small.TButton', padding=(2, 0), font=('Segoe UI', 8, 'bold'))
            style.configure('TEntry', fieldbackground='#ffffff', foreground='#000000',
                            padding=2, bordercolor=border, lightcolor='#ffffff',
                            darkcolor='#a0a0a0')
            style.configure('TCombobox', fieldbackground='#ffffff', padding=2)
            style.configure('TCheckbutton', background=bg, foreground='#000000')
            style.map('TCheckbutton', background=[('active', bg)])
            style.configure('Sheet.TCheckbutton', background='#ffffff', foreground='#000000')
            style.map('Sheet.TCheckbutton', background=[('active', '#ffffff')])
            style.configure('TRadiobutton', background=bg)
            style.configure('TNotebook', background=bg, borderwidth=0,
                            tabmargins=(0, 2, 0, 0))
            style.configure('TNotebook.Tab', background='#e4e4e4', foreground='#000000',
                            padding=(14, 4), borderwidth=1, lightcolor='#ffffff',
                            darkcolor='#a0a0a0')
            style.map('TNotebook.Tab',
                      background=[('selected', '#ffffff'), ('active', '#fafafa')],
                      foreground=[('selected', '#000000')])
            style.configure('Document.TNotebook', background=bg, borderwidth=0,
                            tabmargins=(4, 3, 0, 0))
            style.configure('Document.TNotebook.Tab', padding=(18, 5),
                            font=('Segoe UI', 9))
            style.configure('Dock.TNotebook', background='#ffffff', borderwidth=1,
                            tabmargins=(0, 0, 0, 0))
            style.configure('Dock.TNotebook.Tab', padding=(10, 3), font=('Segoe UI', 8))
            style.configure('Section.TNotebook', background=bg, borderwidth=0,
                            tabmargins=(0, 2, 0, 0))
            style.configure('Section.TNotebook.Tab', padding=(11, 2), font=('Segoe UI', 8))
            style.configure('Treeview', background='#ffffff', fieldbackground='#ffffff',
                            foreground='#000000', rowheight=20, borderwidth=1,
                            relief='solid', font=('Segoe UI', 9))
            style.map('Treeview',
                      background=[('selected', '#3399ff')],
                      foreground=[('selected', '#ffffff')])
            style.configure('Treeview.Heading', background='#e6e6e6', foreground='#000000',
                            relief='raised', borderwidth=1, padding=(4, 3),
                            font=('Segoe UI', 8, 'bold'))
            style.map('Treeview.Heading', background=[('active', '#f2f2f2')])
            style.configure('TLabelframe', background=bg, bordercolor=border,
                            borderwidth=1, relief='groove')
            style.configure('TLabelframe.Label', background=bg, foreground='#000000',
                            font=('Segoe UI', 9, 'bold'))
            style.configure('TPanedwindow', background=bg)
            style.configure('TProgressbar', background='#4a9bd1', troughcolor='#dedede',
                            borderwidth=1)
            style.configure('TScrollbar', background='#e4e4e4', troughcolor='#f0f0f0',
                            bordercolor=border, arrowcolor='#333333')
            self.configure(background=bg)

        # -------------------------------------------------------------- menu
        def _build_menu(self):
            mb = tk.Menu(self, tearoff=0, font=('Segoe UI', 9))
            self.configure(menu=mb)

            fm = tk.Menu(mb, tearoff=0, font=('Segoe UI', 9))
            fm.add_command(label='Open ROM…', accelerator='Ctrl+O', command=self.pick_rom)
            fm.add_command(label='Open work folder…', command=self.pick_work)
            fm.add_separator()
            fm.add_command(label='Unpack ROM…', command=self.do_unpack)
            fm.add_command(label='Repack ROM…', command=self.do_repack)
            fm.add_separator()
            fm.add_command(label='Export all models + collision…', command=self.do_export_all)
            fm.add_command(label='Revert selected file to original', command=self.do_revert)
            fm.add_separator()
            fm.add_command(label='Exit', command=self.destroy)
            mb.add_cascade(label='File', menu=fm, underline=0)

            em = tk.Menu(mb, tearoff=0, font=('Segoe UI', 9))
            em.add_command(label='Save text', accelerator='Ctrl+S', command=self.save_text)
            em.add_command(label='Reload text', command=self.reload_text)
            em.add_separator()
            em.add_command(label='Focus file filter',
                           command=lambda: self.filter_entry.focus_set())
            em.add_command(label='Clear file filter',
                           command=lambda: self.filter_var.set(''))
            mb.add_cascade(label='Edit', menu=em, underline=0)

            tm = tk.Menu(mb, tearoff=0, font=('Segoe UI', 9))
            tm.add_command(label='Reset model view', command=self.reset_view)
            tm.add_command(label='Export current OBJ…', command=self.do_export_one)
            tm.add_command(label='Replace model with any OBJ…', command=self.do_replace_obj)
            tm.add_separator()
            tm.add_command(label='Export collision OBJ…', command=self.do_export_collision)
            tm.add_command(label='Generate collision from OBJ…',
                           command=self.do_generate_collision)
            tm.add_separator()
            tm.add_command(label='Show all rooms', command=self.show_all_rooms)
            tm.add_command(label='Zoom to selected room', command=self.zoom_room)
            mb.add_cascade(label='Tools', menu=tm, underline=0)

            hm = tk.Menu(mb, tearoff=0, font=('Segoe UI', 9))
            hm.add_command(label='Controls / shortcuts…', command=self._show_controls)
            hm.add_command(label='About Pandora editor…', command=self._show_about)
            mb.add_cascade(label='Help', menu=hm, underline=0)

            self.bind_all('<Control-o>', lambda e: self.pick_rom())
            self.bind_all('<Control-s>', lambda e: (self.save_text(), 'break')[1])

        def _show_controls(self):
            messagebox.showinfo('Controls', (
                'File list\n'
                '  Click a file to open it. Filter box narrows the list.\n\n'
                '3D viewport\n'
                '  Left-drag = rotate, right-drag = pan, wheel = zoom.\n'
                '  "Solid" toggles filled triangles; "Reset view" recentres.\n\n'
                'Placement tab\n'
                '  Click a row or a marker to select it; double-click to centre.\n'
                '  + / − / duplicate / focus buttons sit above the table.\n\n'
                'Data tab\n'
                '  .char/.plt/.ico show a preview; Import PNG replaces the picture.\n'
                '  .crt/.nsbca/.nslbc/.tex show info + a hex view; the patch box\n'
                '  writes bytes at a hexadecimal offset. .lvl opens in the Text tab.\n\n'
                'Files\n'
                '  Edits are written in place and a copy is kept in _backup.\n'
                '  Repack the ROM after editing to test in-game.'))

        def _show_about(self):
            messagebox.showinfo('About Pandora editor', (
                'Pandora editor for GoldenEye: Rogue Agent (Nintendo DS).\n\n'
                'Model, texture, text, room, placement and extra-file editing, with a '
                'classic Windows layout inspired by Mario Kart Toolbox.\n\n'
                'Keep this script next to nitro.py, scene.py, collision.py, '
                'objects_lp.py, extra_files.py and the other tool modules.'))

        def _dock_sheet(self, parent):
            """White property sheet with dark MKTB-style group headers."""
            sheet = tk.Frame(parent, background='#ffffff', highlightthickness=1,
                             highlightbackground='#a0a0a0')
            sheet.pack(fill='both', expand=True)

            def group(title):
                tk.Label(sheet, text='  ' + title, anchor='w', background='#0a246a',
                         foreground='#ffffff', font=('Segoe UI', 9, 'bold'),
                         padx=2, pady=2).pack(fill='x', pady=(0, 1))

            def row(name, build=None, value=None):
                r = tk.Frame(sheet, background='#ffffff')
                r.pack(fill='x')
                tk.Label(r, text=name, background='#ffffff', foreground='#000000',
                         anchor='w', font=('Segoe UI', 9), padx=4).pack(side='left')
                if build is not None:
                    build(r)
                elif value is not None:
                    tk.Label(r, textvariable=value, background='#ffffff',
                             foreground='#000000', anchor='e', font=('Segoe UI', 9),
                             padx=4).pack(side='right')
                return r

            return sheet, group, row

        def _build_model_dock(self, dock):
            props = ttk.Frame(dock, padding=(2, 2))
            dock.add(props, text='Properties')
            _sheet, group, row = self._dock_sheet(props)
            group('Object')
            row('File', value=self.dock_file)
            row('Type', value=self.dock_kind)
            row('Solid', build=lambda r: ttk.Checkbutton(
                r, variable=self.solid, style='Sheet.TCheckbutton',
                command=self.redraw).pack(side='right', padx=(2, 4)))
            group('Transformation')
            row('Yaw', value=self.dock_yaw)
            row('Pitch', value=self.dock_pitch)
            row('Zoom', value=self.dock_zoom)
            ttk.Button(_sheet, text='Reset view', style='Tool.TButton',
                       command=self.reset_view).pack(fill='x', padx=4, pady=(4, 3))
            group('Actions')
            ttk.Button(_sheet, text='Export OBJ…', style='Tool.TButton',
                       command=self.do_export_one).pack(fill='x', padx=4, pady=(2, 2))
            ttk.Button(_sheet, text='Replace with any OBJ…', style='Tool.TButton',
                       command=self.do_replace_obj).pack(fill='x', padx=4, pady=(0, 4))

            col = ttk.Frame(dock, padding=(4, 4))
            dock.add(col, text='Collision')
            _cs, cgroup, crow = self._dock_sheet(col)
            cgroup('Collision')
            crow('File', value=self.dock_collision)
            crow('Triangles', value=self.dock_collision_tris)
            ttk.Button(col, text='Export collision OBJ…', style='Tool.TButton',
                       command=self.do_export_collision).pack(fill='x', pady=(4, 2))
            ttk.Button(col, text='Generate from OBJ…', style='Tool.TButton',
                       command=self.do_generate_collision).pack(fill='x', pady=(0, 2))
            ttk.Label(col, justify='left', style='Hint.TLabel', text=(
                'Collision geometry lives in level space.\n'
                'Generate builds a .CVHM from a collision OBJ.')).pack(anchor='w', pady=(4, 0))

            filep = ttk.Frame(dock, padding=(4, 4))
            dock.add(filep, text='File')
            _fs, fgroup, _frow = self._dock_sheet(filep)
            fgroup('Current file')
            tk.Label(_fs, textvariable=self.info, background='#ffffff',
                     foreground='#000000', anchor='w', justify='left', wraplength=215,
                     font=('Segoe UI', 9), padx=4, pady=4).pack(fill='x')
            fgroup('Original file')
            self.btn_revert = ttk.Button(filep, text='Revert file to original',
                                         style='Tool.TButton', command=self.do_revert,
                                         state='disabled')
            self.btn_revert.pack(fill='x', pady=(4, 2))
            ttk.Label(filep, justify='left', style='Hint.TLabel', text=(
                'Reverting restores the copy kept in the _backup folder.')).pack(
                anchor='w', pady=(2, 0))

        # ------------------------------------------------------------ layout
        def _build(self):
            self._apply_mktb_theme()
            self._build_menu()
            self.info = tk.StringVar(
                value='Choose a ROM and click Unpack, or pick an already-unpacked folder.')
            self.dock_file = tk.StringVar(value='no file selected')
            self.dock_kind = tk.StringVar(value='—')
            self.dock_yaw = tk.StringVar(value='0.60')
            self.dock_pitch = tk.StringVar(value='0.35')
            self.dock_zoom = tk.StringVar(value='1.00')
            self.dock_collision = tk.StringVar(value='no collision loaded')
            self.dock_collision_tris = tk.StringVar(value='0')

            # ---- top tool strip: ROM / work folder
            top = ttk.Frame(self, padding=(6, 4, 6, 3))
            top.pack(fill='x')
            self.rom_var, self.work_var = tk.StringVar(), tk.StringVar()
            ttk.Label(top, text='ROM (.nds):').grid(row=0, column=0, sticky='e', padx=(0, 4))
            ttk.Entry(top, textvariable=self.rom_var).grid(row=0, column=1, sticky='ew',
                                                          padx=(0, 4))
            ttk.Button(top, text='Browse…', style='Tool.TButton',
                       command=self.pick_rom).grid(row=0, column=2, padx=(0, 3))
            ttk.Button(top, text='Unpack ROM', style='Tool.TButton',
                       command=self.do_unpack).grid(row=0, column=3)
            ttk.Label(top, text='Work folder:').grid(row=1, column=0, sticky='e',
                                                     padx=(0, 4), pady=(2, 0))
            ttk.Entry(top, textvariable=self.work_var).grid(row=1, column=1, sticky='ew',
                                                           padx=(0, 4), pady=(2, 0))
            ttk.Button(top, text='Browse…', style='Tool.TButton',
                       command=self.pick_work).grid(row=1, column=2, padx=(0, 3), pady=(2, 0))
            ttk.Button(top, text='Repack ROM…', style='Tool.TButton',
                       command=self.do_repack).grid(row=1, column=3, pady=(2, 0))
            top.columnconfigure(1, weight=1)
            ttk.Separator(self, orient='horizontal').pack(fill='x')

            # ---- bottom: status line + log dock (packed before the body)
            status = ttk.Frame(self, padding=(6, 2))
            status.pack(side='bottom', fill='x')
            ttk.Label(status, textvariable=self.info, style='Status.TLabel',
                      anchor='w').pack(side='left', fill='x', expand=True)

            lf = ttk.Frame(self)
            lf.pack(side='bottom', fill='x')
            self.progress = ttk.Progressbar(lf, mode='indeterminate')
            self.progress.pack(fill='x')
            logbar = ttk.Frame(lf)
            logbar.pack(fill='x')
            ttk.Label(logbar, text='Log', style='DockHeader.TLabel').pack(
                side='left', fill='x', expand=True)
            self.log_text = tk.Text(lf, height=4, state='disabled', wrap='word',
                                    background='#ffffff', foreground='#000000',
                                    relief='solid', borderwidth=1,
                                    font=('Consolas', 9))
            self.log_text.pack(fill='x')

            body = ttk.PanedWindow(self, orient='horizontal')
            body.pack(fill='both', expand=True, padx=6, pady=(4, 4))

            # ---- left dock: file browser
            left = ttk.Frame(body, width=250)
            body.add(left, weight=1)
            ttk.Label(left, text='Files', style='DockHeader.TLabel').pack(fill='x')
            fbar = ttk.Frame(left)
            fbar.pack(fill='x', pady=(3, 0))
            self.filter_var = tk.StringVar()
            self.filter_var.trace_add('write', lambda *a: self.fill_tree())
            self.filter_entry = ttk.Entry(fbar, textvariable=self.filter_var)
            self.filter_entry.pack(side='left', fill='x', expand=True)
            ttk.Button(fbar, text='×', width=2, style='Small.TButton',
                       command=lambda: self.filter_var.set('')).pack(side='left', padx=(2, 0))
            ttk.Label(left, text='filter files (e.g. .nsbmd, barrel, .txt)',
                      style='Hint.TLabel').pack(anchor='w', pady=(1, 3))
            tf = ttk.Frame(left)
            tf.pack(fill='both', expand=True)
            self.tree = ttk.Treeview(tf, show='tree', selectmode='browse')
            sb = ttk.Scrollbar(tf, command=self.tree.yview)
            self.tree.configure(yscrollcommand=sb.set)
            self.tree.pack(side='left', fill='both', expand=True)
            sb.pack(side='right', fill='y')
            self.tree.bind('<<TreeviewSelect>>', self.on_select)
            ttk.Button(left, text='Export ALL models + collision…', style='Tool.TButton',
                       command=self.do_export_all).pack(fill='x', pady=(4, 0))

            # ---- right: document tabs
            right = ttk.Frame(body)
            body.add(right, weight=4)
            self.nb = ttk.Notebook(right, style='Document.TNotebook')
            self.nb.pack(fill='both', expand=True)

            # ---- model tab (viewport + property dock + bottom section tabs)
            mt = ttk.Frame(self.nb)
            self.nb.add(mt, text='Model')
            mp = ttk.PanedWindow(mt, orient='horizontal')
            mp.pack(fill='both', expand=True)

            vpf = ttk.Frame(mp)
            mp.add(vpf, weight=4)
            vbar = ttk.Frame(vpf)
            vbar.pack(fill='x', pady=(2, 2))
            self.solid = tk.BooleanVar(value=True)
            ttk.Checkbutton(vbar, text='Solid', variable=self.solid,
                            command=self.redraw).pack(side='left')
            ttk.Button(vbar, text='Reset view', style='Tool.TButton',
                       command=self.reset_view).pack(side='left', padx=6)
            ttk.Label(vbar, text='drag = rotate   right-drag = pan   wheel = zoom',
                      style='Hint.TLabel').pack(side='right')

            self.canvas = tk.Canvas(vpf, background='#000000', highlightthickness=1,
                                    highlightbackground='#7a7a7a', borderwidth=0)
            self.canvas.pack(fill='both', expand=True)
            self.canvas.bind('<ButtonPress-1>', self.on_press)
            self.canvas.bind('<B1-Motion>', self.on_drag)
            self.canvas.bind('<ButtonRelease-1>', self.on_release)
            self.canvas.bind('<ButtonPress-3>', self.on_press_r)
            self.canvas.bind('<B3-Motion>', self.on_drag_r)
            self.canvas.bind('<ButtonRelease-3>', self.on_release_r)
            self.canvas.bind('<MouseWheel>', self.on_wheel)
            self.canvas.bind('<Button-4>', lambda e: self.zoom_by(1.1))
            self.canvas.bind('<Button-5>', lambda e: self.zoom_by(1 / 1.1))
            self.canvas.bind('<Configure>', lambda e: self.redraw())

            # MKTB-style bottom section strip under the viewport
            vd = ttk.Notebook(vpf, style='Section.TNotebook')
            vd.pack(fill='x', pady=(2, 0))
            colpage = ttk.Frame(vd, padding=(4, 3))
            vd.add(colpage, text='Collision')
            ttk.Button(colpage, text='Export collision OBJ…', style='Tool.TButton',
                       command=self.do_export_collision).pack(side='left')
            ttk.Button(colpage, text='Generate from OBJ…', style='Tool.TButton',
                       command=self.do_generate_collision).pack(side='left', padx=4)
            ttk.Label(colpage, text='.CVHM files - collision geometry lives in level space',
                      style='Hint.TLabel').pack(side='left', padx=8)
            campage = ttk.Frame(vd, padding=(4, 3))
            vd.add(campage, text='Camera')
            ttk.Button(campage, text='Reset view', style='Tool.TButton',
                       command=self.reset_view).pack(side='left')
            ttk.Label(campage, text='1 pixel = 0.01 rad, wheel zooms around the view centre',
                      style='Hint.TLabel').pack(side='left', padx=8)

            dock = ttk.Notebook(mp, style='Dock.TNotebook')
            mp.add(dock, weight=1)
            dock.configure(width=250)
            self.model_dock = dock
            self._build_model_dock(dock)

            # ---- texture tab
            tt = ttk.Frame(self.nb)
            self.nb.add(tt, text='Textures')
            tl = ttk.Frame(tt)
            tl.pack(side='left', fill='y')
            self.texlist = tk.Listbox(tl, width=34, exportselection=False,
                                      background='#ffffff', foreground='#000000',
                                      selectbackground='#3399ff', selectforeground='#ffffff',
                                      relief='solid', borderwidth=1, font=('Segoe UI', 9))
            self.texlist.pack(side='left', fill='y')
            tsb = ttk.Scrollbar(tl, command=self.texlist.yview)
            self.texlist.configure(yscrollcommand=tsb.set)
            tsb.pack(side='left', fill='y')
            self.texlist.bind('<<ListboxSelect>>', lambda e: self.show_texture())
            tr = ttk.Frame(tt)
            tr.pack(side='left', fill='both', expand=True, padx=6)
            pr = ttk.Frame(tr)
            pr.pack(fill='x')
            ttk.Label(pr, text='Palette:').pack(side='left')
            self.pal_var = tk.StringVar()
            self.pal_box = ttk.Combobox(pr, textvariable=self.pal_var, state='readonly', width=24)
            self.pal_box.pack(side='left', padx=4)
            self.pal_box.bind('<<ComboboxSelected>>', lambda e: self.show_texture())
            ttk.Button(pr, text='Import PNG…', command=self.do_import_png).pack(side='right')
            ttk.Button(pr, text='Save all PNGs…', command=self.save_all_textures).pack(side='right', padx=4)
            ttk.Button(pr, text='Save PNG…', command=self.save_texture).pack(side='right')
            self.tex_info = tk.StringVar()
            ttk.Label(tr, textvariable=self.tex_info, wraplength=520, justify='left').pack(anchor='w', pady=2)
            self.tex_canvas = tk.Canvas(tr, background='#303030', highlightthickness=1,
                                        highlightbackground='#7a7a7a')
            self.tex_canvas.pack(fill='both', expand=True)

            # ---- text tab
            xt = ttk.Frame(self.nb)
            self.nb.add(xt, text='Text')
            xtb = ttk.Frame(xt)
            xtb.pack(fill='x')
            self.text_label = tk.StringVar(value='')
            ttk.Label(xtb, textvariable=self.text_label).pack(side='left')
            self.btn_text_save = ttk.Button(xtb, text='Save text', command=self.save_text, state='disabled')
            self.btn_text_save.pack(side='right')
            ttk.Button(xtb, text='Reload', command=self.reload_text).pack(side='right', padx=4)
            txtf = ttk.Frame(xt)
            txtf.pack(fill='both', expand=True)
            self.text = tk.Text(txtf, wrap='word', font=('Consolas', 10), undo=True,
                                background='#ffffff', foreground='#000000',
                                insertbackground='#000000', relief='solid', borderwidth=1)
            xsb = ttk.Scrollbar(txtf, command=self.text.yview)
            self.text.configure(yscrollcommand=xsb.set)
            self.text.pack(side='left', fill='both', expand=True)
            xsb.pack(side='right', fill='y')
            self.text.bind('<<Modified>>', self.on_text_modified)
            self.text.bind('<Control-s>', lambda e: (self.save_text(), 'break')[1])

            # ---- rooms tab (.roo / Global.ROO)
            rt = ttk.Frame(self.nb)
            self.nb.add(rt, text='Rooms (.ROO)')
            rbar = ttk.Frame(rt)
            rbar.pack(fill='x')
            ttk.Button(rbar, text='Show all rooms', command=self.show_all_rooms).pack(side='left')
            ttk.Button(rbar, text='Zoom to room', command=self.zoom_room).pack(side='left', padx=4)
            self.rooms_hint = tk.StringVar(
                value='Open a level\'s Global.ROO in the list on the left to see and edit its room boxes.')
            ttk.Label(rt, textvariable=self.rooms_hint, style='Hint.TLabel', wraplength=780,
                      justify='left').pack(anchor='w', pady=(4, 4))
            # Pack the selected-box editor first (bottom) so it keeps its space
            # when the window is short; the 3D view and table shrink instead.
            self.room_entries = []
            ef = ttk.LabelFrame(rt, text=' Selected room box (level units, stored with 1/4096 precision) ',
                                padding=6)
            ef.pack(side='bottom', fill='x', pady=(6, 0))
            for k, ax in enumerate(('X', 'Y', 'Z')):
                ttk.Label(ef, text=ax).grid(row=0, column=k + 1)
            ttk.Label(ef, text='Min').grid(row=1, column=0, sticky='e', padx=(0, 6))
            ttk.Label(ef, text='Max').grid(row=2, column=0, sticky='e', padx=(0, 6))
            for i in range(6):
                ent = ttk.Entry(ef, textvariable=self.room_vars[i], width=13)
                ent.grid(row=1 + i // 3, column=1 + i % 3, padx=3, pady=2, sticky='w')
                self.room_entries.append(ent)
            ttk.Button(ef, text='Apply box and save', command=self.apply_room_box).grid(
                row=1, column=4, rowspan=2, padx=(10, 0))
            ttk.Label(ef, style='Hint.TLabel', text=(
                'The game only runs collision inside the room the player is in, so a box must contain the '
                'walkable area. "Apply" writes the file (a backup is kept); Repack the ROM to test it.'),
                wraplength=780, justify='left').grid(row=3, column=0, columnspan=5, sticky='w', pady=(4, 0))

            rf = ttk.PanedWindow(rt, orient='vertical')
            rf.pack(fill='both', expand=True)
            viewf = ttk.Frame(rf)
            rf.add(viewf, weight=3)
            self.room_canvas = tk.Canvas(viewf, background='#000000', highlightthickness=1,
                                         highlightbackground='#7a7a7a', height=210)
            self.room_canvas.pack(fill='both', expand=True)
            self.room_canvas.bind('<ButtonPress-1>', self.on_press)
            self.room_canvas.bind('<B1-Motion>', self.on_drag)
            self.room_canvas.bind('<ButtonRelease-1>', self.on_release)
            self.room_canvas.bind('<ButtonPress-3>', self.on_press_r)
            self.room_canvas.bind('<B3-Motion>', self.on_drag_r)
            self.room_canvas.bind('<ButtonRelease-3>', self.on_release_r)
            self.room_canvas.bind('<MouseWheel>', self.on_wheel)
            self.room_canvas.bind('<Button-4>', lambda e: self.zoom_by(1.1))
            self.room_canvas.bind('<Button-5>', lambda e: self.zoom_by(1 / 1.1))
            self.room_canvas.bind('<Configure>', lambda e: self.redraw())
            tablef = ttk.Frame(rf)
            rf.add(tablef, weight=2)
            cols = ('idx', 'name', 'flags', 'x0', 'y0', 'z0', 'x1', 'y1', 'z1', 'size')
            heads = ('#', 'room', 'flags', 'min X', 'min Y', 'min Z', 'max X', 'max Y', 'max Z', 'size (X x Y x Z)')
            widths = (34, 130, 52, 66, 66, 66, 66, 66, 66, 150)
            self.room_tree = ttk.Treeview(tablef, columns=cols, show='headings', selectmode='browse', height=6)
            for c, hd, wd in zip(cols, heads, widths):
                self.room_tree.heading(c, text=hd)
                self.room_tree.column(c, width=wd, stretch=False, anchor='w' if c == 'name' else 'center')
            rsb = ttk.Scrollbar(tablef, command=self.room_tree.yview)
            self.room_tree.configure(yscrollcommand=rsb.set)
            self.room_tree.pack(side='left', fill='both', expand=True)
            rsb.pack(side='right', fill='y')
            self.room_tree.bind('<<TreeviewSelect>>', self.on_room_select)
            self.room_tree.bind('<Double-1>', lambda e: self.room_entries[0].focus_set())

            # ---- placement tab (Objects.LP)
            pt = ttk.Frame(self.nb)
            self.nb.add(pt, text='Placement (.LP)')
            pbar = ttk.Frame(pt)
            pbar.pack(fill='x')
            ttk.Button(pbar, text='Show all', command=self.lp_show_all).pack(side='left')
            ttk.Button(pbar, text='Zoom to selection', command=self.lp_zoom_sel).pack(side='left', padx=4)
            ttk.Label(pbar, text='Show:').pack(side='left', padx=(10, 2))
            self.lp_kind_box = ttk.Combobox(pbar, textvariable=self.lp_kind, state='readonly', width=15,
                                            values=['Everything', 'Objects', 'Enemies (AI)', 'Trigger boxes'])
            self.lp_kind_box.pack(side='left')
            self.lp_kind.trace_add('write', lambda *a: self.fill_lp_place_table())
            ttk.Label(pbar, text='filter name:').pack(side='left', padx=(10, 2))
            self.lp_filter_entry = ttk.Entry(pbar, textvariable=self.lp_filter, width=22)
            self.lp_filter_entry.pack(side='left')
            self.lp_filter.trace_add('write', lambda *a: self.fill_lp_place_table())
            self.lp_hint = tk.StringVar(value=(
                'Open a level\'s Objects.LP on the left. It holds everything the level places: objects, '
                'weapons, waypoints, trigger boxes (flags 0/2), the patrol path graph (flags 5) and the '
                'enemy spawn points with their facing (flags 8). Positions are 12.12 fixed point in the '
                'same space as the collision and models.'))
            ttk.Label(pt, textvariable=self.lp_hint, style='Hint.TLabel', wraplength=780,
                      justify='left').pack(anchor='w', pady=(4, 4))
            # The section tabs (table + editor) are packed first, from the bottom,
            # so the table and edited values stay reachable when the window is
            # short; the 3D view above shrinks instead.
            inner = ttk.Notebook(pt, style='Section.TNotebook')
            inner.pack(side='bottom', fill='x', pady=(3, 0))
            self.lp_inner = inner
            self.lp_canvas = tk.Canvas(pt, background='#000000', highlightthickness=1,
                                       highlightbackground='#7a7a7a', height=220)
            self.lp_canvas.pack(fill='both', expand=True)
            self.lp_canvas.bind('<ButtonPress-1>', self.on_press)
            self.lp_canvas.bind('<B1-Motion>', self.on_drag)
            self.lp_canvas.bind('<ButtonRelease-1>', self.on_release)
            self.lp_canvas.bind('<ButtonPress-3>', self.on_press_r)
            self.lp_canvas.bind('<B3-Motion>', self.on_drag_r)
            self.lp_canvas.bind('<ButtonRelease-3>', self.on_release_r)
            self.lp_canvas.bind('<MouseWheel>', self.on_wheel)
            self.lp_canvas.bind('<Button-4>', lambda e: self.zoom_by(1.1))
            self.lp_canvas.bind('<Button-5>', lambda e: self.zoom_by(1 / 1.1))
            self.lp_canvas.bind('<Configure>', lambda e: self.redraw())

            # ---- placements page
            pp = ttk.Frame(inner)
            inner.add(pp, text='Placements')
            # Editor first (bottom) so it keeps its space on short windows.
            ppan = ttk.LabelFrame(pp, text=' Selected placement ', padding=6)
            ppan.pack(side='bottom', fill='x', pady=(6, 0))
            for k, ax in enumerate(('X', 'Y', 'Z')):
                ttk.Label(ppan, text=ax).grid(row=0, column=k + 1)
            ttk.Label(ppan, text='Position').grid(row=1, column=0, sticky='e', padx=(0, 6))
            for i in range(3):
                ent = ttk.Entry(ppan, textvariable=self.lp_vars[i], width=10)
                ent.grid(row=1, column=1 + i, padx=3, pady=2, sticky='w')
            ttk.Label(ppan, text='Facing').grid(row=2, column=0, sticky='e', padx=(0, 6))
            self.lp_angle_ent = ttk.Entry(ppan, textvariable=self.lp_vars[3], width=10)
            self.lp_angle_ent.grid(row=2, column=1, padx=3, pady=2, sticky='w')
            ttk.Label(ppan, text='brads').grid(row=2, column=2, columnspan=2,
                                               sticky='w', padx=3)
            ttk.Label(ppan, text='Box').grid(row=2, column=3, sticky='e')
            self.lp_box_vars = [tk.StringVar() for _ in range(3)]     # half X, half Z, height
            for i in range(3):
                ttk.Entry(ppan, textvariable=self.lp_box_vars[i], width=7).grid(
                    row=2, column=4 + i, padx=3, pady=2, sticky='w')
            ttk.Label(ppan, text='size').grid(row=2, column=7, sticky='w')
            ttk.Button(ppan, text='Apply and save', command=self.lp_apply_save).grid(
                row=1, column=8, rowspan=2, padx=(10, 0))
            ttk.Label(ppan, style='Hint.TLabel', text=(
                'Coordinates are the level\'s world space. "Apply and save" rewrites the file (a backup is '
                'kept); repack the ROM to test it. Facing uses brads (4096 = 360°); the box stores '
                'half X, half Z and height.'), wraplength=740, justify='left').grid(
                row=3, column=0, columnspan=10, sticky='w', pady=(4, 0))

            pf = ttk.PanedWindow(pp, orient='vertical')
            pf.pack(fill='both', expand=True)
            tablef = ttk.Frame(pf)
            pf.add(tablef, weight=3)
            ptool = ttk.Frame(tablef)
            ptool.pack(fill='x')
            ttk.Button(ptool, text='+', width=3, style='Small.TButton',
                       command=self.lp_add_placement).pack(side='left')
            ttk.Button(ptool, text='−', width=3, style='Small.TButton',
                       command=self.lp_delete_placement).pack(side='left', padx=2)
            ttk.Button(ptool, text='⧉', width=3, style='Small.TButton',
                       command=self.lp_duplicate_placement).pack(side='left')
            ttk.Button(ptool, text='◎', width=3, style='Small.TButton',
                       command=self.lp_focus_sel).pack(side='left', padx=2)
            ttk.Label(ptool, text='add / delete / duplicate / focus',
                      style='Hint.TLabel').pack(side='left', padx=6)
            ptreef = ttk.Frame(tablef)
            ptreef.pack(fill='both', expand=True)
            cols = ('idx', 'h1', 'name', 'template', 'x', 'y', 'z')
            heads = ('#', 'id', 'name (CodeStrings.CHR)', 'template', 'X', 'Y', 'Z')
            widths = (34, 44, 150, 200, 70, 70, 70)
            self.lp_place_tree = ttk.Treeview(ptreef, columns=cols, show='headings',
                                               selectmode='browse', height=6)
            for c, hd, wd in zip(cols, heads, widths):
                self.lp_place_tree.heading(c, text=hd)
                self.lp_place_tree.column(c, width=wd, stretch=(c == 'template'),
                                          anchor='w' if c in ('name', 'template') else 'center')
            tsb = ttk.Scrollbar(ptreef, command=self.lp_place_tree.yview)
            self.lp_place_tree.configure(yscrollcommand=tsb.set)
            self.lp_place_tree.pack(side='left', fill='both', expand=True)
            tsb.pack(side='right', fill='y')
            self.lp_place_tree.bind('<<TreeviewSelect>>', self.on_lp_place_select)
            self.lp_place_tree.bind('<Double-1>', lambda e: self.lp_focus_sel())

            # ---- paths page
            qp = ttk.Frame(inner)
            inner.add(qp, text='Paths')
            # Editor first (bottom) so it keeps its space on short windows.
            qpan = ttk.LabelFrame(qp, text=' Selected path node ', padding=6)
            qpan.pack(side='bottom', fill='x', pady=(6, 0))
            for k, ax in enumerate(('X', 'Y', 'Z')):
                ttk.Label(qpan, text=ax).grid(row=0, column=k + 1)
            self.lp_pvars = [tk.StringVar() for _ in range(3)]
            for i in range(3):
                ttk.Entry(qpan, textvariable=self.lp_pvars[i], width=11).grid(
                    row=1, column=1 + i, padx=3, pady=2, sticky='w')
            ttk.Button(qpan, text='Apply and save', command=self.lp_path_apply_save).grid(
                row=1, column=4, padx=(10, 0))
            ttk.Label(qpan, style='Hint.TLabel', text=(
                'Edges are stored in both directions and their lengths are rewritten when you apply. '
                'The AI uses these graphs to walk patrol routes.'), wraplength=740,
                justify='left').grid(
                row=2, column=0, columnspan=5, sticky='w', pady=(4, 0))

            qf = ttk.PanedWindow(qp, orient='vertical')
            qf.pack(fill='both', expand=True)
            qtablef = ttk.Frame(qf)
            qf.add(qtablef, weight=3)
            qtool = ttk.Frame(qtablef)
            qtool.pack(fill='x')
            ttk.Button(qtool, text='+', width=3, style='Small.TButton',
                       command=self.lp_path_add_node).pack(side='left')
            ttk.Button(qtool, text='−', width=3, style='Small.TButton',
                       command=self.lp_path_del_node).pack(side='left', padx=2)
            ttk.Button(qtool, text='+P', width=3, style='Small.TButton',
                       command=self.lp_path_add_path).pack(side='left')
            ttk.Button(qtool, text='−P', width=3, style='Small.TButton',
                       command=self.lp_path_del_path).pack(side='left', padx=2)
            ttk.Button(qtool, text='⇄', width=3, style='Small.TButton',
                       command=self.lp_path_add_edge).pack(side='left')
            ttk.Button(qtool, text='⇎', width=3, style='Small.TButton',
                       command=self.lp_path_del_edge).pack(side='left', padx=2)
            ttk.Label(qtool, text='node add/remove, path add/remove, link/unlink',
                      style='Hint.TLabel').pack(side='left', padx=6)
            qtreef = ttk.Frame(qtablef)
            qtreef.pack(fill='both', expand=True)
            qcols = ('node', 'x', 'y', 'z', 'deg')
            qheads = ('node', 'X', 'Y', 'Z', 'links')
            qwidths = (60, 90, 90, 90, 60)
            self.lp_path_tree = ttk.Treeview(qtreef, columns=qcols, show='tree headings',
                                             selectmode='browse', height=7)
            self.lp_path_tree.heading('#0', text='path')
            self.lp_path_tree.column('#0', width=170, stretch=True, anchor='w')
            for c, hd, wd in zip(qcols, qheads, qwidths):
                self.lp_path_tree.heading(c, text=hd)
                self.lp_path_tree.column(c, width=wd, stretch=False, anchor='center')
            qsb = ttk.Scrollbar(qtreef, command=self.lp_path_tree.yview)
            self.lp_path_tree.configure(yscrollcommand=qsb.set)
            self.lp_path_tree.pack(side='left', fill='both', expand=True)
            qsb.pack(side='right', fill='y')
            self.lp_path_tree.bind('<<TreeviewSelect>>', self.on_lp_path_select)
            self.lp_path_tree.bind('<Double-1>', lambda e: self.lp_focus_sel())

            # ---- data tab (.char .plt .ico .crt .nsbca .nslbc .tex)
            dt = ttk.Frame(self.nb)
            self.nb.add(dt, text='Data')
            dbar = ttk.Frame(dt)
            dbar.pack(fill='x')
            self.data_label = tk.StringVar(value=(
                'Open a .char, .plt, .ico, .crt, .nsbca, .nslbc or .tex file in the list on the left.'))
            ttk.Label(dbar, textvariable=self.data_label).pack(side='left')
            self.btn_data_reload = ttk.Button(dbar, text='Reload', command=self.reload_data)
            self.btn_data_reload.pack(side='right')
            self.btn_data_png = ttk.Button(dbar, text='Save PNG…', command=self.save_data_png,
                                           state='disabled')
            self.btn_data_png.pack(side='right', padx=4)
            self.btn_data_import = ttk.Button(dbar, text='Import PNG…', command=self.import_data_png,
                                              state='disabled')
            self.btn_data_import.pack(side='right')

            # Palette chips are shown only for .plt files.
            self.data_pal_bar = ttk.Frame(dt)
            palhdr = ttk.Frame(self.data_pal_bar)
            palhdr.pack(fill='x')
            ttk.Label(palhdr, text='Palette (click a colour to change it):').pack(side='left')
            self.btn_pal_apply = ttk.Button(palhdr, text='Apply palette and save',
                                            command=self.apply_data_palette)
            self.btn_pal_apply.pack(side='left', padx=8)
            chips = ttk.Frame(self.data_pal_bar)
            chips.pack(fill='x', pady=(2, 2))
            self.data_swatches = []
            for i in range(16):
                b = tk.Button(chips, text=str(i), width=2, relief='solid', borderwidth=1,
                              font=('Consolas', 8),
                              command=lambda i=i: self.edit_palette_color(i))
                b.pack(side='left', padx=1)
                self.data_swatches.append(b)
            self.data_pal_bar.pack_forget()

            pfb = ttk.LabelFrame(dt, text=' Byte patch (a backup is kept in _backup) ', padding=6)
            pfb.pack(side='bottom', fill='x', pady=(6, 0))
            ttk.Label(pfb, text='Offset (hex)').grid(row=0, column=0, sticky='e')
            self.data_patch_off = tk.StringVar(value='0')
            ttk.Entry(pfb, textvariable=self.data_patch_off, width=10).grid(
                row=0, column=1, padx=4)
            ttk.Label(pfb, text='Bytes (hex)').grid(row=0, column=2, sticky='e')
            self.data_patch_bytes = tk.StringVar()
            ttk.Entry(pfb, textvariable=self.data_patch_bytes, width=44).grid(
                row=0, column=3, padx=4, sticky='ew')
            ttk.Button(pfb, text='Apply patch', command=self.apply_data_patch).grid(
                row=0, column=4, padx=(8, 0))
            ttk.Label(pfb, style='Hint.TLabel', text=(
                'Example: offset 200, bytes 00 FF 7A. The bytes are written into the file; '
                'Reload re-reads it. The hex view below is read-only.'), wraplength=780,
                justify='left').grid(row=1, column=0, columnspan=5, sticky='w', pady=(3, 0))
            pfb.columnconfigure(3, weight=1)

            db = ttk.PanedWindow(dt, orient='horizontal')
            db.pack(fill='both', expand=True, pady=(4, 0))
            self.data_split = db
            dl = ttk.Frame(db)
            db.add(dl, weight=2)
            ttk.Label(dl, text='File information', style='DockHeader.TLabel').pack(fill='x')
            smf = ttk.Frame(dl)
            smf.pack(fill='both', expand=True)
            self.data_summary = tk.Text(smf, width=48, wrap='word', state='disabled',
                                        background='#ffffff', foreground='#000000',
                                        relief='solid', borderwidth=1, font=('Consolas', 9))
            ssb = ttk.Scrollbar(smf, command=self.data_summary.yview)
            self.data_summary.configure(yscrollcommand=ssb.set)
            self.data_summary.pack(side='left', fill='both', expand=True)
            ssb.pack(side='right', fill='y')
            dr = ttk.Frame(db)
            db.add(dr, weight=5)
            self.data_nb = ttk.Notebook(dr, style='Section.TNotebook')
            self.data_nb.pack(fill='both', expand=True)
            prev = ttk.Frame(self.data_nb, padding=2)
            self.data_nb.add(prev, text='Preview')
            self.data_note = tk.StringVar(value='')
            ttk.Label(prev, textvariable=self.data_note, style='Hint.TLabel').pack(anchor='w')
            self.data_preview = tk.Canvas(prev, background='#303030', highlightthickness=1,
                                          highlightbackground='#7a7a7a')
            self.data_preview.pack(fill='both', expand=True)
            self.data_preview.bind('<Configure>', lambda e: self._draw_data_preview())
            hx = ttk.Frame(self.data_nb, padding=2)
            self.data_nb.add(hx, text='Bytes (hex)')
            hbar = ttk.Frame(hx)
            hbar.pack(fill='x')
            ttk.Label(hbar, text='From offset (hex)').pack(side='left')
            self.data_hex_off_var = tk.StringVar(value='0')
            ttk.Entry(hbar, textvariable=self.data_hex_off_var, width=9).pack(side='left', padx=4)
            ttk.Button(hbar, text='Go', width=4, style='Small.TButton',
                       command=self.goto_data_hex).pack(side='left')
            ttk.Button(hbar, text='◀ 8 KB', style='Small.TButton',
                       command=lambda: self.page_data_hex(-1)).pack(side='left', padx=(8, 2))
            ttk.Button(hbar, text='8 KB ▶', style='Small.TButton',
                       command=lambda: self.page_data_hex(1)).pack(side='left')
            self.data_hex_range = tk.StringVar(value='')
            ttk.Label(hbar, textvariable=self.data_hex_range,
                      style='Hint.TLabel').pack(side='left', padx=8)
            self.data_hex = tk.Text(hx, wrap='none', state='disabled', font=('Consolas', 9),
                                    background='#ffffff', foreground='#000000',
                                    relief='solid', borderwidth=1)
            hsb = ttk.Scrollbar(hx, command=self.data_hex.yview)
            self.data_hex.configure(yscrollcommand=hsb.set)
            self.data_hex.pack(side='left', fill='both', expand=True)
            hsb.pack(side='right', fill='y')

        def report_callback_exception(self, exc, val, tb):
            msg = ''.join(traceback.format_exception(exc, val, tb))
            try:
                with open(LOGFILE, 'a') as f:
                    f.write(msg + '\n')
            except Exception:
                pass
            self.log('ERROR: %s' % val)
            self.log(msg)
            messagebox.showerror('Error', '%s\n\n(details in the log box and %s)' % (val, os.path.basename(LOGFILE)))

        # ------------------------------------------------------------- logging/jobs
        def log(self, msg):
            self.q.put(('log', str(msg)))

        def _poll(self):
            try:
                while True:
                    kind, val = self.q.get_nowait()
                    if kind == 'log':
                        self.log_text.configure(state='normal')
                        self.log_text.insert('end', val + '\n')
                        self.log_text.see('end')
                        self.log_text.configure(state='disabled')
                    elif kind == 'done':
                        self.busy = False
                        self.progress.stop()
                        if val:
                            val()
            except queue.Empty:
                pass
            self.after(100, self._poll)

        def run_job(self, fn, after=None):
            if self.busy:
                messagebox.showinfo('Busy', 'Another task is still running.')
                return
            self.busy = True
            self.progress.start(12)

            def work():
                try:
                    fn()
                except Exception as e:
                    self.log('ERROR: %s' % e)
                    if nitro_write is None or not isinstance(e, nitro_write.ImportError_):
                        self.log(traceback.format_exc())
                finally:
                    self.q.put(('done', after))
            threading.Thread(target=work, daemon=True).start()

        # ----------------------------------------------------------- ROM actions
        def pick_rom(self):
            p = filedialog.askopenfilename(title='Select GoldenEye: Rogue Agent .nds',
                                           filetypes=[('Nintendo DS ROM', '*.nds'), ('All files', '*.*')])
            if p:
                self.rom_var.set(p)
                if not self.work_var.get():
                    self.work_var.set(os.path.join(os.path.dirname(p), 'rogue_agent_extracted'))

        def pick_work(self):
            p = filedialog.askdirectory(title='Select work folder (unpacked files)')
            if p:
                self.work_var.set(p)
                self.fill_tree()

        def do_unpack(self):
            rom, out = self.rom_var.get().strip(), self.work_var.get().strip()
            if not os.path.isfile(rom):
                return messagebox.showerror('Unpack', 'Pick a valid .nds ROM first.')
            if not out:
                return messagebox.showerror('Unpack', 'Pick a work folder.')
            if os.path.isdir(out) and os.listdir(out):
                if not messagebox.askyesno('Unpack', 'The work folder is not empty. Files in it with the same names '
                                           'will be OVERWRITTEN (your edits would be lost). Continue?'):
                    return
            self.log('Unpacking %s …' % rom)
            self.run_job(lambda: nds_unpack.unpack(rom, out, self.log), after=self.fill_tree)

        def do_repack(self):
            rom, work = self.rom_var.get().strip(), self.work_var.get().strip()
            if not os.path.isfile(rom) or not os.path.isdir(work):
                return messagebox.showerror('Repack', 'Pick the original ROM and the work folder first.')
            out = filedialog.asksaveasfilename(title='Save modified ROM as', defaultextension='.nds',
                                               initialfile='GoldenEye_RogueAgent_modded.nds',
                                               filetypes=[('Nintendo DS ROM', '*.nds')])
            if not out:
                return
            if os.path.abspath(out) == os.path.abspath(rom):
                return messagebox.showerror('Repack', 'Choose a different file name than the original ROM.')
            self.log('Repacking …')
            self.run_job(lambda: nds_repack.repack(rom, work, out, self.log))

        # ---------------------------------------------------------------- tree
        def fill_tree(self):
            self.tree.delete(*self.tree.get_children())
            self.tree_paths = {}
            root = self.work_var.get().strip()
            if not os.path.isdir(root):
                return
            flt = self.filter_var.get().strip().lower()
            dirs = {'': ''}

            def ensure(rel):
                if rel in dirs:
                    return dirs[rel]
                parent = ensure(os.path.dirname(rel))
                iid = self.tree.insert(parent, 'end', text=os.path.basename(rel) + '/', open=bool(flt))
                dirs[rel] = iid
                return iid

            n = 0
            for r, ds, fs in os.walk(root):
                ds[:] = sorted(d for d in ds if d != '_backup')
                for f in sorted(fs):
                    if flt and flt not in f.lower():
                        continue
                    rel = os.path.relpath(r, root)
                    rel = '' if rel == '.' else rel
                    iid = self.tree.insert(ensure(rel), 'end', text=f)
                    self.tree_paths[iid] = os.path.join(r, f)
                    n += 1
            self.info.set('%d files in %s' % (n, root))

        def on_select(self, _e=None):
            sel = self.tree.selection()
            if not sel or sel[0] not in self.tree_paths:
                return
            if not self._confirm_discard_text():
                return
            if self._room_dirty() and not messagebox.askyesno(
                    'Rooms', 'Discard the room box values you typed but did not save?'):
                return
            self.load_path(self.tree_paths[sel[0]])

        def _confirm_discard_text(self):
            if self.text_dirty and self.text_path:
                return messagebox.askyesno('Text', 'Discard the unsaved changes to %s?'
                                           % os.path.basename(self.text_path))
            return True

        def _has_backup(self, path):
            work = self.work_var.get().strip()
            return os.path.exists(backup_path(work, path)) if path else False

        def load_path(self, path, tex_index=None):
            low = path.lower()
            self.cur_path = path
            self.dock_file.set(os.path.basename(path))
            self.dock_kind.set(
                'level text' if low.endswith('.lvl') else
                'text' if low.endswith('.txt') else
                'collision' if low.endswith('.cvhm') else
                'placement' if low.endswith('.lp') else
                'rooms' if low.endswith('.roo') else
                'image' if low.endswith(('.char', '.plt', '.ico')) else
                'data' if low.endswith(('.crt', '.nsbca', '.nslbc', '.tex')) else
                'model' if low.endswith(('.nsbmd', '.nsbtx')) else 'file')
            self.dock_collision.set('no collision loaded')
            self.dock_collision_tris.set('0')
            self.loaded = None
            self.collision = None
            self.roo = None
            self.roo_room = None
            self.roo_base_view = None
            self.placement = None
            self.lp_view_tris = []
            self.base_view = None
            if getattr(self, 'lp_place_tree', None) is not None:
                self.lp_place_tree.delete(*self.lp_place_tree.get_children())
                self.lp_path_tree.delete(*self.lp_path_tree.get_children())
                self.lp_hint.set('Open a level\'s Objects.LP on the left to see and edit its enemy and '
                                 'object placements and its patrol paths.')
                for v in self.lp_vars:
                    v.set('')
                for v in self.lp_pvars:
                    v.set('')
                for v in self.lp_box_vars:
                    v.set('')
            self.room_tree.delete(*self.room_tree.get_children())
            self.rooms_hint.set('Open a level\'s Global.ROO in the list on the left to see and edit its room boxes.')
            for v in self.room_vars:
                v.set('')
            self.view_tris = []
            self._last_tex = None
            self.canvas.delete('all')
            if self.room_canvas is not None:
                self.room_canvas.delete('all')
            self.texlist.delete(0, 'end')
            self.tex_canvas.delete('all')
            self.pal_box['values'] = ()
            self.pal_var.set('')
            self.tex_info.set('')
            self._set_text('', False)
            self._clear_data()
            self.btn_revert.configure(state='normal' if self._has_backup(path) else 'disabled')
            name = os.path.basename(path)
            if low.endswith(('.txt', '.lvl')):
                try:
                    txt, info = read_text_doc(path)
                except Exception as e:
                    self.info.set('%s: could not read (%s)' % (name, e))
                    return
                self.text_path, self.text_info = path, info
                self._set_text(txt, True)
                self.text_label.set('%s  (%s line endings, %s) - editable'
                                    % (name, 'CRLF' if info['newline'] == '\r\n' else 'LF', info['encoding']))
                if low.endswith('.lvl'):
                    sections = extra_files.lvl_sections(txt)
                    self.info.set('%s  -  level text, %d section(s): %s'
                                  % (name, len(sections), ', '.join(sections)))
                else:
                    self.info.set('%s  -  text, %d lines, %d characters'
                                  % (name, txt.count('\n') + 1, len(txt)))
                self.nb.select(2)
                return
            if extra_files.is_data_file(path):
                self.load_data(path)
                return
            if low.endswith('.cvhm'):
                try:
                    col = collision.parse(path)
                except Exception as e:
                    self.info.set('%s: could not parse collision (%s)' % (name, e))
                    return
                self.collision = col
                self.view_tris = col.triangles()
                self.dock_collision.set(name)
                self.dock_collision_tris.set(str(len(self.view_tris)))
                self.view = bounds(self.view_tris)
                self.base_view = self.view
                parts = ', '.join('%s: %d verts / %d tris' % (m.name, len(m.verts), len(m.tris))
                                  for m in col.meshes)
                self.info.set('%s  -  collision v%d, %d triangle(s)  -  %s'
                              % (name, col.version, len(self.view_tris), parts))
                self.nb.select(0)
                self.reset_view()
                return
            if low.endswith('.lp'):
                try:
                    lp = lpmod.parse(path)
                except Exception as e:
                    self.info.set('%s: could not parse placement file (%s)' % (name, e))
                    return
                self.lp_load(path, lp)
                return
            if low.endswith('.roo'):
                try:
                    roo = collision.Roo(open(path, 'rb').read(), path)
                except Exception as e:
                    self.info.set('%s: not a level room table (%s)' % (name, e))
                    return
                self.roo = roo
                self.roo_room = 0 if roo.rooms else None
                self.roo_base_view = boxes_bounds([r['box'] for r in roo.rooms])
                self.view = self.roo_base_view
                self.base_view = self.view
                self.fill_rooms()
                self.rooms_hint.set(
                    '%s: %d room box(es). Pick a room, then edit its Min/Max values below. The game only '
                    'runs collision inside the room the player is in, so each box has to contain the '
                    'walkable area of that room.' % (name, roo.room_count))
                self.info.set('%s  -  %d room box(es), room table version %d'
                              % (name, roo.room_count, roo.version))
                self.nb.select(3)
                self.reset_view()
                return
            if not (low.endswith('.nsbmd') or low.endswith('.nsbtx')):
                self.info.set('%s  (%d bytes) - no preview for this type' % (name, os.path.getsize(path)))
                return
            try:
                self.loaded = Loaded(path)
            except Exception as e:
                self.info.set('%s: could not parse (%s)' % (name, e))
                return
            L = self.loaded
            if L.tex_path and L.tex_path != path and self._has_backup(L.tex_path):
                self.btn_revert.configure(state='normal')
            tris = L.triangles() if L.models else []
            self.view_tris = tris
            self.view = bounds(tris)
            self.base_view = self.view
            msg = name
            if L.models:
                msg += '  -  %d model(s), %d triangles' % (len(L.models), len(tris))
            msg += '  -  %d textures (%s)' % (len(L.tex.textures) if L.tex else 0, L.tex_source or 'none')
            self.info.set(msg)
            if L.tex:
                for t in L.tex.textures:
                    self.texlist.insert('end', '%s   [%s %dx%d]' % (t.name, FMT_NAMES.get(t.fmt, t.fmt), t.w, t.h))
                self.pal_box['values'] = list(L.tex.palettes.keys())
                if self.texlist.size():
                    i = tex_index if tex_index is not None and tex_index < self.texlist.size() else 0
                    self.texlist.selection_set(i)
                    self.show_texture()
            self.nb.select(0 if L.models else 1)
            self.reset_view()

        def _set_text(self, txt, editable=False):
            self.text.configure(state='normal')
            self.text.delete('1.0', 'end')
            self.text.insert('1.0', txt)
            self.text.edit_modified(False)
            self.text.edit_reset()
            self.text.configure(state='normal' if editable else 'disabled')
            self.text_dirty = False
            self.btn_text_save.configure(state='disabled')
            if not editable:
                self.text_path, self.text_info = None, None
                self.text_label.set('')

        def on_text_modified(self, _e=None):
            if self.text.edit_modified():
                self.text.edit_modified(False)      # fires again, then it is already False
                if self.text_path:
                    self.text_dirty = True
                    self.btn_text_save.configure(state='normal')

        def save_text(self):
            if not self.text_path or not self.text_info:
                return
            txt = self.text.get('1.0', 'end-1c')
            try:
                data = encode_text_doc(txt, self.text_info)
            except UnicodeEncodeError:
                return messagebox.showerror(
                    'Save text', 'This file uses %s and cannot store the characters you typed.\n'
                    'Only ASCII / latin-1 text can be written back into it.' % self.text_info['encoding'])
            work = self.work_var.get().strip()
            try:
                backup_file(work, self.text_path)
                with open(self.text_path, 'wb') as f:
                    f.write(data)
            except Exception as e:
                return messagebox.showerror('Save text', 'Could not save:\n%s' % e)
            self.text_dirty = False
            self.btn_text_save.configure(state='disabled')
            self.btn_revert.configure(state='normal' if self._has_backup(self.text_path) else 'disabled')
            self.log('saved %s (%d bytes)' % (self.text_path, len(data)))
            self.log('Repack the ROM to test it.')

        def reload_text(self):
            if not self.text_path:
                return
            if self.text_dirty and not messagebox.askyesno(
                    'Reload', 'Discard the unsaved changes to %s?' % os.path.basename(self.text_path)):
                return
            self.load_path(self.text_path)

        # ------------------------------------------------------- data files view
        def _set_readonly_text(self, widget, text):
            widget.configure(state='normal')
            widget.delete('1.0', 'end')
            widget.insert('1.0', text)
            widget.configure(state='disabled')

        def _clear_data(self):
            self.data = None
            self.data_raw = b''
            self.data_hex_off = 0
            self.data_palette = None
            self.data_char_raw = None
            self.btn_data_import.configure(state='disabled')
            self.btn_data_png.configure(state='disabled')
            self.data_label.set('Open a .char, .plt, .ico, .crt, .nsbca, .nslbc or .tex file '
                                'in the list on the left.')
            self.data_note.set('')
            self.data_pal_bar.pack_forget()
            self._set_readonly_text(self.data_summary, '')
            self.data_preview.delete('all')
            self.data_photo = None
            self._set_hex_text('', 0)

        def load_data(self, path):
            name = os.path.basename(path)
            try:
                ex = extra_files.open_extra(path)
                raw = open(path, 'rb').read()
            except Exception as e:
                self.info.set('%s: could not read (%s)' % (name, e))
                return
            self.data = ex
            self.data_raw = raw
            self.data_label.set('%s  (%s bytes) - view and edit below'
                                % (name, format(len(raw), ',')))
            self._set_readonly_text(self.data_summary, ex.summary)
            self.btn_data_import.configure(state='normal' if ex.import_kind else 'disabled')
            self.btn_data_png.configure(state='normal' if ex.image else 'disabled')
            self.data_note.set(ex.preview_note)
            self.data_palette = list(ex.palette) if ex.palette else None
            self.data_char_raw = ex.char_raw
            if ex.palette_editable:
                self.data_pal_bar.pack(fill='x', pady=(4, 0), before=self.data_split)
                self._update_swatches()
            else:
                self.data_pal_bar.pack_forget()
            self._draw_data_preview()
            self.show_data_hex(0)
            self.data_nb.select(0 if ex.kind == 'image' else 1)
            self.nb.select(5)
            self.info.set('%s  -  %s' % (name, 'picture' if ex.kind == 'image' else 'data file'))

        def reload_data(self):
            if self.cur_path and self.data:
                self.load_path(self.cur_path)

        def _update_swatches(self):
            for i, b in enumerate(self.data_swatches):
                if self.data_palette and i < len(self.data_palette):
                    r, g, bl = self.data_palette[i][:3]
                    lum = (r + g + bl) / 3
                    col = '#%02X%02X%02X' % (r, g, bl)
                    b.configure(background=col, activebackground=col,
                                foreground='#000000' if lum > 128 else '#ffffff')
                else:
                    b.configure(background='#f0f0f0', activebackground='#f0f0f0',
                                foreground='#000000')

        def edit_palette_color(self, index):
            if not self.data_palette or index >= len(self.data_palette):
                return
            col = self.data_palette[index]
            rgb, _hex = colorchooser.askcolor(
                color='#%02X%02X%02X' % tuple(col[:3]),
                title='Palette colour %d' % index)
            if not rgb:
                return
            self.data_palette[index] = tuple(int(v) for v in rgb)
            self._update_swatches()
            if self.data_char_raw:
                self.data.image = extra_files.decode_char(self.data_char_raw, self.data_palette)
            else:
                self.data.image = extra_files.palette_strip(self.data_palette)
            self._draw_data_preview()

        def _draw_data_preview(self):
            c = self.data_preview
            c.delete('all')
            im = self.data.image if self.data else None
            w = max(1, c.winfo_width())
            h = max(1, c.winfo_height())
            if im is None:
                if self.data:
                    c.create_text(w // 2, h // 2, anchor='center', fill='#c8c8c8',
                                  justify='center', font=('Segoe UI', 10),
                                  text='No picture preview for this format.\n'
                                       'Use the "Bytes (hex)" page and the patch box below.')
                return
            iw, ih = im.size
            scale = max(1, min((w - 16) // iw, (h - 16) // ih))
            scale = min(scale, 16)
            self.data_photo = ImageTk.PhotoImage(
                im.resize((iw * scale, ih * scale), Image.NEAREST))
            c.create_image(w // 2, h // 2, image=self.data_photo, anchor='center')
            c.create_text(6, 6, anchor='nw', text='%dx%d  (shown %dx)' % (iw, ih, scale),
                          fill='#c8c8c8', font=('Segoe UI', 8))

        def import_data_png(self):
            if not self.cur_path or not self.data or not self.data.import_kind:
                return
            p = filedialog.askopenfilename(
                title='PNG to import into %s' % os.path.basename(self.cur_path),
                filetypes=[('PNG picture', '*.png'), ('All files', '*.*')])
            if not p:
                return
            what = {'char': '32x32 graphic (and its 16-colour palette)',
                    'plt': '16 palette colours',
                    'ico': '64x64 portrait'}[self.data.import_kind]
            if not messagebox.askyesno(
                    'Import PNG', 'Replace the %s of\n%s\nwith the picture %s?\n\n'
                    'A copy of the original files is kept in _backup.'
                    % (what, os.path.basename(self.cur_path), os.path.basename(p))):
                return
            work = self.work_var.get().strip()
            try:
                backup_file(work, self.cur_path)
                if self.data.import_kind == 'char' and self.data.companion:
                    backup_file(work, self.data.companion)
                res = extra_files.import_png(self.cur_path, p)
            except Exception as e:
                return messagebox.showerror('Import PNG', 'Could not import:\n%s' % e)
            for path_, desc in res['written']:
                self.log('imported %s -> %s (%s)' % (os.path.basename(p), path_, desc))
            if res['note']:
                self.log(res['note'])
            self.log('Repack the ROM to test it.')
            self.load_path(self.cur_path)

        def save_data_png(self):
            if not self.cur_path or not self.data or not self.data.image:
                return
            base = os.path.splitext(os.path.basename(self.cur_path))[0]
            p = filedialog.asksaveasfilename(
                title='Save picture as PNG', defaultextension='.png',
                initialfile=base + '.png', filetypes=[('PNG picture', '*.png')])
            if not p:
                return
            try:
                self.data.image.save(p)
            except Exception as e:
                return messagebox.showerror('Save PNG', 'Could not save:\n%s' % e)
            self.log('saved picture %s' % p)

        def apply_data_palette(self):
            if not self.cur_path or not self.data_palette:
                return
            work = self.work_var.get().strip()
            try:
                backup_file(work, self.cur_path)
                extra_files.write_plt(self.cur_path, self.data_palette)
            except Exception as e:
                return messagebox.showerror('Palette', 'Could not save:\n%s' % e)
            self.log('saved palette %s (%d colours)' % (self.cur_path, len(self.data_palette)))
            self.log('Repack the ROM to test it.')
            self.load_path(self.cur_path)

        def _set_hex_text(self, text, offset):
            self.data_hex.configure(state='normal')
            self.data_hex.delete('1.0', 'end')
            self.data_hex.insert('1.0', text)
            self.data_hex.configure(state='disabled')
            self.data_hex.yview_moveto(0)
            self.data_hex_off = offset
            self.data_hex_off_var.set('0x%X' % offset)
            if self.data_raw:
                end = min(len(self.data_raw), offset + extra_files.HEX_PAGE)
                self.data_hex_range.set('0x%X - 0x%X of 0x%X'
                                        % (offset, max(offset, end - 1), len(self.data_raw)))
            else:
                self.data_hex_range.set('')

        def show_data_hex(self, offset):
            if not self.data_raw:
                self._set_hex_text('', 0)
                return
            offset = max(0, min(offset, len(self.data_raw) - 1))
            offset -= offset % 16
            self._set_hex_text('\n'.join(extra_files.hex_lines(self.data_raw, offset)), offset)

        def goto_data_hex(self):
            if not self.data_raw:
                return
            try:
                off = extra_files.parse_offset(self.data_hex_off_var.get(), len(self.data_raw))
            except Exception as e:
                return messagebox.showerror('Hex view', str(e))
            self.show_data_hex(off)

        def page_data_hex(self, direction):
            if not self.data_raw:
                return
            self.show_data_hex(self.data_hex_off + direction * extra_files.HEX_PAGE)

        def apply_data_patch(self):
            if not self.cur_path or not self.data:
                return
            try:
                off = extra_files.parse_offset(self.data_patch_off.get(), len(self.data_raw))
                blob = extra_files.parse_hex_bytes(self.data_patch_bytes.get())
                if off + len(blob) > len(self.data_raw):
                    raise extra_files.ExtraError(
                        'the patch runs past the end of the file (size 0x%X)' % len(self.data_raw))
            except Exception as e:
                return messagebox.showerror('Byte patch', str(e))
            if not messagebox.askyesno(
                    'Byte patch', 'Write %d byte(s) at 0x%X of %s?\n\n'
                    'A copy of the original is kept in _backup.'
                    % (len(blob), off, os.path.basename(self.cur_path))):
                return
            work = self.work_var.get().strip()
            try:
                backup_file(work, self.cur_path)
                extra_files.apply_patch(self.cur_path, off, blob)
            except Exception as e:
                return messagebox.showerror('Byte patch', 'Could not write:\n%s' % e)
            self.log('patched %s: %d byte(s) at 0x%X'
                     % (self.cur_path, len(blob), off))
            self.log('Repack the ROM to test it.')
            self.load_path(self.cur_path)
            self.show_data_hex(off)

        # ------------------------------------------------------------ rooms view
        def fill_rooms(self):
            tree = self.room_tree
            tree.delete(*tree.get_children())
            roo = self.roo
            if not roo:
                return
            for room in roo.rooms:
                b = room['box']
                tree.insert('', 'end', iid=str(room['index']), values=(
                    room['index'], room['name'] or '(unnamed)', '0x%03x' % room['flags'],
                    '%.2f' % b[0], '%.2f' % b[1], '%.2f' % b[2],
                    '%.2f' % b[3], '%.2f' % b[4], '%.2f' % b[5],
                    '%.1f x %.1f x %.1f' % (b[3] - b[0], b[4] - b[1], b[5] - b[2])))
            if self.roo_room is None and roo.rooms:
                self.roo_room = 0
            if self.roo_room is not None:
                tree.selection_set(str(self.roo_room))
                tree.see(str(self.roo_room))
                self._load_room_vars(self.roo_room)

        def _load_room_vars(self, idx):
            b = self.roo.rooms[idx]['box']
            for i in range(6):
                self.room_vars[i].set('%.3f' % b[i])

        def _room_entry_vals(self):
            try:
                return [float(v.get().strip().replace(',', '.')) for v in self.room_vars]
            except ValueError:
                return None

        def _room_dirty(self):
            if self.roo is None or self.roo_room is None:
                return False
            vals = self._room_entry_vals()
            if vals is None:
                return True
            return any(abs(vals[i] - self.roo.rooms[self.roo_room]['box'][i]) > 0.002 for i in range(6))

        def on_room_select(self, _e=None):
            sel = self.room_tree.selection()
            if not sel or self.roo is None:
                return
            idx = int(sel[0])
            if idx >= len(self.roo.rooms):
                return
            if idx != self.roo_room and self._room_dirty():
                room = self.roo.rooms[self.roo_room]
                if not messagebox.askyesno('Rooms', 'Discard the box values you typed for room %s without '
                                           'saving them?' % (room['name'] or self.roo_room)):
                    self.room_tree.selection_set(str(self.roo_room))
                    self._load_room_vars(self.roo_room)
                    return
            self.roo_room = idx
            self._load_room_vars(idx)
            self._focus_room()

        def _focus_room(self):
            """Centre the view on the selected room.  Rotation and zoom stay untouched."""
            if self.roo is None or self.roo_room is None or self.view is None:
                return
            b = self.roo.rooms[self.roo_room]['box']
            mid = ((b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2)
            self.view = (mid, self.view[1])          # same radius -> same apparent size
            self.redraw()

        def apply_room_box(self):
            roo, idx = self.roo, self.roo_room
            if roo is None or idx is None:
                return messagebox.showinfo('Rooms', 'Select a level\'s Global.ROO file and one of its rooms first.')
            vals = self._room_entry_vals()
            if vals is None:
                return messagebox.showerror('Rooms', 'Every box value must be a number (level units, e.g. -12.5).')
            bmin, bmax = vals[:3], vals[3:]
            if any(bmin[k] > bmax[k] for k in range(3)):
                return messagebox.showerror('Rooms', 'Each Min value has to be smaller than the matching Max value.')
            if any(abs(v) > 1e6 for v in vals):
                return messagebox.showerror('Rooms', 'Those coordinates are far outside the level (limit 1,000,000).')
            room = roo.rooms[idx]
            work = self.work_var.get().strip()
            if not roo.set_box(idx, bmin, bmax):
                self._load_room_vars(idx)
                return self.log('room %s: box unchanged' % (room['name'] or idx))
            try:
                backup_file(work, roo.path)
                roo.write()
            except Exception as e:
                return messagebox.showerror('Rooms', 'Could not save the room box:\n%s' % e)
            b = room['box']
            self.fill_rooms()
            self.btn_revert.configure(state='normal' if self._has_backup(roo.path) else 'disabled')
            self.redraw()
            self.log('room %s (%s): box set to [%.2f, %.2f, %.2f] .. [%.2f, %.2f, %.2f]'
                     % (room['name'] or '(unnamed)', os.path.basename(roo.path),
                        b[0], b[1], b[2], b[3], b[4], b[5]))
            self.log('Repack the ROM to test it. "Revert file to original" restores the old boxes.')

        def show_all_rooms(self):
            if self.roo is None:
                return
            self.view = self.roo_base_view or self.view
            self.yaw, self.pitch, self.zoom = 0.6, 0.35, 1.0
            self.redraw()

        def zoom_room(self):
            if self.roo is None or self.roo_room is None or not self.roo_base_view:
                return
            b = self.roo.rooms[self.roo_room]['box']
            center, room_radius = boxes_bounds([b])
            self.view = (center, self.roo_base_view[1])   # radius stays fixed, zoom does the work
            self.zoom = max(0.02, min(500.0, self.roo_base_view[1] / room_radius))
            self.redraw()

        # ------------------------------------------------------- placement view (.LP)
        def lp_load(self, path, lp):
            folder = os.path.dirname(path)
            tris = []
            for f in sorted(os.listdir(folder)):
                if f.lower().endswith('.cvhm'):
                    try:
                        tris.extend(collision.parse(os.path.join(folder, f)).triangles())
                    except Exception:
                        pass
            name = os.path.basename(path)
            chr_obj = None
            try:
                chr_obj = lpmod.load_chr(os.path.join(folder, 'CodeStrings.CHR'))
            except Exception:
                pass
            root = self.work_var.get().strip() or os.path.dirname(path)
            template_details = lpmod.load_template_details(os.path.join(root, 'Templates.CRT'),
                                                           os.path.join(root, 'Global.LVL'))
            p = dict(path=path, lp=lp, chr=chr_obj, templates={d['w1']: d['name'] for d in template_details},
                     template_details=template_details,
                     context_tris=tris,
                     sel_rec=None, sel_node=None, sel_path=None, rows={})
            p['base_view'] = self.lp_compute_view(lp, tris)
            self.placement = p
            self.view = p['base_view']
            self.base_view = p['base_view']
            n_ai = len(lp.records(lpmod.REC_AI))
            n_tr = len(lp.records(lpmod.REC_TRIGGER))
            n_ob = len(lp.records(lpmod.REC_STATIC))
            n_nodes = sum(pp.node_count for pp in lp.paths())
            self.lp_hint.set('%s  -  %d placements (%d objects, %d enemies, %d trigger boxes), '
                             '%d path(s) with %d node(s)  -  %s'
                             % (name, n_ob + n_ai + n_tr, n_ob, n_ai, n_tr, len(lp.paths()), n_nodes,
                                'CodeStrings.CHR loaded' if chr_obj else 'no CodeStrings.CHR found'))
            self.fill_lp_place_table()
            self.fill_lp_path_tree()
            self.info.set('%s  -  %d placements, %d path(s), %d path node(s)'
                          % (name, n_ob + n_ai + n_tr, len(lp.paths()), n_nodes))
            self.nb.select(4)
            self.reset_view()

        @staticmethod
        def lp_compute_view(lp, tris):
            pts = []
            for r in lp.records():
                pts.append(r.position)
                box = r.box
                if box:
                    pts += [box[0], box[1]]
            for path in lp.paths():
                for i in range(path.node_count):
                    pts.append(path.node_pos(i))
            if tris:
                c, r = bounds(tris)
                lo = [min(p[k] for p in tris for p in [p]) for k in range(3)] if False else None
                xs = [v[0] for t in tris for v in t]
                ys = [v[1] for t in tris for v in t]
                zs = [v[2] for t in tris for v in t]
                lo = (min(xs), min(ys), min(zs))
                hi = (max(xs), max(ys), max(zs))
            elif pts:
                lo = (min(p[0] for p in pts), min(p[1] for p in pts), min(p[2] for p in pts))
                hi = (max(p[0] for p in pts), max(p[1] for p in pts), max(p[2] for p in pts))
            else:
                lo = hi = (0, 0, 0)
            center = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)
            radius = max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / 2 or 1.0
            return center, radius

        def lp_context(self, c, w, h, fast=False):
            """Wireframe of the level geometry, faint, as a backdrop."""
            P = self.placement
            tris = P['context_tris']
            if not tris:
                return
            center, radius = self.view
            step = max(1, len(tris) // 1200) if fast else 1
            for i in range(0, len(tris), step):
                p = tris[i]
                pts = []
                for v in p:
                    x, y = self.lp_project(v, center, radius, w, h)
                    pts += [x, y]
                c.create_polygon(pts, fill='', outline='#3a4750')

        def lp_project(self, v, center, radius, w, h):
            tf, screen = _camera(center, radius, self.yaw, self.pitch, self.zoom, w, h)
            return screen(tf(v))

        def lp_draw_paths(self, c, w, h):
            P = self.placement
            center, radius = self.view
            tf, screen = _camera(center, radius, self.yaw, self.pitch, self.zoom, w, h)
            if not P or not P['lp'].paths():
                return
            paths = P['lp'].paths()
            sel_path, sel_node = P['sel_path'], P['sel_node']
            for pi, path in enumerate(paths):
                color = '#59b7e8' if sel_path == pi else '#2f7ba0'
                spts = [screen(tf(path.node_pos(i))) for i in range(path.node_count)]
                for a, b, _d in path.edges:
                    c.create_line(spts[a][0], spts[a][1], spts[b][0], spts[b][1],
                                  fill=color, width=2 if sel_path == pi else 1)
                for ni, sp in enumerate(spts):
                    is_sel = sel_node == (pi, ni)
                    r = 5 if is_sel else 3
                    c.create_oval(sp[0] - r, sp[1] - r, sp[0] + r, sp[1] + r,
                                  fill='#ffd24a' if is_sel else '#8fd0ff', outline='')

        def lp_draw_records(self, c, w, h):
            P = self.placement
            if not P:
                return
            center, radius = self.view
            tf, screen = _camera(center, radius, self.yaw, self.pitch, self.zoom, w, h)
            recs = P['lp'].records()
            for rec in recs:
                box = rec.box
                if box:
                    color = '#ffd24a' if rec is P['sel_rec'] else '#8a6d2f'
                    lo, hi = box
                    for p1, p2 in box_edges([lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]]):
                        x1, y1 = screen(tf(p1))
                        x2, y2 = screen(tf(p2))
                        c.create_line(x1, y1, x2, y2, fill=color,
                                      width=2 if rec is P['sel_rec'] else 1)
                    continue
                x, y = screen(tf(rec.position))
                is_sel = rec is P['sel_rec']
                r = 6 if is_sel else 3
                if is_sel:
                    c.create_oval(x - r - 2, y - r - 2, x + r + 2, y + r + 2,
                                  outline='#ffffff', width=2)
                c.create_oval(x - r, y - r, x + r, y + r,
                              fill=('#ff7a5c' if rec.is_ai else '#7ce38b'), outline='')
                if is_sel:
                    name = self.lp_rec_name(rec)
                    c.create_text(x + 10, y - 8, anchor='w', fill='#ffffff',
                                  font=('TkDefaultFont', 8), text=name)
        
        def lp_rec_name(self, rec):
            P = self.placement
            ch = P.get('chr') if P else None
            if ch and rec.h1 < ch.count:
                return ch.strings[rec.h1]
            return 'placement %d' % rec.h1

        def lp_name(self, w1):
            P = self.placement
            return P['templates'].get(w1, 'template %08x' % w1) if P else 'template %08x' % w1

        def _draw_lp(self, c, w, h, fast=False):
            P = self.placement
            if not P:
                return
            self.lp_context(c, w, h, fast)
            self.lp_draw_paths(c, w, h)
            self.lp_draw_records(c, w, h)
            c.create_text(8, max(8, h - 8), anchor='sw', fill='#9fb2c4', font=('TkDefaultFont', 8),
                          text=('placement: red = enemy, green = object, yellow box = trigger, '
                                'blue line/dot = path, yellow dot = selected'))

        def lp_points_view(self):
            P = self.placement
            pts = []
            for r in P['lp'].records():
                pts.append(r.position)
                box = r.box
                if box:
                    pts += [box[0], box[1]]
            for path in P['lp'].paths():
                for i in range(path.node_count):
                    pts.append(path.node_pos(i))
            if pts:
                lo = [min(p[k] for p in pts) for k in range(3)]
                hi = [max(p[k] for p in pts) for k in range(3)]
                c = tuple((lo[k] + hi[k]) / 2 for k in range(3))
                r = max(hi[k] - lo[k] for k in range(3)) / 2 or 1.0
                return c, r
            return self.view

        def fill_lp_place_table(self):
            tree = getattr(self, 'lp_place_tree', None)
            if tree is None:
                return
            tree.delete(*tree.get_children())
            P = self.placement
            P and P.update(rows={})
            if not P:
                return
            kind = self.lp_kind.get()
            flt = self.lp_filter.get().strip().lower()
            want = {'Everything': None, 'Objects': lpmod.REC_STATIC,
                    'Enemies (AI)': lpmod.REC_AI, 'Trigger boxes': lpmod.REC_TRIGGER}.get(kind)
            idx = 0
            for rec in P['lp'].records():
                if want is not None and rec.flags != want:
                    continue
                nm = self.lp_rec_name(rec)
                tpl = self.lp_name(rec.w1) if rec.w1 else '(no template)'
                if flt and flt not in nm.lower() and flt not in tpl.lower():
                    continue
                pos = rec.position
                iid = str(idx)
                idx += 1
                tree.insert('', 'end', iid=iid, values=(idx, rec.h1, nm, tpl,
                                                       '%.2f' % pos[0], '%.2f' % pos[1], '%.2f' % pos[2]))
                P['rows'][iid] = rec
            if P['sel_rec'] is not None:
                for iid, rec in P['rows'].items():
                    if rec is P['sel_rec']:
                        tree.selection_set(iid)
                        tree.see(iid)
                        break

        def fill_lp_path_tree(self):
            tree = getattr(self, 'lp_path_tree', None)
            if tree is None:
                return
            tree.delete(*tree.get_children())
            P = self.placement
            if not P:
                return
            paths = P['lp'].paths()
            for pi, path in enumerate(paths):
                color = 'path %d  (hash %08x, %d nodes, %d edges)' % (
                    pi, path.hash, path.node_count, path.edge_count)
                pid = tree.insert('', 'end', iid='p%d' % pi, text=color)
                for ni in range(path.node_count):
                    pos = path.node_pos(ni)
                    deg = sum(1 for a, b, _ in path.edges if a == ni)
                    tree.insert(pid, 'end', iid='p%dn%d' % (pi, ni),
                                text='node %d' % ni,
                                values=('%d' % ni, '%.2f' % pos[0], '%.2f' % pos[1],
                                        '%.2f' % pos[2], deg))
                tree.item(pid, open=True)

        def lp_place_selected(self):
            P = self.placement
            if not P:
                return None
            sel = self.lp_place_tree.selection()
            if not sel:
                return None
            return P['rows'].get(sel[0])

        def on_lp_place_select(self, _e=None):
            rec = self.lp_place_selected()
            P = self.placement
            if P is None:
                return
            P['sel_rec'] = rec
            P['sel_node'] = None
            for v in self.lp_vars:
                v.set('')
            for v in self.lp_box_vars:
                v.set('')
            if rec is None:
                return
            pos = rec.position
            for i in range(3):
                self.lp_vars[i].set('%.4f' % pos[i])
            if rec.is_ai:
                self.lp_vars[3].set(str(rec.angle))
            if rec.is_trigger:
                box = rec.box
                if box:
                    lo, hi = box
                    self.lp_box_vars[0].set('%.3f' % (hi[0] - lo[0]) / 2)
                    self.lp_box_vars[1].set('%.3f' % (hi[2] - lo[2]) / 2)
                    self.lp_box_vars[2].set('%.3f' % (hi[1] - lo[1]))
            self.redraw()

        def on_lp_path_select(self, _e=None):
            P = self.placement
            if P is None:
                return
            sel = self.lp_path_tree.selection()
            if not sel:
                P['sel_node'] = None
                P['sel_path'] = None
                return
            iid = sel[0]
            if iid.startswith('p') and 'n' not in iid[1:]:
                P['sel_path'] = int(iid[1:])
                P['sel_node'] = None
                for v in self.lp_pvars:
                    v.set('')
            else:
                head, node = iid[1:].split('n', 1)
                P['sel_path'] = int(head)
                P['sel_node'] = (int(head), int(node))
                path = P['lp'].paths()[int(head)]
                pos = path.node_pos(int(node))
                for i in range(3):
                    self.lp_pvars[i].set('%.4f' % pos[i])
            P['sel_rec'] = None
            self.redraw()

        def lp_write_file(self):
            P = self.placement
            lp = P['lp']
            work = self.work_var.get().strip()
            backup_file(work, P['path'])
            data = lp.to_bytes()
            with open(P['path'], 'wb') as f:
                f.write(data)
            if P.get('chr') is not None:
                chr_path = os.path.join(os.path.dirname(P['path']), 'CodeStrings.CHR')
                if os.path.exists(chr_path):
                    backup_file(work, chr_path)
                    with open(chr_path, 'wb') as f:
                        f.write(P['chr'].to_bytes())
            self.btn_revert.configure(state='normal' if self._has_backup(P['path']) else 'disabled')
            return len(data)

        def lp_apply_save(self):
            rec = self.lp_place_selected()
            P = self.placement
            if rec is None or P is None:
                return messagebox.showinfo('Placement', 'Select a placement first.')
            try:
                vals = [float(v.get().strip().replace(',', '.')) for v in self.lp_vars[:3]]
            except ValueError:
                return messagebox.showerror('Placement', 'X, Y and Z must be numbers (level units).')
            if any(abs(v) > 1e6 for v in vals):
                return messagebox.showerror('Placement', 'Those coordinates are far outside the level.')
            rec.set_position(*vals)
            if rec.is_ai:
                try:
                    rec.set_angle(int(self.lp_vars[3].get().strip() or '0'))
                except ValueError:
                    return messagebox.showerror('Placement', 'Facing must be a whole number of brads '
                                                             '(4096 = 360°).')
            if rec.is_trigger:
                try:
                    bv = [float(v.get().strip().replace(',', '.')) for v in self.lp_box_vars]
                except ValueError:
                    return messagebox.showerror('Placement', 'Box half extents and height must be numbers.')
                if bv[0] <= 0 or bv[1] <= 0 or bv[2] <= 0:
                    return messagebox.showerror('Placement', 'Half X, half Z and height must be positive.')
                rec.set_box(bv[0], bv[1], bv[2])
            n = self.lp_write_file()
            self.fill_lp_place_table()
            self.redraw()
            self.log('saved %s (%d bytes)  -  %s moved to (%.2f, %.2f, %.2f)'
                     % (os.path.basename(P['path']), n, self.lp_rec_name(rec), vals[0], vals[1], vals[2]))
            self.log('Repack the ROM to test it.')

        def lp_duplicate_placement(self):
            P = self.placement
            if P is None:
                return
            src = P['sel_rec']
            if src is None:
                return messagebox.showinfo('Placement', 'Select the placement you want to duplicate.')
            self.lp_add_dialog(src)

        def lp_add_placement(self):
            P = self.placement
            if P is None:
                return
            self.lp_add_dialog(None)

        def lp_delete_placement(self):
            P = self.placement
            rec = P and P['sel_rec']
            if rec is None:
                return messagebox.showinfo('Placement', 'Select a placement first.')
            if not messagebox.askyesno('Delete placement',
                                       'Delete %s (id %d) from this level?\n'
                                       'The file is backed up, so "Revert file to original" undoes it.'
                                       % (self.lp_rec_name(rec), rec.h1)):
                return
            P['lp'].remove_record(rec)
            P['sel_rec'] = None
            n = self.lp_write_file()
            self.fill_lp_place_table()
            self.redraw()
            self.log('deleted placement id %d from %s (%d bytes)'
                     % (rec.h1, os.path.basename(P['path']), n))
            self.log('Repack the ROM to test it.')

        def lp_zoom_sel(self):
            P = self.placement
            if not P:
                return
            pos = self.lp_selection_pos()
            if pos is None:
                return messagebox.showinfo('Placement', 'Select a placement or a path node first.')
            self.view = (pos, self.view[1])
            self.redraw()

        def lp_selection_pos(self):
            P = self.placement
            if P['sel_rec'] is not None:
                rec = P['sel_rec']
                box = rec.box
                if box:
                    lo, hi = box
                    return ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)
                return rec.position
            if P['sel_node'] is not None:
                pi, ni = P['sel_node']
                return P['lp'].paths()[pi].node_pos(ni)
            if P['sel_path'] is not None:
                path = P['lp'].paths()[P['sel_path']]
                if path.node_count:
                    return path.node_pos(0)
            return None

        def lp_focus_sel(self):
            self.lp_zoom_sel()

        def lp_show_all(self):
            P = self.placement
            if not P:
                return
            self.view = P['base_view'] or self.lp_points_view()
            self.yaw, self.pitch, self.zoom = 0.6, 0.35, 1.0
            self.redraw()

        def lp_add_dialog(self, src):
            P = self.placement
            lp = P['lp']
            dlg = tk.Toplevel(self)
            dlg.title('Duplicate placement' if src else 'Add placement')
            dlg.transient(self)
            dlg.grab_set()
            dlg.resizable(False, False)

            kind_var = tk.StringVar(value=('AI enemy' if src is not None and src.is_ai else
                                           'Trigger box' if src is not None and src.is_trigger else
                                           'Object'))
            details = P['template_details']
            kinds = {'Object': [d for d in details if d['kind'] == 'object'],
                     'AI enemy': [d for d in details if d['kind'] == 'ai']}
            tpl_var = tk.StringVar()
            name_var = tk.StringVar(value=(src and ('%s_copy' % self.lp_rec_name(src)) or 'new_object'))
            reuse_var = tk.BooleanVar(value=True)
            slot_var = tk.StringVar()

            ttk.Label(dlg, text='Kind:').grid(row=0, column=0, sticky='e', padx=6, pady=4)
            kind_box = ttk.Combobox(dlg, textvariable=kind_var, state='readonly',
                                    values=['Object', 'AI enemy', 'Trigger box'], width=20)
            kind_box.grid(row=0, column=1, sticky='w', padx=6, pady=4)

            ttk.Label(dlg, text='Template:').grid(row=1, column=0, sticky='e', padx=6, pady=4)
            tpl_box = ttk.Combobox(dlg, textvariable=tpl_var, state='readonly', width=42)
            tpl_box.grid(row=1, column=1, sticky='w', padx=6, pady=4)

            def refill_templates(*_):
                k = kind_var.get()
                if k == 'Trigger box':
                    tpl_box.configure(values=['(trigger volumes need no template)'], state='disabled')
                    tpl_var.set('(trigger volumes need no template)')
                    return
                opts = kinds.get(k) or [dict(w1=0, name='(no template)')]
                tpl_box.configure(values=['%s   [%08x]' % (d['name'], d['w1']) for d in opts],
                                  state='readonly')
                tpl_var.set('%s   [%08x]' % (opts[0]['name'], opts[0]['w1']))
            kind_box.bind('<<ComboboxSelected>>', refill_templates)
            refill_templates()

            ttk.Label(dlg, text='Name:').grid(row=2, column=0, sticky='e', padx=6, pady=4)
            ttk.Entry(dlg, textvariable=name_var, width=42).grid(row=2, column=1, sticky='w', padx=6, pady=4)

            # unused slots the user can reuse for the name (cheaper than extending CHR)
            ch = P['chr']
            unused = []
            if ch is not None:
                used = lp.h1_used()
                unused = [(h1, ch.strings[h1]) for h1 in range(ch.count) if h1 not in used]
            reuse = ttk.Frame(dlg)
            reuse.grid(row=3, column=0, columnspan=2, sticky='w', padx=6, pady=(2, 0))
            ttk.Checkbutton(reuse, text='Reuse an unused slot from the level\'s list:',
                            variable=reuse_var).pack(side='left')
            ttk.Combobox(reuse, textvariable=slot_var, state='readonly', width=30,
                         values=['%d: %s' % (h1, nm) for h1, nm in unused]).pack(side='left', padx=4)
            if unused:
                slot_var.set('%d: %s' % unused[0])
            ttk.Label(dlg, text='Otherwise a new name is appended to the level\'s CodeStrings.CHR.',
                      foreground='#606060').grid(row=4, column=0, columnspan=2, sticky='w', padx=6)

            ttk.Label(dlg, text='Position:').grid(row=5, column=0, sticky='e', padx=6, pady=4)
            posf = ttk.Frame(dlg)
            posf.grid(row=5, column=1, sticky='w', padx=6)
            pos_vars = [tk.StringVar() for _ in range(3)]
            view = self.view
            for i in range(3):
                ttk.Entry(posf, textvariable=pos_vars[i], width=9).pack(side='left', padx=2)
                pos_vars[i].set('%.2f' % view[0][i])
            ttk.Label(posf, text='  (view centre)', foreground='#606060').pack(side='left')

            def confirm():
                k = kind_var.get()
                try:
                    pos = [float(v.get().strip().replace(',', '.')) for v in pos_vars]
                except ValueError:
                    return messagebox.showerror('Add placement', 'Position must be three numbers.',
                                                parent=dlg)
                # name / h1
                if reuse_var.get() and unused:
                    sel = slot_var.get()
                    try:
                        h1 = int(sel.split(':', 1)[0])
                    except ValueError:
                        return messagebox.showerror('Add placement',
                                                    'Pick an unused slot from the list.', parent=dlg)
                    chr_obj = None
                else:
                    nm = name_var.get().strip()
                    if not nm:
                        return messagebox.showerror('Add placement',
                                                    'Enter a name for the new placement.', parent=dlg)
                    try:
                        nm.encode('ascii')
                    except UnicodeEncodeError:
                        return messagebox.showerror('Add placement',
                                                    'Names must be plain ASCII.', parent=dlg)
                    nm = nm.replace(' ', '_')
                    if ch is None:
                        return messagebox.showerror('Add placement',
                                                    'No CodeStrings.CHR found next to the level\'s '
                                                    'Objects.LP - cannot add new names.', parent=dlg)
                    h1 = ch.append([nm])[0]
                    chr_obj = ch
                # template / flags
                if k == 'Trigger box':
                    w1 = 0
                    flags = lpmod.REC_TRIGGER
                else:
                    tpl = tpl_var.get().rsplit('[', 1)
                    w1 = int(tpl[1].rstrip(']'), 16) if len(tpl) == 2 else 0
                    flags = lpmod.REC_AI if k == 'AI enemy' else lpmod.REC_STATIC
                w0s = [r.w0 for r in lp.records()]
                rec = lpmod.LPRecord(flags, lpmod.TRIGGER_SIZE if flags == lpmod.REC_TRIGGER
                                     else lpmod.RECORD_SIZE,
                                     (max(w0s) + 1) if w0s else 1, w1,
                                     [0, 0, 0], b'\x00\x00\x00\x00\x00\x00', h1, 0xffffffff, 0)
                rec.set_position(*pos)
                if flags == lpmod.REC_TRIGGER:
                    rec.set_box(1.0, 1.0, 3.0)
                if src is not None and src.is_ai:
                    rec.set_angle(src.angle)
                    rec.typ = src.typ
                if src is not None and src.is_trigger:
                    rec.extra6 = src.extra6
                if src is not None:
                    rec.typ = src.typ
                lp.add_record(rec)
                n = self.lp_write_file()
                self.fill_lp_place_table()
                self.redraw()
                self.log('added placement %s (id %d) to %s (%d bytes)'
                         % (name_var.get().strip() or 'new', rec.h1, os.path.basename(P['path']), n))
                self.log('Repack the ROM to test it.')
                dlg.destroy()

            bf = ttk.Frame(dlg)
            bf.grid(row=6, column=0, columnspan=2, pady=8)
            ttk.Button(bf, text='Cancel', command=dlg.destroy).pack(side='right', padx=4)
            ttk.Button(bf, text='Add', command=confirm).pack(side='right')
            dlg.wait_window(dlg)

        # ------------------------------------------------- path editing
        def lp_path_apply_save(self):
            P = self.placement
            if P is None:
                return
            sel = P['sel_node']
            if sel is None:
                return messagebox.showinfo('Paths', 'Select a path node first (click a node in the table '
                                                    'or the 3D view).')
            pi, ni = sel
            path = P['lp'].paths()[pi]
            try:
                vals = [float(v.get().strip().replace(',', '.')) for v in self.lp_pvars]
            except ValueError:
                return messagebox.showerror('Paths', 'X, Y and Z must be numbers (level units).')
            if any(abs(v) > 1e6 for v in vals):
                return messagebox.showerror('Paths', 'Those coordinates are far outside the level.')
            path.set_node_pos(ni, *vals)
            path.recompute_distances()
            n = self.lp_write_file()
            self.fill_lp_path_tree()
            self.redraw()
            self.log('path %d node %d moved to (%.2f, %.2f, %.2f)  -  saved %s (%d bytes)'
                     % (pi, ni, vals[0], vals[1], vals[2], os.path.basename(P['path']), n))
            self.log('Repack the ROM to test it.')

        def lp_path_selected_path(self):
            P = self.placement
            if P is None:
                return None, None
            if P['sel_path'] is not None:
                return P['lp'].paths()[P['sel_path']], P['sel_path']
            if P['sel_node'] is not None:
                pi, _ = P['sel_node']
                return P['lp'].paths()[pi], pi
            return None, None

        def lp_path_add_node(self):
            P = self.placement
            if P is None:
                return
            path, pi = self.lp_path_selected_path()
            if path is None:
                return messagebox.showinfo('Paths', 'Select a path first (or one of its nodes), or press '
                                                    '"Add path" to create a new one.')
            pos = self.view[0]
            path.nodes.append([lpmod._fixed_up(pos[0]), lpmod._fixed_up(pos[1]), lpmod._fixed_up(pos[2])])
            path.recompute_distances()
            n = self.lp_write_file()
            P['sel_node'] = (pi, path.node_count - 1)
            self.fill_lp_path_tree()
            self.redraw()
            self.log('path %d: added node %d at (%.2f, %.2f, %.2f)  -  saved %s (%d bytes)'
                     % (pi, path.node_count - 1, pos[0], pos[1], pos[2], os.path.basename(P['path']), n))

        def lp_path_del_node(self):
            P = self.placement
            if P is None:
                return
            sel = P['sel_node']
            if sel is None:
                return messagebox.showinfo('Paths', 'Select a node to remove.')
            pi, ni = sel
            path = P['lp'].paths()[pi]
            if not messagebox.askyesno('Remove node',
                                       'Remove node %d from path %d?\nEdges attached to it are removed too.'
                                       % (ni, pi)):
                return
            del path.nodes[ni]
            path.edges = [[a, b, d] for a, b, d in path.edges if a != ni and b != ni]
            path.edges = [[(a - 1 if a > ni else a), (b - 1 if b > ni else b), d]
                          for a, b, d in path.edges]
            path.recompute_distances()
            P['sel_node'] = None
            n = self.lp_write_file()
            self.fill_lp_path_tree()
            self.redraw()
            self.log('path %d: removed node %d  -  saved %s (%d bytes)'
                     % (pi, ni, os.path.basename(P['path']), n))

        def lp_path_add_path(self):
            P = self.placement
            if P is None:
                return
            hashes = [p.hash for p in P['lp'].paths()]
            new_hash = (max(hashes) + 1) if hashes else 0x1000
            P['lp'].add_path(lpmod.LPPath(new_hash, b'\x00' * 36, [], []))
            pi = len(P['lp'].paths()) - 1
            P['sel_path'] = pi
            P['sel_node'] = None
            n = self.lp_write_file()
            self.fill_lp_path_tree()
            self.redraw()
            self.log('added path %d (hash %08x)  -  saved %s (%d bytes)'
                     % (pi, new_hash, os.path.basename(P['path']), n))

        def lp_path_del_path(self):
            P = self.placement
            if P is None:
                return
            path, pi = self.lp_path_selected_path()
            if path is None:
                return messagebox.showinfo('Paths', 'Select a path to delete (click its title row).')
            if not messagebox.askyesno('Delete path',
                                       'Delete path %d (%d nodes, %d edges)?'
                                       % (pi, path.node_count, path.edge_count)):
                return
            P['lp'].remove_path(path)
            P['sel_path'] = None
            P['sel_node'] = None
            n = self.lp_write_file()
            self.fill_lp_path_tree()
            self.redraw()
            self.log('deleted path %d  -  saved %s (%d bytes)' % (pi, os.path.basename(P['path']), n))

        def lp_path_add_edge(self):
            P = self.placement
            if P is None:
                return
            path, pi = self.lp_path_selected_path()
            if path is None:
                return messagebox.showinfo('Paths', 'Select a path first (or one of its nodes).')
            if path.node_count < 2:
                return messagebox.showinfo('Paths', 'Add at least two nodes to the path first.')
            dlg = tk.Toplevel(self)
            dlg.title('Link nodes')
            dlg.transient(self)
            dlg.grab_set()
            dlg.resizable(False, False)
            opts = ['node %d   (%.2f, %.2f, %.2f)' % (
                i, path.node_pos(i)[0], path.node_pos(i)[1], path.node_pos(i)[2])
                for i in range(path.node_count)]
            fv, tv = tk.StringVar(value=opts[0]), tk.StringVar(value=opts[1])
            ttk.Label(dlg, text='From:').grid(row=0, column=0, sticky='e', padx=6, pady=4)
            ttk.Combobox(dlg, textvariable=fv, values=opts, state='readonly', width=34).grid(
                row=0, column=1, padx=6, pady=4)
            ttk.Label(dlg, text='To:').grid(row=1, column=0, sticky='e', padx=6, pady=4)
            ttk.Combobox(dlg, textvariable=tv, values=opts, state='readonly', width=34).grid(
                row=1, column=1, padx=6, pady=4)

            def confirm():
                a = int(fv.get().split()[1])
                b = int(tv.get().split()[1])
                if a == b:
                    return messagebox.showerror('Link nodes', 'Pick two different nodes.', parent=dlg)
                exists = any((x == a and y == b) or (x == b and y == a) for x, y, _ in path.edges)
                if exists:
                    return messagebox.showerror('Link nodes', 'Those nodes are already linked.',
                                                parent=dlg)
                path.edges.append([a, b, 0])
                path.edges.append([b, a, 0])
                path.recompute_distances()
                n = self.lp_write_file()
                self.fill_lp_path_tree()
                self.redraw()
                self.log('path %d: linked node %d and node %d  -  saved %s (%d bytes)'
                         % (pi, a, b, os.path.basename(P['path']), n))
                dlg.destroy()

            bf = ttk.Frame(dlg)
            bf.grid(row=2, column=0, columnspan=2, pady=8)
            ttk.Button(bf, text='Cancel', command=dlg.destroy).pack(side='right', padx=4)
            ttk.Button(bf, text='Link', command=confirm).pack(side='right')
            dlg.wait_window(dlg)

        def lp_path_del_edge(self):
            P = self.placement
            if P is None:
                return
            path, pi = self.lp_path_selected_path()
            if path is None:
                return messagebox.showinfo('Paths', 'Select a path first (or one of its nodes).')
            seen = set()
            pairs = []
            for a, b, _d in path.edges:
                key = (min(a, b), max(a, b))
                if key in seen:
                    continue
                seen.add(key)
                pairs.append(key)
            if not pairs:
                return messagebox.showinfo('Paths', 'This path has no edges.')
            dlg = tk.Toplevel(self)
            dlg.title('Unlink nodes')
            dlg.transient(self)
            dlg.grab_set()
            dlg.resizable(False, False)
            ttk.Label(dlg, text='Select an edge to remove (both directions go):').pack(
                anchor='w', padx=8, pady=(8, 4))
            lb = tk.Listbox(dlg, height=min(12, len(pairs)), width=52, exportselection=False)
            for a, b in pairs:
                pa = path.node_pos(a)
                pb = path.node_pos(b)
                lb.insert('end', 'node %d (%.2f, %.2f, %.2f)  <->  node %d (%.2f, %.2f, %.2f)'
                              % (a, pa[0], pa[1], pa[2], b, pb[0], pb[1], pb[2]))
            lb.pack(padx=8, fill='both', expand=True)

            def confirm():
                sel = lb.curselection()
                if not sel:
                    return messagebox.showerror('Unlink nodes', 'Pick an edge to remove.', parent=dlg)
                a, b = pairs[sel[0]]
                path.edges = [[x, y, d] for x, y, d in path.edges
                              if not ((x == a and y == b) or (x == b and y == a))]
                path.recompute_distances()
                n = self.lp_write_file()
                self.fill_lp_path_tree()
                self.redraw()
                self.log('path %d: unlinked node %d and node %d  -  saved %s (%d bytes)'
                         % (pi, a, b, os.path.basename(P['path']), n))
                dlg.destroy()

            bf = ttk.Frame(dlg)
            bf.pack(pady=8)
            ttk.Button(bf, text='Cancel', command=dlg.destroy).pack(side='right', padx=4)
            ttk.Button(bf, text='Unlink', command=confirm).pack(side='right')
            dlg.wait_window(dlg)

        # ------------------------------------------------------------ model view
        def reset_view(self):
            self.yaw, self.pitch, self.zoom = 0.6, 0.35, 1.0
            if self.roo is not None and self.roo_base_view:
                self.view = self.roo_base_view
            elif self.base_view:
                self.view = self.base_view
            self.redraw()

        def zoom_by(self, f):
            self.zoom = max(0.02, min(500, self.zoom * f))
            self.redraw()

        def on_wheel(self, e):
            self.zoom_by(1.1 if e.delta > 0 else 1 / 1.1)

        def on_press(self, e):
            self.drag = (e.x, e.y)

        def on_drag(self, e):
            if not self.drag:
                return
            dx, dy = e.x - self.drag[0], e.y - self.drag[1]
            self.drag = (e.x, e.y)
            self.yaw += dx * 0.01
            self.pitch = max(-1.55, min(1.55, self.pitch + dy * 0.01))
            self.redraw(fast=True)

        def on_release(self, _e):
            self.drag = None
            self.redraw()

        def on_press_r(self, e):
            self.drag_r = (e.x, e.y)

        def on_drag_r(self, e):
            if not self.drag_r:
                return
            dx, dy = e.x - self.drag_r[0], e.y - self.drag_r[1]
            self.drag_r = (e.x, e.y)
            self.pan_by(dx, dy, e.widget)

        def on_release_r(self, _e):
            self.drag_r = None
            self.redraw()

        def pan_by(self, dx, dy, canvas=None):
            """Move the view sideways: the scene follows the mouse, angles and zoom unchanged."""
            if self.view is None:
                return
            c = canvas or self.canvas
            w, h = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
            center, radius = self.view
            scale = self.zoom * min(w, h) / (2.2 * radius)
            cy, sy_ = math.cos(self.yaw), math.sin(self.yaw)
            cp, sp = math.cos(self.pitch), math.sin(self.pitch)
            # screen delta -> camera-space move of the looked-at point (cam[1] is flipped on screen)
            u = -dx / scale
            v = dy / scale
            self.view = ((center[0] + cy * u + sy_ * sp * v,
                          center[1] + cp * v,
                          center[2] + sy_ * u - cy * sp * v), radius)
            self.redraw(fast=True)

        def redraw(self, fast=False):
            if getattr(self, 'dock_yaw', None) is not None:
                self.dock_yaw.set('%.2f' % self.yaw)
                self.dock_pitch.set('%.2f' % self.pitch)
                self.dock_zoom.set('%.3f' % self.zoom)
            c = self.canvas
            c.delete('all')
            w, h = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
            if self.roo is not None:
                self._draw_rooms(c, w, h)
            elif self.placement is not None:
                self._draw_lp(c, w, h, fast)      # same overlay as the Placement tab
            else:
                self._draw_tris(c, w, h, fast)
            rc = getattr(self, 'room_canvas', None)
            if rc is not None:
                rc.delete('all')
                if self.roo is not None:
                    self._draw_rooms(rc, max(rc.winfo_width(), 50), max(rc.winfo_height(), 50))
            pc = getattr(self, 'lp_canvas', None)
            if pc is not None:
                pc.delete('all')
                if self.placement is not None:
                    self._draw_lp(pc, max(pc.winfo_width(), 50), max(pc.winfo_height(), 50), fast)

        def _draw_tris(self, c, w, h, fast=False):
            tris = self.view_tris
            if not tris:
                return
            step = max(1, len(tris) // 2500) if fast else 1
            center, radius = self.view
            solid = self.solid.get() and len(tris) <= 12000
            for _d, pts, shade in project(tris, center, radius, self.yaw, self.pitch, self.zoom, w, h, step):
                if solid:
                    g = int(60 + 180 * min(1.0, shade))
                    c.create_polygon(pts, fill='#%02x%02x%02x' % (g, g, min(255, g + 12)), outline='')
                else:
                    c.create_polygon(pts, fill='', outline='#8fd0ff')

        def _draw_rooms(self, c, w, h):
            """Wireframe of every room box of a Global.ROO, the selected one highlighted."""
            roo = self.roo
            if not roo or not roo.rooms:
                return
            center, radius = self.view
            tf, screen = _camera(center, radius, self.yaw, self.pitch, self.zoom, w, h)
            order = [r['index'] for r in roo.rooms if r['index'] != self.roo_room]
            if self.roo_room is not None:
                order.append(self.roo_room)          # selected box drawn last, on top
            for idx in order:
                room = roo.rooms[idx]
                b = room['box']
                sel = idx == self.roo_room
                color = '#ffd24a' if sel else '#7fa8c9'
                for p1, p2 in box_edges(b):
                    x1, y1 = screen(tf(p1))
                    x2, y2 = screen(tf(p2))
                    c.create_line(x1, y1, x2, y2, fill=color, width=2 if sel else 1)
                if room['name']:
                    x0, y0 = screen(tf((b[0], b[1], b[2])))
                    x1, y1 = screen(tf((b[3], b[4], b[5])))
                    if sel or max(abs(x1 - x0), abs(y1 - y0)) > 60:
                        mid = ((b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2)
                        sx, sy = screen(tf(mid))
                        c.create_text(sx, sy, text=room['name'], fill=color, font=('TkDefaultFont', 8),
                                      anchor='center')
            c.create_text(8, max(8, h - 8), anchor='sw', fill='#9fb2c4', font=('TkDefaultFont', 8), text=(
                'room boxes: yellow = selected, blue = others   -   drag = rotate, '
                'right-drag = pan, wheel = zoom'))

        # ------------------------------------------------------------ texture view
        def _current_tex(self):
            L = self.loaded
            sel = self.texlist.curselection()
            if not L or not L.tex or not sel:
                return None, None
            t = L.tex.textures[sel[0]]
            name = self.pal_var.get()
            pal = L.tex.palettes.get(name) if name else None
            return t, pal

        def show_texture(self):
            L = self.loaded
            sel = self.texlist.curselection()
            if not L or not L.tex or not sel:
                return
            t = L.tex.textures[sel[0]]
            changed = (sel != self._last_tex)
            self._last_tex = sel
            if changed or not self.pal_var.get():
                pn = L.tex_pal.get(t.name)
                if pn not in L.tex.palettes:
                    pn = next(iter(L.tex.palettes), '')
                self.pal_var.set(pn)
            pal = L.tex.palettes.get(self.pal_var.get(), [])
            try:
                im = texture_image(t, pal)
            except Exception as e:
                self.tex_info.set('decode error: %s' % e)
                return
            scale = max(1, min(8, 384 // max(t.w, t.h)))
            self.photo = ImageTk.PhotoImage(on_checker(im, scale))
            self.tex_canvas.delete('all')
            self.tex_canvas.create_image(8, 8, anchor='nw', image=self.photo)
            self.tex_info.set('%s - %s %dx%d%s - palette has room for %d colours' % (
                t.name, FMT_NAMES.get(t.fmt, t.fmt), t.w, t.h,
                ' (colour 0 transparent)' if t.color0_transp else '', len(pal)))

        def save_texture(self):
            t, pal = self._current_tex()
            if not t:
                return
            p = filedialog.asksaveasfilename(defaultextension='.png', initialfile=t.name + '.png',
                                             filetypes=[('PNG image', '*.png')])
            if p:
                texture_image(t, pal or []).save(p)
                self.log('saved ' + p)

        def save_all_textures(self):
            L = self.loaded
            if not L or not L.tex:
                return
            d = filedialog.askdirectory(title='Folder for PNGs')
            if not d:
                return
            for t in L.tex.textures:
                pn = L.tex_pal.get(t.name)
                pal = L.tex.palettes.get(pn) or next(iter(L.tex.palettes.values()), [])
                try:
                    texture_image(t, pal).save(os.path.join(d, t.name + '.png'))
                except Exception as e:
                    self.log('skip %s: %s' % (t.name, e))
            self.log('saved %d textures to %s' % (len(L.tex.textures), d))

        # ---------------------------------------------------------------- imports
        def _need_writer(self):
            if nitro_write is None:
                messagebox.showerror('Import', 'Importing needs the numpy package (%s).\n\nOpen a command prompt and run:\n'
                                     '    pip install numpy\nthen restart the tool.' % NW_ERROR)
                return False
            return True

        def do_import_png(self):
            if not self._need_writer():
                return
            L = self.loaded
            t, _ = self._current_tex()
            if not L or not t:
                return messagebox.showinfo('Import PNG', 'Select a file with textures, then a texture in the list.')
            png = filedialog.askopenfilename(title='PNG to import into "%s"' % t.name,
                                             filetypes=[('PNG image', '*.png'), ('All images', '*.*')])
            if not png:
                return
            try:
                im = Image.open(png)
                im.load()
            except Exception as e:
                return messagebox.showerror('Import PNG', 'Could not open the image:\n%s' % e)
            if im.size != (t.w, t.h):
                if not messagebox.askyesno('Import PNG', 'The image is %dx%d but the texture is %dx%d.\n'
                                           'Resize the image to fit?' % (im.width, im.height, t.w, t.h)):
                    return
            tex_path, path, name = L.tex_path, L.path, t.name
            pal_name = self.pal_var.get() or None
            idx = self.texlist.curselection()[0]
            work = self.work_var.get().strip()

            def job():
                new = nitro_write.import_png_texture(tex_path, name, im, pal_name, True, self.log)
                backup_file(work, tex_path)
                with open(tex_path, 'wb') as f:
                    f.write(new)
                self.log('imported %s into %s. Repack the ROM to test it.' % (os.path.basename(png), os.path.basename(tex_path)))
            self.run_job(job, after=lambda: self.load_path(path, idx))

        def do_replace_obj(self):
            if not self._need_writer():
                return
            L = self.loaded
            if not L or not L.models:
                return messagebox.showinfo('Replace model', 'Select the .nsbmd model you want to replace first.')
            obj = filedialog.askopenfilename(title='OBJ that should replace %s' % os.path.basename(L.path),
                                             filetypes=[('Wavefront OBJ', '*.obj'), ('All files', '*.*')])
            if not obj:
                return
            path = L.path
            work = self.work_var.get().strip()
            shared_tex, shared_path = find_shared_tex(path, True)
            try:
                info = nitro_write.analyze_replace(path, obj, shared_tex)
            except Exception as e:
                return messagebox.showerror('Replace model', 'Could not read the OBJ:\n%s' % e)
            dlg = MapDialog(self, info)
            if dlg.result is None:
                return
            mapping, import_tex, disable_bb = dlg.result

            def job():
                files, rep = nitro_write.replace_model(path, obj, mapping, import_tex, shared_tex, shared_path,
                                                       self.log, disable_bb)
                for p, data in files.items():
                    backup_file(work, p)
                    with open(p, 'wb') as f:
                        f.write(data)
                self.log('replaced %s with %s: %d/%d shape slots used, %d triangles, %d texture(s) -> %s, '
                         'file size %+d bytes'
                         % (os.path.basename(path), os.path.basename(obj), rep['slots_used'], rep['slots'],
                            rep['total_tris'], len(rep['textures']), rep.get('texture_bank') or 'nothing',
                            rep['size_delta']))
                if rep['total_tris'] > 2048:
                    self.log('NOTE: the DS draws at most ~2048 triangles per frame; this model has %d.' % rep['total_tris'])
                cvs = sorted(f for f in os.listdir(os.path.dirname(path)) if f.lower().endswith('.cvhm'))
                if cvs:
                    self.log('This folder has %d collision file(s) (.CVHM). If the model changed shape, open the '
                             'matching .CVHM and use "Generate collision from OBJ" with this OBJ.' % len(cvs))
                self.log('Repack the ROM to test it.')
            self.run_job(job, after=lambda: self.load_path(path))

        def do_revert(self):
            path = self.cur_path
            L = self.loaded
            work = self.work_var.get().strip()
            targets = [path]
            if L and L.tex_path and L.tex_path != path:
                targets.append(L.tex_path)
            if self.data and self.data.import_kind == 'char' and self.data.companion:
                targets.append(self.data.companion)
            targets = [p for p in targets if p and os.path.exists(backup_path(work, p))]
            if not targets:
                return
            if not messagebox.askyesno('Revert', 'Restore the original version of:\n%s\n\nYour changes to these files '
                                       'will be lost.' % '\n'.join(os.path.basename(p) for p in targets)):
                return
            import shutil
            for p in targets:
                shutil.copy2(backup_path(work, p), p)
                self.log('restored ' + p)
            self.load_path(path)

        # ---------------------------------------------------------------- exports
        def do_export_one(self):
            L = self.loaded
            if not L or not L.models:
                return messagebox.showinfo('Export', 'Select a .nsbmd model first.')
            d = filedialog.askdirectory(title='Export folder')
            if not d:
                return
            path = L.path

            def job():
                s = export_models.export(path, d, find_shared_tex(path))
                self.log('exported %s: %d triangles, %d textures -> %s' % (os.path.basename(path), s['tris'], s['tex'], d))
                self.log('Keep the shpNNN_ prefix of the object names when editing, so "Replace with any OBJ" can match them.')
            self.run_job(job, after=lambda: open_folder(d))

        # ------------------------------------------------------------- collision
        def do_export_collision(self):
            col = self.collision
            if not col:
                return messagebox.showinfo('Collision', 'Select a .CVHM collision file in the list on the left first.')
            base = os.path.splitext(os.path.basename(col.path))[0]
            p = filedialog.asksaveasfilename(title='Save collision as OBJ', defaultextension='.obj',
                                             initialfile=base + '_collision.obj',
                                             filetypes=[('Wavefront OBJ', '*.obj')])
            if not p:
                return
            try:
                s = collision.export_obj(col, p)
            except Exception as e:
                return messagebox.showerror('Collision', 'Could not export:\n%s' % e)
            self.log('exported collision %s: %d triangles, %d vertices -> %s'
                     % (base, s['tris'], s['verts'], p))
            self.log('The collision OBJ is in the level\'s world position (the render model OBJ is not).')

        def do_generate_collision(self):
            col = self.collision
            if not col:
                return messagebox.showinfo('Collision', 'Select the .CVHM collision file you want to replace first, '
                                           'then choose the OBJ to build it from.')
            obj = filedialog.askopenfilename(title='OBJ to build %s from' % os.path.basename(col.path),
                                             filetypes=[('Wavefront OBJ', '*.obj'), ('All files', '*.*')])
            if not obj:
                return
            try:
                dlg = CollisionDialog(self, col, obj)
            except Exception as e:
                return messagebox.showerror('Generate collision', 'Could not read the OBJ:\n%s' % e)
            if dlg.result is None:
                return
            mode, off = dlg.result
            path, work = col.path, self.work_var.get().strip()

            def job():
                old_bounds = col.bounds()
                new, info = collision.generate_from_obj(obj, target_path=path, mode=mode,
                                                        offset=off, log=self.log)
                backup_file(work, path)
                size = new.write(path)
                self.log('wrote %s: %d triangles, %d vertices, %d bytes (%s)'
                         % (os.path.basename(path), info['tris'], info['verts'], size,
                            'moved onto the old collision' if mode == 'auto' else 'coordinates kept (as the model '
                            'importer does)'))
                grown = collision.grow_level_box(path, old_bounds, new.bounds(),
                                                 extra_names=new.names(), log=self.log)
                if grown:
                    roo_path, data, rinfo = grown
                    if rinfo['changed']:
                        backup_file(work, roo_path)
                        with open(roo_path, 'wb') as f:
                            f.write(data)
                if info['tris'] > 2048:
                    self.log('NOTE: this much collision is fine for the format but heavy for the DS; '
                             'the original had %d triangles' % sum(len(m.tris) for m in col.meshes))
                self.log('Repack the ROM to test it.')
            self.run_job(job, after=lambda: self.load_path(path))

        def do_export_all(self):
            root = self.work_var.get().strip()
            if not os.path.isdir(root):
                return messagebox.showerror('Export', 'Unpack the ROM (or pick a work folder) first.')
            d = filedialog.askdirectory(title='Export folder for ALL models')
            if not d:
                return

            def job():
                files, cvs = [], []
                for r, ds, fs in os.walk(root):
                    ds[:] = [x for x in ds if x != '_backup']
                    for f in fs:
                        low = f.lower()
                        if low.endswith('.nsbmd'):
                            files.append(os.path.join(r, f))
                        elif low.endswith('.cvhm'):
                            cvs.append(os.path.join(r, f))
                files.sort()
                cvs.sort()
                self.log('exporting %d models and %d collision map(s) …' % (len(files), len(cvs)))
                cache, bad = {}, 0
                for p in files:
                    dd = os.path.dirname(p)
                    if dd not in cache:
                        cache[dd] = find_shared_tex(p)
                    rel = os.path.relpath(dd, root)
                    try:
                        export_models.export(p, os.path.join(d, rel), cache[dd])
                    except Exception as e:
                        bad += 1
                        self.log('FAIL %s: %s' % (os.path.relpath(p, root), e))
                bad_col = 0
                for p in cvs:
                    rel = os.path.relpath(os.path.dirname(p), root)
                    out = os.path.join(d, rel)
                    os.makedirs(out, exist_ok=True)
                    try:
                        col = collision.parse(p)
                        name = os.path.splitext(os.path.basename(p))[0] + '_collision.obj'
                        collision.export_obj(col, os.path.join(out, name))
                    except Exception as e:
                        bad_col += 1
                        self.log('FAIL %s: %s' % (os.path.relpath(p, root), e))
                self.log('done: %d models and %d collision map(s) exported, %d failed -> %s'
                         % (len(files) - bad, len(cvs) - bad_col, bad + bad_col, d))
            self.run_job(job, after=lambda: open_folder(d))


def main():
    if tk is None:
        _fatal('Tkinter / Pillow ImageTk is not available.\n'
               '  Windows/macOS: install Python from python.org (it includes Tkinter), then: pip install pillow numpy\n'
               '  Linux: sudo apt install python3-tk python3-pil.imagetk')
    try:
        App().mainloop()
    except Exception:
        _fatal('The tool crashed:\n\n' + traceback.format_exc())


if __name__ == '__main__':
    main()

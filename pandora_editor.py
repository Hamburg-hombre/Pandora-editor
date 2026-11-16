#!/usr/bin/env python3
"""
GoldenEye: Rogue Agent (DS) - Pandora editor GUI
Run:  python pandora_editor.py
Needs: Python 3.8+, Pillow and numpy (pip install pillow numpy). Tkinter ships with Python on
Windows/macOS (Linux: sudo apt install python3-tk).
Keep this file in the same folder as nitro.py, scene.py, nitro_write.py, nds_unpack.py,
nds_repack.py, export_models.py and collision.py.
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
    from tkinter import ttk, filedialog, messagebox
    from PIL import ImageTk
except ImportError:          # headless use (tests); main() explains the problem
    tk = None

try:
    from nitro import *          # noqa
    import nds_unpack, nds_repack, export_models, collision
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


def project(tris, center, radius, yaw, pitch, zoom, w, h, step=1):
    """Return [(depth, [x1,y1,x2,y2,x3,y3], shade 0..1)] sorted far -> near."""
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
            pts += [w / 2 + p[0] * scale, h / 2 - p[1] * scale]
        out.append(((a[2] + b[2] + c[2]) / 3, pts, shade))
    out.sort(key=lambda t: t[0])
    return out


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
            self.title('GoldenEye: Rogue Agent DS - Pandora editor')
            self.geometry('1150x780')
            self.minsize(900, 600)
            self.q = queue.Queue()
            self.busy = False
            self.loaded = None
            self.collision = None
            self.view_tris = []
            self.cur_path = None
            self.tree_paths = {}
            self.yaw, self.pitch, self.zoom = 0.6, 0.35, 1.0
            self.drag = None
            self.view = None
            self.photo = None
            self._last_tex = None
            self.text_path = None
            self.text_info = None
            self.text_dirty = False
            self._build()
            self.after(100, self._poll)

        # ------------------------------------------------------------ layout
        def _build(self):
            top = ttk.Frame(self, padding=6)
            top.pack(fill='x')
            self.rom_var, self.work_var = tk.StringVar(), tk.StringVar()
            ttk.Label(top, text='ROM (.nds):').grid(row=0, column=0, sticky='e')
            ttk.Entry(top, textvariable=self.rom_var).grid(row=0, column=1, sticky='ew', padx=4)
            ttk.Button(top, text='Browse…', command=self.pick_rom).grid(row=0, column=2)
            ttk.Button(top, text='Unpack ROM', command=self.do_unpack).grid(row=0, column=3, padx=(8, 0))
            ttk.Label(top, text='Work folder:').grid(row=1, column=0, sticky='e')
            ttk.Entry(top, textvariable=self.work_var).grid(row=1, column=1, sticky='ew', padx=4, pady=3)
            ttk.Button(top, text='Browse…', command=self.pick_work).grid(row=1, column=2)
            ttk.Button(top, text='Repack ROM…', command=self.do_repack).grid(row=1, column=3, padx=(8, 0))
            top.columnconfigure(1, weight=1)

            body = ttk.PanedWindow(self, orient='horizontal')
            body.pack(fill='both', expand=True, padx=6)

            left = ttk.Frame(body)
            body.add(left, weight=1)
            self.filter_var = tk.StringVar()
            self.filter_var.trace_add('write', lambda *a: self.fill_tree())
            ttk.Entry(left, textvariable=self.filter_var).pack(fill='x')
            ttk.Label(left, text='filter files (e.g. .nsbmd, barrel, .txt)', foreground='gray').pack(anchor='w')
            tf = ttk.Frame(left)
            tf.pack(fill='both', expand=True)
            self.tree = ttk.Treeview(tf, show='tree', selectmode='browse')
            sb = ttk.Scrollbar(tf, command=self.tree.yview)
            self.tree.configure(yscrollcommand=sb.set)
            self.tree.pack(side='left', fill='both', expand=True)
            sb.pack(side='right', fill='y')
            self.tree.bind('<<TreeviewSelect>>', self.on_select)
            ttk.Button(left, text='Export ALL models + collision (OBJ + PNG)…', command=self.do_export_all).pack(fill='x', pady=(4, 0))

            right = ttk.Frame(body)
            body.add(right, weight=3)
            ir = ttk.Frame(right)
            ir.pack(fill='x', pady=(0, 4))
            self.info = tk.StringVar(value='Choose a ROM and click Unpack, or pick an already-unpacked folder.')
            ttk.Label(ir, textvariable=self.info, wraplength=640, justify='left').pack(side='left', anchor='w')
            self.btn_revert = ttk.Button(ir, text='Revert file to original', command=self.do_revert, state='disabled')
            self.btn_revert.pack(side='right')
            self.nb = ttk.Notebook(right)
            self.nb.pack(fill='both', expand=True)

            # ---- model tab
            mt = ttk.Frame(self.nb)
            self.nb.add(mt, text='Model')
            bar = ttk.Frame(mt)
            bar.pack(fill='x')
            self.solid = tk.BooleanVar(value=True)
            ttk.Checkbutton(bar, text='Solid', variable=self.solid, command=self.redraw).pack(side='left')
            ttk.Button(bar, text='Reset view', command=self.reset_view).pack(side='left', padx=6)
            ttk.Button(bar, text='Replace with any OBJ…', command=self.do_replace_obj).pack(side='right')
            ttk.Button(bar, text='Export OBJ…', command=self.do_export_one).pack(side='right')
            ttk.Label(bar, text='drag = rotate, wheel = zoom', foreground='gray').pack(side='left', padx=8)
            cb = ttk.Frame(mt)
            cb.pack(fill='x', pady=(3, 0))
            ttk.Button(cb, text='Export collision OBJ…', command=self.do_export_collision).pack(side='left')
            ttk.Button(cb, text='Generate collision from OBJ…',
                       command=self.do_generate_collision).pack(side='left', padx=4)
            ttk.Label(cb, text='(.CVHM files - collision geometry lives in level space)',
                      foreground='gray').pack(side='left', padx=8)
            self.canvas = tk.Canvas(mt, background='#202428', highlightthickness=0)
            self.canvas.pack(fill='both', expand=True)
            self.canvas.bind('<ButtonPress-1>', self.on_press)
            self.canvas.bind('<B1-Motion>', self.on_drag)
            self.canvas.bind('<ButtonRelease-1>', self.on_release)
            self.canvas.bind('<MouseWheel>', self.on_wheel)
            self.canvas.bind('<Button-4>', lambda e: self.zoom_by(1.1))
            self.canvas.bind('<Button-5>', lambda e: self.zoom_by(1 / 1.1))
            self.canvas.bind('<Configure>', lambda e: self.redraw())

            # ---- texture tab
            tt = ttk.Frame(self.nb)
            self.nb.add(tt, text='Textures')
            tl = ttk.Frame(tt)
            tl.pack(side='left', fill='y')
            self.texlist = tk.Listbox(tl, width=38, exportselection=False)
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
            self.tex_canvas = tk.Canvas(tr, background='#303030', highlightthickness=0)
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
            self.text = tk.Text(txtf, wrap='word', font=('Courier', 11), undo=True)
            xsb = ttk.Scrollbar(txtf, command=self.text.yview)
            self.text.configure(yscrollcommand=xsb.set)
            self.text.pack(side='left', fill='both', expand=True)
            xsb.pack(side='right', fill='y')
            self.text.bind('<<Modified>>', self.on_text_modified)
            self.text.bind('<Control-s>', lambda e: (self.save_text(), 'break')[1])

            # ---- log
            lf = ttk.Frame(self, padding=(6, 4))
            lf.pack(fill='x')
            self.progress = ttk.Progressbar(lf, mode='indeterminate')
            self.progress.pack(fill='x')
            self.log_text = tk.Text(lf, height=7, state='disabled', wrap='word')
            self.log_text.pack(fill='x', pady=(4, 0))

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
            self.loaded = None
            self.collision = None
            self.view_tris = []
            self._last_tex = None
            self.canvas.delete('all')
            self.texlist.delete(0, 'end')
            self.tex_canvas.delete('all')
            self.pal_box['values'] = ()
            self.pal_var.set('')
            self.tex_info.set('')
            self._set_text('', False)
            self.btn_revert.configure(state='normal' if self._has_backup(path) else 'disabled')
            name = os.path.basename(path)
            if low.endswith('.txt'):
                try:
                    txt, info = read_text_doc(path)
                except Exception as e:
                    self.info.set('%s: could not read (%s)' % (name, e))
                    return
                self.text_path, self.text_info = path, info
                self._set_text(txt, True)
                self.text_label.set('%s  (%s line endings, %s) - editable'
                                    % (name, 'CRLF' if info['newline'] == '\r\n' else 'LF', info['encoding']))
                self.info.set('%s  -  text, %d lines, %d characters' % (name, txt.count('\n') + 1, len(txt)))
                self.nb.select(2)
                return
            if low.endswith('.cvhm'):
                try:
                    col = collision.parse(path)
                except Exception as e:
                    self.info.set('%s: could not parse collision (%s)' % (name, e))
                    return
                self.collision = col
                self.view_tris = col.triangles()
                self.view = bounds(self.view_tris)
                parts = ', '.join('%s: %d verts / %d tris' % (m.name, len(m.verts), len(m.tris))
                                  for m in col.meshes)
                self.info.set('%s  -  collision v%d, %d triangle(s)  -  %s'
                              % (name, col.version, len(self.view_tris), parts))
                self.nb.select(0)
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

        # ------------------------------------------------------------ model view
        def reset_view(self):
            self.yaw, self.pitch, self.zoom = 0.6, 0.35, 1.0
            self.redraw()

        def zoom_by(self, f):
            self.zoom = max(0.1, min(20, self.zoom * f))
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

        def redraw(self, fast=False):
            c = self.canvas
            c.delete('all')
            tris = self.view_tris
            if not tris:
                return
            w, h = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
            step = max(1, len(tris) // 2500) if fast else 1
            center, radius = self.view
            solid = self.solid.get() and len(tris) <= 12000
            for _d, pts, shade in project(tris, center, radius, self.yaw, self.pitch, self.zoom, w, h, step):
                if solid:
                    g = int(60 + 180 * min(1.0, shade))
                    c.create_polygon(pts, fill='#%02x%02x%02x' % (g, g, min(255, g + 12)), outline='')
                else:
                    c.create_polygon(pts, fill='', outline='#8fd0ff')

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

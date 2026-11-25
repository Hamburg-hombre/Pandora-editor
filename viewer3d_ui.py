"""Main window: Mario Kart Toolbox 1680 RC1 style layout (menu strip, tool strips, left tab control,
central 3D view, right property panel, status strip) with Pandora's colours and button look."""
import os, json, math, tempfile
import numpy as np
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QSize, QEvent
from PySide6.QtGui import QAction, QActionGroup, QColor, QPainter, QPen, QFont, QKeySequence, QBrush
from PySide6.QtWidgets import (QButtonGroup, QMainWindow, QWidget, QDockWidget, QTabWidget, QTreeWidget, QTreeWidgetItem,
                               QLineEdit, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QDoubleSpinBox,
                               QComboBox, QCheckBox, QPushButton, QToolBar, QToolButton, QMessageBox,
                               QDialog, QDialogButtonBox, QColorDialog, QInputDialog, QStatusBar, QApplication, QGroupBox,
                               QPlainTextEdit)

from viewer3d_gl import GLView, marker_kind, KIND_COLORS
import viewer3d_scene_data as sd
import viewer3d_paths as vpaths

PANDORA_QSS = '''
QToolButton, QPushButton { color:black; background:#f0f0f0; border:1px solid #7a7a7a; padding:2px 8px; min-height:18px; }
QToolButton:hover, QPushButton:hover { background:#e6eef8; }
QToolButton:pressed, QPushButton:pressed { background:#d4dfee; }
QToolButton:checked { background:#c8d8ee; border:1px solid #5a7aa8; }
QToolBar { spacing:3px; padding:2px; border-bottom:1px solid #b5b5b5; }
QTreeWidget::item:selected { background:#c8d8ee; color:black; }
QMenuBar, QMenu, QLabel, QCheckBox, QGroupBox, QTabBar, QDockWidget { color:black; }
QMenu { background:#f0f0f0; }
QMenu::item:selected { background:#c8d8ee; color:black; }
QDockWidget::title { background:#e4e4e4; padding:3px; border:1px solid #b5b5b5; }
'''


def apply_light_theme(app):
    """Pandora's look: light grey windows with black text, even when Windows is in dark mode."""
    from PySide6.QtGui import QPalette
    app.setStyle('Fusion')
    p = QPalette()
    P = QPalette
    for role, col in ((P.Window, '#f0f0f0'), (P.WindowText, '#000000'), (P.Base, '#ffffff'),
                      (P.AlternateBase, '#f5f5f5'), (P.Text, '#000000'), (P.Button, '#f0f0f0'),
                      (P.ButtonText, '#000000'), (P.Highlight, '#c8d8ee'), (P.HighlightedText, '#000000'),
                      (P.ToolTipBase, '#ffffe0'), (P.ToolTipText, '#000000'), (P.PlaceholderText, '#808080')):
        p.setColor(role, QColor(col))
    for role in (P.WindowText, P.Text, P.ButtonText):
        p.setColor(P.Disabled, role, QColor('#808080'))
    app.setPalette(p)


class DragSpin(QDoubleSpinBox):
    """Number field you can also scrub: press on the number and drag left/right to change it live.
    Shift = fine (x0.1), Ctrl = coarse (x10). A plain click still edits the text."""
    PIXELS = 4

    def __init__(self):
        super().__init__()
        self._d = None
        self.lineEdit().installEventFilter(self)
        self.lineEdit().setCursor(Qt.SizeHorCursor)

    def eventFilter(self, obj, e):
        if obj is self.lineEdit():
            t = e.type()
            if t == QEvent.MouseButtonPress and e.button() == Qt.LeftButton:
                self._d = dict(x=e.globalPosition().x(), v=self.value(), on=False)
                return True                         # no text selection while dragging
            if t == QEvent.MouseMove and self._d is not None and (e.buttons() & Qt.LeftButton):
                dx = e.globalPosition().x() - self._d['x']
                if not self._d['on'] and abs(dx) >= self.PIXELS:
                    self._d['on'] = True
                    self.lineEdit().clearFocus()
                    self.lineEdit().deselect()
                if self._d['on']:
                    m = e.modifiers()
                    k = 0.1 if (m & Qt.ShiftModifier) else (10.0 if (m & Qt.ControlModifier) else 1.0)
                    self.setValue(self._d['v'] + dx * self.singleStep() * k)     # emits valueChanged live
                return True
            if t == QEvent.MouseButtonRelease and e.button() == Qt.LeftButton and self._d is not None:
                was = self._d['on']
                self._d = None
                if not was:                          # plain click: edit the text
                    self.lineEdit().setFocus(Qt.MouseFocusReason)
                    self.lineEdit().selectAll()
                return True
        return super().eventFilter(obj, e)


class ViewCubeNet(QWidget):
    """Unwrapped-cube gizmo (cross layout). Click a labelled face to snap the camera to that view."""
    # Top face in the middle; Back / Front / Left / Right fold down from its edges, Bottom hangs off Front
    CELLS = {'Back': (1, 0), 'Left': (0, 1), 'Top': (1, 1), 'Right': (2, 1), 'Front': (1, 2), 'Bottom': (1, 3)}
    TINT = {'Top': '#7fb0ff', 'Bottom': '#7fb0ff', 'Front': '#ff9a9a', 'Back': '#ff9a9a',
            'Left': '#98e098', 'Right': '#98e098'}

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.cell = 34
        self.setFixedSize(self.cell * 3 + 2, self.cell * 4 + 2)
        self.setCursor(Qt.PointingHandCursor)
        view.cameraChanged.connect(self.update)

    def reposition(self):
        self.move(self.view.width() - self.width() - 10, 10)

    def _rect(self, name):
        cx, cy = self.CELLS[name]
        return QRectF(1 + cx * self.cell, 1 + cy * self.cell, self.cell, self.cell)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cur = self.view.cam.nearest_view_name()
        f = QFont('Segoe UI', 8)
        p.setFont(f)
        for n in self.CELLS:
            r = self._rect(n)
            c = QColor(self.TINT[n])
            c.setAlpha(235 if n == cur else 150)
            p.setBrush(QBrush(c))
            p.setPen(QPen(QColor('#202428' if n == cur else '#606060'), 2 if n == cur else 1))
            p.drawRect(r)
            p.setPen(QColor('#101010'))
            p.drawText(r, Qt.AlignCenter, n)

    def mousePressEvent(self, e):
        for n in self.CELLS:
            if self._rect(n).contains(e.position()):
                self.view.snap_view(n)
                return


class LabelOverlay(QWidget):
    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        view.cameraChanged.connect(self.update)

    def paintEvent(self, _e):
        v = self.view
        lv = v.level
        if not lv or not v.opt['labels'] or not v.show_type['lp'] or not v.show_files.get(lv.lp_path, True):
            return
        p = QPainter(self)
        p.setFont(QFont('Segoe UI', 8))
        w, h = v.width(), v.height()
        cam = v.cam
        reach = max(cam.scene_size * 0.12, 20.0)
        drawn = 0
        for o in lv.objects:
            if not o.visible:
                continue
            pos = o.pos
            if np.linalg.norm(pos - cam.pos) > reach and o.idx != v.selected:
                continue
            x, y, ok = cam.project(pos + np.array([0, 0.5, 0]), w, h)
            if not ok or not (0 <= x <= w and 0 <= y <= h):
                continue
            col = KIND_COLORS.get(marker_kind(o), (1, 1, 1))
            p.setPen(QColor.fromRgbF(*col))
            p.drawText(QPointF(x + 4, y), o.name if o.idx != v.selected else '[%s]' % o.name)
            drawn += 1
            if drawn > 150:
                break


class EnvDialog(QDialog):
    def __init__(self, parent, env, opt):
        super().__init__(parent)
        self.setWindowTitle('Environment')
        self.env, self.opt = env, opt
        form = QFormLayout(self)
        self.btn_clear = self._color_btn(env.clear)
        form.addRow('Background / sky colour', self.btn_clear)
        self.amb = self._spin(env.ambient, 0, 2, 0.05)
        self.lcol = self._spin(env.light_col, 0, 2, 0.05)
        form.addRow('Ambient light', self.amb)
        form.addRow('Directional light', self.lcol)
        self.ld = [self._spin(v, -1, 1, 0.05) for v in env.light_dir]
        row = QHBoxLayout()
        for s in self.ld:
            row.addWidget(s)
        form.addRow('Light direction x/y/z', row)
        self.fog = QCheckBox('Fog')
        self.fog.setChecked(bool(opt['fog']))
        form.addRow(self.fog)
        self.btn_fog = self._color_btn(env.fog_col)
        form.addRow('Fog colour', self.btn_fog)
        self.fn = self._spin(env.fog_near, 0, 100000, 5)
        self.ff = self._spin(env.fog_far, 1, 100000, 10)
        form.addRow('Fog near', self.fn)
        form.addRow('Fog far', self.ff)
        note = QLabel('The in-game sky / fog / light values are not decoded yet, so these are manual\n'
                      '(they are saved per work folder). Lighting is switched on in View > Lighting.')
        note.setStyleSheet('color:#606060')
        form.addRow(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    @staticmethod
    def _spin(v, lo, hi, step):
        s = QDoubleSpinBox()
        s.setRange(lo, hi); s.setSingleStep(step); s.setDecimals(3); s.setValue(float(v))
        return s

    def _color_btn(self, rgb):
        b = QPushButton()
        b._rgb = tuple(rgb)
        self._paint(b)
        b.clicked.connect(lambda: self._pick(b))
        return b

    @staticmethod
    def _paint(b):
        c = QColor.fromRgbF(*b._rgb)
        b.setStyleSheet('background:%s; min-width:60px; border:1px solid #606060;' % c.name())

    def _pick(self, b):
        c = QColorDialog.getColor(QColor.fromRgbF(*b._rgb), self)
        if c.isValid():
            b._rgb = (c.redF(), c.greenF(), c.blueF())
            self._paint(b)

    def apply(self):
        e = self.env
        e.clear, e.fog_col = self.btn_clear._rgb, self.btn_fog._rgb
        e.ambient, e.light_col = self.amb.value(), self.lcol.value()
        e.light_dir = tuple(s.value() for s in self.ld)
        e.fog_near, e.fog_far = self.fn.value(), self.ff.value()
        self.opt['fog'] = self.fog.isChecked()


HELP = '''Camera
  Right-drag           rotate around the camera's own centre
  Alt + right-drag     orbit around the point in front of the camera
  Middle-drag          pan (Shift = default pan speed)
  Scroll               move along the view direction (Shift = faster; zoom in orthographic view)
  F / numpad .         focus the selection        Home      frame everything
  Numpad 5             perspective / orthographic
  Numpad 1 / 3 / 7     front / right / top  (Ctrl = back / left / bottom)
  Numpad 2 4 6 8       step-orbit 15 degrees
  Cube net (top right) click a face to snap to that view

Objects
  Left-click           select a placement           Esc       deselect
  Q / W / E            select / move / rotate tool; drag a coloured axis (Ctrl = snap to 1 unit / 22.5 degrees)
  Ctrl+Z               undo                         Ctrl+S    save Objects.LP
'''


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('GoldenEye: Rogue Agent - 3D level viewer')
        self.resize(1400, 860)
        self.work = None
        self.level_name = None
        self.model_cache = {}
        self.undo = []
        self._last_undo = (None, 0.0)
        self._updating = False

        self.view = GLView()
        self.view.attach_model_cache(self.model_cache)
        self.setCentralWidget(self.view)
        self.cube = ViewCubeNet(self.view)
        self.labels = LabelOverlay(self.view)
        self.view.cameraChanged.connect(self._status)
        self.view.objectSelected.connect(self._on_selected)
        self.view.objectEdited.connect(self._on_edited)
        self.view.editBegan.connect(self._push_undo)
        self.view.orthoChanged.connect(self._sync_ortho)
        self.view.status.connect(lambda m: self.sb.showMessage(m, 6000))
        self._build_menus()
        self._build_toolbars()
        self._build_left()
        self._build_props()
        self.pos_label = QLabel('')
        self.sb = QStatusBar()
        self.setStatusBar(self.sb)
        self.sb.addPermanentWidget(self.pos_label)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._status)
        self.timer.start(500)
        self.view.resized.connect(self._view_resized)

    def _view_resized(self):
        self.cube.reposition()
        self.labels.setGeometry(0, 0, self.view.width(), self.view.height())

    # ------------------------------------------------------------------ menus / toolbars
    def _act(self, text, slot=None, shortcut=None, checkable=False, checked=False):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.setCheckable(checkable)
        if checkable:
            a.setChecked(checked)
        if slot:
            (a.toggled if checkable else a.triggered).connect(slot)
        return a

    def _build_menus(self):
        mb = self.menuBar()
        f = mb.addMenu('&File')
        self.menu_levels = f.addMenu('Open level')
        self.act_save = self._act('Save Objects.LP', self.save, 'Ctrl+S')
        f.addAction(self.act_save)
        f.addAction(self._act('Reload level', self.reload))
        f.addSeparator()
        f.addAction(self._act('Close', self.close))
        e = mb.addMenu('&Edit')
        e.addAction(self._act('Undo', self.do_undo, 'Ctrl+Z'))
        e.addAction(self._act('Select nothing', lambda: self.view.select(-1)))
        e.addSeparator()
        for key, text, sc in (('select', 'Select tool', 'Q'), ('move', 'Move tool', 'W'), ('rotate', 'Rotate tool', 'E')):
            e.addAction(self._act(text, lambda _=False, k=key: self.tool_btns[k].setChecked(True), sc))
        v = mb.addMenu('&View')
        self.opt_actions = {}
        for key, text in (('textures', 'Textures'), ('vcolors', 'Vertex colours'), ('lighting', 'Lighting'),
                          ('translucent', 'Translucent polygons'), ('wireframe', 'Wireframe models'),
                          ('cull', 'Back-face culling (as in-game)'), ('sky', 'Sky'), ('fog', 'Fog'),
                          ('markers', 'LP markers / paths'), ('labels', 'LP labels'),
                          ('grid', 'Grid'), ('axes', 'Axes')):
            a = self._act(text, lambda on, k=key: self._set_opt(k, on), checkable=True, checked=self.view.opt[key])
            self.opt_actions[key] = a
            v.addAction(a)
        v.addSeparator()
        v.addAction(self._act('Environment...', self.env_dialog))
        v.addSeparator()
        v.addAction(self._act('Frame all', self.view.frame_all, 'Home'))
        v.addAction(self._act('Focus selected', self.view.focus_selected, 'F'))
        v.addAction(self._act('Go to player spawn', self.view.go_spawn))
        self.act_ortho = self._act('Orthographic', lambda on: self._ortho(on), checkable=True)
        v.addAction(self.act_ortho)
        vm = v.addMenu('Snap view')
        for n in ('Front', 'Back', 'Left', 'Right', 'Top', 'Bottom'):
            vm.addAction(self._act(n, lambda _=False, n=n: self.view.snap_view(n)))
        t = mb.addMenu('&Tools')
        em = t.addMenu('Euler order (LP rotation)')
        grp = QActionGroup(self)
        for o in ('YXZ', 'YZX', 'XYZ', 'XZY', 'ZXY', 'ZYX'):
            a = self._act(o, lambda on, o=o: on and self._euler(o), checkable=True, checked=(o == 'YXZ'))
            grp.addAction(a); em.addAction(a)
        fm = t.addMenu('Flip rotation direction')
        self.flip_acts = []
        for i, ax in enumerate('XYZ'):
            a = self._act('Flip %s rotation' % ax, lambda on, i=i: self._flip(i, on), checkable=True,
                          checked=(self.view.euler_sign[i] < 0))
            self.flip_acts.append(a)
            fm.addAction(a)
        t.addAction(self._act('Facing offset for arrows / spawn camera...', self._facing_dialog))
        h = mb.addMenu('&Help')
        h.addAction(self._act('Controls...', lambda: QMessageBox.information(self, 'Controls', HELP)))

    def _tool_btn(self, text, slot, checkable=False, tip=None):
        b = QToolButton()
        b.setText(text)
        b.setCheckable(checkable)
        b.setToolTip(tip or text)
        (b.toggled if checkable else b.clicked).connect(slot)
        return b

    def _build_toolbars(self):
        tb = QToolBar('Tools')
        tb.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, tb)
        self.tool_btns = {}
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        for key, text, tip in (('select', 'Select', 'Click to select  (Q)'), ('move', 'Move', 'Drag an axis to move  (W)'),
                               ('rotate', 'Rotate', 'Drag an axis ring to rotate  (E)')):
            b = self._tool_btn(text, lambda on, k=key: on and self._set_tool(k), True, tip)
            self.tool_group.addButton(b)
            self.tool_btns[key] = b
            tb.addWidget(b)
        self.tool_btns['select'].setChecked(True)
        tb.addSeparator()
        tb.addWidget(self._tool_btn('Frame all', self.view.frame_all, tip='Home'))
        tb.addWidget(self._tool_btn('Focus selected', self.view.focus_selected, tip='F'))
        tb.addWidget(self._tool_btn('Player spawn', self.view.go_spawn))
        self.btn_ortho = self._tool_btn('Ortho', self._ortho, True, 'Numpad 5')
        tb.addWidget(self.btn_ortho)
        tb.addSeparator()
        tb.addWidget(self._tool_btn('Save LP', self.save, tip='Ctrl+S'))
        self.addToolBarBreak(Qt.TopToolBarArea)
        t2 = QToolBar('Show')
        t2.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, t2)
        t2.addWidget(QLabel(' Show: '))
        self.type_btns = {}
        tips = {'nsbmd': 'Room / sky models (.nsbmd)', 'cvhm': 'Collision wireframe (.cvhm)',
                'roo': 'Room boxes (.roo)', 'lp': 'Placements, paths and their models (.lp)',
                'nsbtx': 'Textures (.nsbtx) - off = models drawn untextured'}
        for t in sd.FILE_TYPES:
            b = self._tool_btn('.' + t, lambda on, t=t: self._set_type(t, on), True, tips[t])
            b.setChecked(True)
            self.type_btns[t] = b
            t2.addWidget(b)
        t2.addSeparator()
        t2.addWidget(QLabel(' Pan speed: '))
        self.pan_spin = DragSpin()
        self.pan_spin.setRange(0.001, 20.0)
        self.pan_spin.setDecimals(3)
        self.pan_spin.setSingleStep(0.005)
        self.pan_spin.setValue(self.view.cam.pan_speed)
        self.pan_spin.setSuffix(' u/px')
        self.pan_spin.setToolTip('Pan (middle-drag) speed in level units per pixel, constant. Hold Shift while dragging for the default (0.05).')
        self.pan_spin.valueChanged.connect(self._pan_speed_changed)
        t2.addWidget(self.pan_spin)

    # ------------------------------------------------------------------ docks
    def _build_left(self):
        dock = QDockWidget('Level', self)
        dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        tabs = QTabWidget()
        self.files_tree = QTreeWidget()
        self.files_tree.setHeaderLabels(['File', 'Info'])
        self.files_tree.itemChanged.connect(self._file_item_changed)
        tabs.addTab(self.files_tree, 'Files')
        ow = QWidget()
        lay = QVBoxLayout(ow)
        lay.setContentsMargins(2, 2, 2, 2)
        self.obj_filter = QLineEdit()
        self.obj_filter.setPlaceholderText('filter objects (name, kind, model)')
        self.obj_filter.textChanged.connect(self._fill_objects)
        lay.addWidget(self.obj_filter)
        self.obj_tree = QTreeWidget()
        self.obj_tree.setHeaderLabels(['Name', 'Kind', 'Model'])
        self.obj_tree.itemSelectionChanged.connect(self._obj_list_selected)
        self.obj_tree.itemChanged.connect(self._obj_item_changed)
        lay.addWidget(self.obj_tree)
        tabs.addTab(ow, 'Objects')
        tabs.setCurrentIndex(1)                      # Objects list open by default
        dock.setWidget(tabs)
        dock.setMinimumWidth(300)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

    def _build_props(self):
        dock = QDockWidget('Properties', self)
        dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        w = QWidget()
        lay = QVBoxLayout(w)
        self.p_name = QLabel('-')
        self.p_info = QLabel('')
        self.p_info.setWordWrap(True)
        self.p_info.setStyleSheet('color:#606060')
        lay.addWidget(self.p_name)
        lay.addWidget(self.p_info)
        gb = QGroupBox('Transform')
        form = QFormLayout(gb)
        self.pos_spin = [self._spin(-1e6, 1e6, 0.05, 3) for _ in range(3)]      # drag the number to scrub
        self.rot_spin = [self._spin(-360, 360, 1.0, 2) for _ in range(3)]
        for lbl, sp in zip('XYZ', self.pos_spin):
            form.addRow('Position ' + lbl, sp)
            sp.valueChanged.connect(self._pos_edited)
        for lbl, sp in zip('XYZ', self.rot_spin):
            form.addRow('Rotation ' + lbl + ' (deg)', sp)
            sp.valueChanged.connect(self._rot_edited)
        self.p_brads = QLabel('')
        self.p_brads.setStyleSheet('color:#606060')
        form.addRow('Raw (4096/turn)', self.p_brads)
        lay.addWidget(gb)
        mg = QGroupBox('Model')
        mf = QFormLayout(mg)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        mf.addRow('Shows', self.model_combo)
        self.p_how = QLabel('')
        mf.addRow('Found by', self.p_how)
        b1 = QPushButton('Use for this name')
        b2 = QPushButton('Use for this template (all placements sharing it)')
        b3 = QPushButton('Clear my override')
        b1.clicked.connect(lambda: self._assign_model('name'))
        b2.clicked.connect(lambda: self._assign_model('w1'))
        b3.clicked.connect(self._clear_override)
        for b in (b1, b2, b3):
            mf.addRow(b)
        lay.addWidget(mg)
        lay.addStretch(1)
        dock.setWidget(w)
        dock.setMinimumWidth(300)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self._set_props_enabled(False)

    @staticmethod
    def _spin(lo, hi, step, dec):
        s = DragSpin()
        s.setRange(lo, hi); s.setSingleStep(step); s.setDecimals(dec)
        s.setKeyboardTracking(False)
        return s

    def _set_props_enabled(self, on):
        for s in self.pos_spin + self.rot_spin:
            s.setEnabled(on)
        self.model_combo.setEnabled(on)

    # ------------------------------------------------------------------ options
    def _set_opt(self, k, on):
        self.view.opt[k] = bool(on)
        self.view.markers_dirty = True
        self.view.update()

    def _set_type(self, t, on):
        self.view.show_type[t] = bool(on)
        if t == 'nsbtx':
            self.view.opt['textures'] = bool(on)
            self.opt_actions['textures'].blockSignals(True)
            self.opt_actions['textures'].setChecked(bool(on))
            self.opt_actions['textures'].blockSignals(False)
        self.view.markers_dirty = True
        self.view.update()

    def _set_tool(self, k):
        self.view.tool = k
        for key, b in self.tool_btns.items():
            b.blockSignals(True)
            b.setChecked(key == k)
            b.blockSignals(False)
        if hasattr(self, 'sb'):
            self.sb.showMessage('%s tool%s' % (k.capitalize(), '' if k == 'select' else ' - select a placement, then drag a coloured axis'), 5000)
        self.view.update()

    def _sync_ortho(self, on):
        for w in (self.btn_ortho, self.act_ortho):
            w.blockSignals(True); w.setChecked(bool(on)); w.blockSignals(False)

    def _ortho(self, on):
        if bool(on) != self.view.cam.is_ortho:
            self.view.toggle_ortho()
        for w in (self.btn_ortho, self.act_ortho):
            w.blockSignals(True); w.setChecked(self.view.cam.is_ortho); w.blockSignals(False)

    def _euler(self, order=None, sign=None):
        self.view.set_euler(order or self.view.euler_order, sign if sign is not None else self.view.euler_sign)

    def _flip(self, axis, on):
        s = list(self.view.euler_sign)
        s[axis] = -1.0 if on else 1.0
        self._euler(None, tuple(s))

    def _pan_speed_changed(self, v):
        self.view.cam.pan_speed = float(v)
        self._save_env()

    def _facing_dialog(self):
        v, ok = QInputDialog.getDouble(self, 'Facing offset', 'Degrees added to the Y angle when drawing facing\n'
                                       'arrows and placing the camera at the player spawn:',
                                       self.view.facing_offset, -360.0, 360.0, 1)
        if ok:
            self.view.set_facing_offset(v)
            self._save_env()

    def env_dialog(self):
        d = EnvDialog(self, self.view.env, self.view.opt)
        if d.exec():
            d.apply()
            self.opt_actions['fog'].blockSignals(True)
            self.opt_actions['fog'].setChecked(self.view.opt['fog'])
            self.opt_actions['fog'].blockSignals(False)
            self._save_env()
            self.view.update()

    def _env_path(self, for_save=False):
        if not self.work:
            return None
        if for_save:
            return vpaths.save_path('_viewer3d_env.json')
        return vpaths.read_path('_viewer3d_env.json', self.work)

    def _save_env(self):
        p = self._env_path(for_save=True)
        if p:
            try:
                d = self.view.env.to_dict()
                d['pan_speed'] = self.view.cam.pan_speed
                d['facing_offset'] = self.view.facing_offset
                json.dump(d, open(p, 'w'), indent=1)
            except OSError:
                pass

    # ------------------------------------------------------------------ level loading
    def open_level(self, work, name):
        if self.level_name and self.view.level and any(o.dirty for o in self.view.level.objects):
            r = QMessageBox.question(self, 'Unsaved changes', 'Save the edited placements of %s first?' % self.level_name,
                                     QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return
            if r == QMessageBox.Save:
                self.save()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if work != self.work:
                self.model_cache.clear()
                self.work = work
                p = self._env_path()
                if p and os.path.isfile(p):
                    try:
                        j = json.load(open(p))
                        self.view.env.from_dict(j)
                        if 'facing_offset' in j:
                            self.view.facing_offset = float(j['facing_offset'])
                        if 'pan_speed' in j:
                            self.view.cam.pan_speed = float(j['pan_speed'])
                            self.pan_spin.blockSignals(True)
                            self.pan_spin.setValue(self.view.cam.pan_speed)
                            self.pan_spin.blockSignals(False)
                    except Exception:
                        pass
                self._fill_level_menu()
            lv = sd.Level.load(work, name, self.model_cache)
            self.view.attach_model_cache(self.model_cache)
            self.level_name = name
            self.undo.clear()
            self.view.set_level(lv)
            self._fill_files()
            self._fill_objects()
            self._on_selected(-1)
            self._update_title()
            msg = 'Loaded %s' % name
            if lv.warnings:
                msg += '  (%d warning(s): %s)' % (len(lv.warnings), lv.warnings[0])
            self.sb.showMessage(msg, 8000)
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, 'Could not load level', '%s: %s' % (type(e).__name__, e))
        finally:
            QApplication.restoreOverrideCursor()
        self.cube.reposition()
        self.raise_()
        self.activateWindow()

    def reload(self):
        if self.work and self.level_name:
            for o in (self.view.level.objects if self.view.level else []):
                o.dirty = False
            self.model_cache.clear()
            self.open_level(self.work, self.level_name)

    def _fill_level_menu(self):
        self.menu_levels.clear()
        for n in sd.list_levels(self.work):
            self.menu_levels.addAction(self._act(n, lambda _=False, n=n: self.open_level(self.work, n)))

    def _update_title(self):
        dirty = self.view.level and any(o.dirty for o in self.view.level.objects)
        self.setWindowTitle('%s%s - GoldenEye: Rogue Agent 3D viewer' % (self.level_name or '', ' *' if dirty else ''))

    # ------------------------------------------------------------------ left panels
    def _fill_files(self):
        t = self.files_tree
        t.blockSignals(True)
        t.clear()
        lv = self.view.level
        names = {'nsbmd': 'Models (.nsbmd)', 'cvhm': 'Collision (.cvhm)', 'roo': 'Room boxes (.roo)',
                 'lp': 'Placements (.lp)', 'nsbtx': 'Textures (.nsbtx)'}
        for kind in sd.FILE_TYPES:
            files = [f for f in lv.files if f.kind == kind]
            top = QTreeWidgetItem([names[kind], '%d' % len(files)])
            top.setFlags(top.flags() | Qt.ItemIsUserCheckable)
            top.setCheckState(0, Qt.Checked if self.view.show_type[kind] else Qt.Unchecked)
            top.setData(0, Qt.UserRole, ('type', kind))
            t.addTopLevelItem(top)
            for f in files:
                it = QTreeWidgetItem([f.label, f.info])
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Checked)
                it.setData(0, Qt.UserRole, ('file', f.path, kind))
                top.addChild(it)
            top.setExpanded(kind in ('nsbmd', 'lp', 'roo'))
        t.resizeColumnToContents(0)
        t.blockSignals(False)

    def _file_item_changed(self, it, _col):
        d = it.data(0, Qt.UserRole)
        on = it.checkState(0) == Qt.Checked
        if d[0] == 'type':
            self.type_btns[d[1]].setChecked(on)         # -> _set_type
        else:
            self.view.show_files[d[1]] = on
            self.view.markers_dirty = True
            self.view.update()

    def _fill_objects(self):
        t = self.obj_tree
        t.blockSignals(True)
        t.clear()
        lv = self.view.level
        flt = self.obj_filter.text().strip().lower()
        for o in lv.objects:
            row = [o.name, o.kind, o.model or '']
            if flt and not any(flt in r.lower() for r in row):
                continue
            it = QTreeWidgetItem(row)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked if o.visible else Qt.Unchecked)
            it.setData(0, Qt.UserRole, o.idx)
            t.addTopLevelItem(it)
        t.resizeColumnToContents(0)
        t.blockSignals(False)

    def _obj_item_changed(self, it, _c):
        idx = it.data(0, Qt.UserRole)
        self.view.level.objects[idx].visible = it.checkState(0) == Qt.Checked
        self.view.markers_dirty = True
        self.view.update()

    def _obj_list_selected(self):
        if self._updating:
            return
        its = self.obj_tree.selectedItems()
        if its:
            self.view.select(its[0].data(0, Qt.UserRole))

    # ------------------------------------------------------------------ properties
    def _on_selected(self, idx):
        self._updating = True
        try:
            lv = self.view.level
            o = lv.objects[idx] if lv and 0 <= idx < len(lv.objects) else None
            self._set_props_enabled(o is not None)
            if o is None:
                self.p_name.setText('Nothing selected')
                self.p_info.setText('')
                self.p_how.setText('')
                self.obj_tree.clearSelection()
                return
            self.p_name.setText('<b>%s</b>' % o.name)
            self.p_info.setText('%s placement #%d, template %08X\nsource: %s' % (
                o.kind, o.idx, o.rec.w1, os.path.basename(lv.lp_path)))
            self._refresh_transform(o)
            stems = sorted(lv.resolver.index.keys()) if lv.resolver else []
            self.model_combo.clear()
            self.model_combo.addItems([''] + stems)
            self.model_combo.setCurrentText(o.model or '')
            self.p_how.setText({'override': 'your override', 'builtin': 'built-in template map',
                                'ai-name': 'AI spawn name (inferred)',
                                'name': 'CHR name', 'template': 'shared template',
                                None: 'not found (marker shown)'}[o.how])
            for i in range(self.obj_tree.topLevelItemCount()):
                it = self.obj_tree.topLevelItem(i)
                if it.data(0, Qt.UserRole) == idx:
                    self.obj_tree.setCurrentItem(it)
                    break
        finally:
            self._updating = False

    def _refresh_transform(self, o):
        self._updating = True
        for s, v in zip(self.pos_spin, o.pos):
            s.setValue(float(v))
        e = o.euler
        for s, v in zip(self.rot_spin, e):
            s.setValue(v * 360.0 / 4096.0)
        self.p_brads.setText('%d, %d, %d' % e)
        self._updating = False

    def _on_edited(self, idx):
        o = self.view.level.objects[idx]
        self._refresh_transform(o)
        self._update_title()

    def _push_undo(self, idx):
        import time
        o = self.view.level.objects[idx]
        now = time.time()
        if self._last_undo[0] == idx and now - self._last_undo[1] < 0.8:
            self._last_undo = (idx, now)
            return
        self.undo.append((idx, o.pos.copy(), tuple(o.euler)))
        self.undo = self.undo[-200:]
        self._last_undo = (idx, now)

    def _pos_edited(self):
        if self._updating:
            return
        o = self.view.sel_obj()
        if o is None:
            return
        self._push_undo(o.idx)
        o.set_pos([s.value() for s in self.pos_spin])
        self.view.object_changed(o.idx)
        self._update_title()

    def _rot_edited(self):
        if self._updating:
            return
        o = self.view.sel_obj()
        if o is None:
            return
        self._push_undo(o.idx)
        o.set_euler([s.value() * 4096.0 / 360.0 for s in self.rot_spin])
        self.p_brads.setText('%d, %d, %d' % o.euler)
        self.view.object_changed(o.idx)
        self._update_title()

    def do_undo(self):
        if not self.undo:
            return
        idx, pos, eul = self.undo.pop()
        o = self.view.level.objects[idx]
        o.set_pos(pos)
        o.rec.tail6 = __import__('struct').pack('<3h', *eul)
        self.view.object_changed(idx)
        if self.view.selected == idx:
            self._refresh_transform(o)
        self._update_title()

    def _assign_model(self, how):
        o = self.view.sel_obj()
        lv = self.view.level
        stem = self.model_combo.currentText().strip().lower()
        if o is None or not stem or stem not in lv.resolver.index:
            QMessageBox.information(self, 'Model', 'Pick a model name from the list first.')
            return
        if how == 'name':
            lv.resolver.by_name[o.name.lower()] = stem
        else:
            lv.resolver.by_w1['%08X' % o.rec.w1] = stem
        lv.resolver.save_overrides()
        for ob in lv.objects:
            ob.model, ob.how = lv.resolver.resolve(ob.rec.w1, ob.name, ob.rec.is_ai)
        self.view.ensure_models()
        self.view.object_changed()
        self._fill_objects()
        self._on_selected(o.idx)

    def _clear_override(self):
        o = self.view.sel_obj()
        lv = self.view.level
        if o is None:
            return
        lv.resolver.by_name.pop(o.name.lower(), None)
        lv.resolver.by_w1.pop('%08X' % o.rec.w1, None)
        lv.resolver.save_overrides()
        for ob in lv.objects:
            ob.model, ob.how = lv.resolver.resolve(ob.rec.w1, ob.name, ob.rec.is_ai)
        self.view.object_changed()
        self._fill_objects()
        self._on_selected(o.idx)

    # ------------------------------------------------------------------ misc
    def save(self):
        lv = self.view.level
        if not lv or not lv.lp:
            return
        try:
            lv.save_lp(lambda m: self.sb.showMessage(m, 8000))
            self.sb.showMessage('Saved Objects.LP - press the reload button on Pandora\'s .lp tab, then Repack ROM.', 10000)
        except Exception as e:
            QMessageBox.critical(self, 'Save failed', str(e))
        self._update_title()

    def _status(self):
        c = self.view.cam
        self.pos_label.setText('cam %.1f, %.1f, %.1f   %s   %.0f fps   %d tris   %s' % (
            c.pos[0], c.pos[1], c.pos[2], 'ortho' if c.is_ortho else 'persp %d\u00b0' % c.fov,
            self.view.fps, self.view.tri_drawn, self.view.tool))

    def closeEvent(self, e):
        lv = self.view.level
        if lv and any(o.dirty for o in lv.objects):
            r = QMessageBox.question(self, 'Unsaved changes', 'Save the edited placements first?',
                                     QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                e.ignore()
                return
            if r == QMessageBox.Save:
                self.save()
        e.accept()

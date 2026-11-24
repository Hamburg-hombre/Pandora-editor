"""Camera maths (numpy only): free look, orbit, pan, dolly, ortho + axis-view snapping."""
import math
import numpy as np

DEFAULT_PAN_SPEED = 0.05      # world units per pixel; Shift while right-dragging returns to this

VIEWS = {   # name -> (yaw, pitch) in degrees; forward = (sin y * cos p, sin p, -cos y * cos p)
    'Front':  (0.0, 0.0),        # looking toward -Z
    'Back':   (180.0, 0.0),
    'Right':  (-90.0, 0.0),      # looking toward -X (camera sits on +X)
    'Left':   (90.0, 0.0),
    'Top':    (0.0, -89.9),
    'Bottom': (0.0, 89.9),
}


def normalize(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


def look_at(eye, fwd, up=(0.0, 1.0, 0.0)):
    f = normalize(np.asarray(fwd, np.float64))
    r = normalize(np.cross(f, up))
    u = np.cross(r, f)
    m = np.eye(4)
    m[0, :3], m[1, :3], m[2, :3] = r, u, -f
    m[:3, 3] = -m[:3, :3] @ np.asarray(eye, np.float64)
    return m


def perspective(fov_deg, aspect, near, far):
    f = 1.0 / math.tan(math.radians(fov_deg) / 2.0)
    m = np.zeros((4, 4))
    m[0, 0], m[1, 1] = f / aspect, f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = 2 * far * near / (near - far)
    m[3, 2] = -1.0
    return m


def ortho(half_h, aspect, near, far):
    hw = half_h * aspect
    m = np.eye(4)
    m[0, 0], m[1, 1] = 1.0 / hw, 1.0 / half_h
    m[2, 2] = -2.0 / (far - near)
    m[2, 3] = -(far + near) / (far - near)
    return m


class Camera:
    def __init__(self):
        self.pos = np.array([0.0, 10.0, 30.0])
        self.yaw = 0.0               # radians
        self.pitch = -0.2
        self.fov = 60.0
        self.near, self.far = 0.1, 5000.0
        self.is_ortho = False
        self.ortho_half = 20.0       # half of the visible height in ortho mode
        self.pivot_dist = 30.0       # distance to the orbit pivot
        self.speed = 1.0             # world units per scroll notch (scaled to the scene)
        self.pan_speed = DEFAULT_PAN_SPEED        # world units per pixel of right-drag (user setting, constant)
        self.scene_size = 100.0

    # ---- basics
    def forward(self):
        cp = math.cos(self.pitch)
        return np.array([math.sin(self.yaw) * cp, math.sin(self.pitch), -math.cos(self.yaw) * cp])

    def right(self):
        return normalize(np.cross(self.forward(), (0, 1, 0)))

    def up(self):
        return np.cross(self.right(), self.forward())

    def pivot(self):
        return self.pos + self.forward() * self.pivot_dist

    def view(self):
        return look_at(self.pos, self.forward())

    def proj(self, aspect):
        if self.is_ortho:
            return ortho(self.ortho_half, aspect, -self.far, self.far)
        return perspective(self.fov, aspect, self.near, self.far)

    def set_scene(self, bmin, bmax):
        self.scene_size = max(float(np.linalg.norm(np.asarray(bmax) - np.asarray(bmin))), 1.0)
        self.speed = self.scene_size * 0.0025
        self.far = max(self.scene_size * 6.0, 500.0)
        self.near = max(self.scene_size * 0.0004, 0.02)

    # ---- the control scheme
    def look(self, dx, dy, sens=0.005):
        """Left-drag: rotate around the camera's own centre."""
        self.yaw += dx * sens
        self.pitch = max(-1.5533, min(1.5533, self.pitch - dy * sens))

    def orbit(self, dx, dy, sens=0.005):
        """Alt+left-drag: orbit around the pivot point in front of the camera (drag the scene)."""
        piv = self.pivot()
        self.yaw += dx * sens
        self.pitch = max(-1.5533, min(1.5533, self.pitch - dy * sens))
        self.pos = piv - self.forward() * self.pivot_dist

    def pivot_floor(self):
        """Smallest pivot distance used for pan speed, so fast scrolling can't make panning crawl."""
        return max(self.scene_size * 0.02, 0.5)

    def pan(self, dx, dy, view_h_px, speed=None):
        """Right-drag: slide the camera along its own right/up axes at a constant speed
        (world units per pixel = pan_speed). Orthographic view keeps the picture under the mouse."""
        if self.is_ortho:
            k = 2.0 * self.ortho_half / max(view_h_px, 1)
        else:
            k = speed if speed is not None else self.pan_speed
        self.pos = self.pos - self.right() * dx * k + self.up() * dy * k

    def dolly(self, notches, fast=False, ndc=None, aspect=1.0):
        """Scroll: move toward the point under the mouse (ndc = mouse in -1..1 screen coordinates).
        Shift = faster. In orthographic view it zooms, keeping the point under the mouse fixed."""
        x, y = ndc if ndc is not None else (0.0, 0.0)
        if self.is_ortho:
            old = self.ortho_half
            new = max(0.05, old * (0.9 ** (notches * (3.0 if fast else 1.0))))
            self.pos = self.pos + (self.right() * x * aspect + self.up() * y) * (old - new)
            self.ortho_half = new
            return
        f = self.forward()
        t = math.tan(math.radians(self.fov) / 2.0)
        d = normalize(f + self.right() * x * t * aspect + self.up() * y * t)
        step = self.speed * (5.0 if fast else 1.0) * notches
        self.pos = self.pos + d * step
        self.pivot_dist = max(self.pivot_floor(), self.pivot_dist - step * float(np.dot(d, f)))

    # ---- framing
    def frame(self, bmin, bmax):
        bmin, bmax = np.asarray(bmin, np.float64), np.asarray(bmax, np.float64)
        c = (bmin + bmax) / 2.0
        r = max(float(np.linalg.norm(bmax - bmin)) / 2.0, 0.5)
        dist = r / math.tan(math.radians(self.fov) / 2.0) * 1.1
        self.pos = c - self.forward() * dist
        self.pivot_dist = dist
        self.ortho_half = r * 1.05

    def focus_point(self, p, radius=2.0):
        self.frame(np.asarray(p) - radius, np.asarray(p) + radius)

    def snap(self, name):
        yaw, pitch = VIEWS[name]
        piv = self.pivot()
        self.yaw, self.pitch = math.radians(yaw), math.radians(pitch)
        self.pos = piv - self.forward() * self.pivot_dist

    def step_orbit(self, d_yaw_deg, d_pitch_deg):
        piv = self.pivot()
        self.yaw += math.radians(d_yaw_deg)
        self.pitch = max(-1.5533, min(1.5533, self.pitch + math.radians(d_pitch_deg)))
        self.pos = piv - self.forward() * self.pivot_dist

    def look_from(self, pos, yaw):
        """Place the camera at a point looking along `yaw` (used for the player spawn)."""
        self.yaw, self.pitch = yaw, -0.05
        self.pos = np.asarray(pos, np.float64) + np.array([0.0, 1.6, 0.0])
        self.pivot_dist = 5.0

    # ---- picking
    def ray(self, px, py, w, h):
        """World-space ray (origin, direction) through pixel (px, py)."""
        aspect = w / max(h, 1)
        x = (2.0 * px / w - 1.0)
        y = (1.0 - 2.0 * py / h)
        f, r, u = self.forward(), self.right(), self.up()
        if self.is_ortho:
            o = self.pos + r * x * self.ortho_half * aspect + u * y * self.ortho_half
            return o, f
        t = math.tan(math.radians(self.fov) / 2.0)
        d = normalize(f + r * x * t * aspect + u * y * t)
        return self.pos.copy(), d

    def project(self, p, w, h):
        """World point -> (px, py, depth_ok)."""
        aspect = w / max(h, 1)
        m = self.proj(aspect) @ self.view()
        v = m @ np.array([p[0], p[1], p[2], 1.0])
        if v[3] <= 1e-6 and not self.is_ortho:
            return 0.0, 0.0, False
        v = v / v[3] if v[3] != 0 else v
        return (v[0] * 0.5 + 0.5) * w, (1.0 - (v[1] * 0.5 + 0.5)) * h, abs(v[2]) <= 1.0

    def nearest_view_name(self):
        f = self.forward()
        best, bn = -2.0, 'Front'
        for n, (y, p) in VIEWS.items():
            yy, pp = math.radians(y), math.radians(p)
            v = np.array([math.sin(yy) * math.cos(pp), math.sin(pp), -math.cos(yy) * math.cos(pp)])
            d = float(np.dot(v, f))
            if d > best:
                best, bn = d, n
        return bn


def ray_box(o, d, bmin, bmax):
    """Slab test. Returns the entry distance or None."""
    t0, t1 = -1e30, 1e30
    for i in range(3):
        if abs(d[i]) < 1e-12:
            if o[i] < bmin[i] or o[i] > bmax[i]:
                return None
        else:
            a, b = (bmin[i] - o[i]) / d[i], (bmax[i] - o[i]) / d[i]
            if a > b:
                a, b = b, a
            t0, t1 = max(t0, a), min(t1, b)
            if t0 > t1:
                return None
    return t0 if t1 >= 0 else None


def closest_on_axis(o, d, p0, axis):
    """Parameter t along the line p0 + t*axis closest to the ray (o, d)."""
    w0 = o - p0
    a, b, c = float(np.dot(d, d)), float(np.dot(d, axis)), float(np.dot(axis, axis))
    dd, e = float(np.dot(d, w0)), float(np.dot(axis, w0))
    den = a * c - b * b
    if abs(den) < 1e-9:
        return 0.0
    return (a * e - b * dd) / den


def ray_triangles(o, d, tris):
    """Nearest hit distance of a ray against an (N,3,3) triangle array (two-sided), or None."""
    if len(tris) == 0:
        return None
    v0, v1, v2 = tris[:, 0], tris[:, 1], tris[:, 2]
    e1, e2 = v1 - v0, v2 - v0
    p = np.cross(d, e2)
    det = np.einsum('ij,ij->i', e1, p)
    ok = np.abs(det) > 1e-12
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    tv = o - v0
    u = np.einsum('ij,ij->i', tv, p) * inv
    q = np.cross(tv, e1)
    v = (q @ d) * inv
    t = np.einsum('ij,ij->i', e2, q) * inv
    hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-6)
    return float(t[hit].min()) if hit.any() else None

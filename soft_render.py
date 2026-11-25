#!/usr/bin/env python3
"""Small software textured rasterizer for the Tk preview canvas.

Tk's Canvas cannot draw textured polygons, so animation frames are rendered
into a PIL Image and shown as a PhotoImage.  Triangles are in the format
produced by anim_player.frame_triangles(): (verts, uvs, image).
"""
import math

import numpy as np
from PIL import Image


def render_textured(tris, center, radius, yaw, pitch, zoom, w, h,
                    bg=(32, 36, 40), step=1):
    """Return a PIL RGB Image of `tris` using the same camera as the old viewer.

    tris: list of (verts, uvs, image) with 3 world-space vertices, 3 UV pairs,
          and a PIL RGBA image (or None for a flat-coloured triangle).
    """
    w, h = max(1, int(w)), max(1, int(h))
    frame = np.zeros((h, w, 3), np.float32)
    frame[:] = np.array(bg, np.float32)
    zbuf = np.full((h, w), -1e30, np.float32)   # larger camera z = nearer

    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    scale = zoom * min(w, h) / (2.2 * radius)
    cx0, cy0, cz0 = center

    def project(v):
        x, y, z = v[0] - cx0, v[1] - cy0, v[2] - cz0
        x2 = x * cy + z * sy
        z2 = -x * sy + z * cy
        y2 = y * cp - z2 * sp
        z3 = y * sp + z2 * cp
        return w / 2 + x2 * scale, h / 2 - y2 * scale, z3

    # far -> near, matching the existing flat renderer's painter order
    prepared = []
    for i in range(0, len(tris), step):
        verts, uvs, image = tris[i]
        try:
            p = [project(v) for v in verts]
        except Exception:
            continue
        depth = (p[0][2] + p[1][2] + p[2][2]) / 3.0
        prepared.append((depth, p, uvs, image))
    prepared.sort(key=lambda t: t[0])           # far first, near last (larger z = nearer)

    cache = {}
    for _depth, p, uvs, image in prepared:
        xs = [q[0] for q in p]
        ys = [q[1] for q in p]
        x0 = max(0, int(math.floor(min(xs)))); x1 = min(w - 1, int(math.ceil(max(xs))))
        y0 = max(0, int(math.floor(min(ys)))); y1 = min(h - 1, int(math.ceil(max(ys))))
        if x1 < x0 or y1 < y0:
            continue
        ax, ay = xs[0], ys[0]
        bx, by = xs[1], ys[1]
        cx, cyv = xs[2], ys[2]
        den = (by - cyv) * (ax - cx) + (cx - bx) * (ay - cyv)
        if abs(den) < 1e-9:
            continue
        yy, xx = np.mgrid[y0:y1 + 1, x0:x1 + 1]
        w0 = ((by - cyv) * (xx - cx) + (cx - bx) * (yy - cyv)) / den
        w1 = ((cyv - ay) * (xx - cx) + (ax - cx) * (yy - cyv)) / den
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not inside.any():
            continue

        # affine interpolation in screen space (DS models are low-poly, and
        # this avoids perspective divides for vertices at zero camera depth)
        zpx = w0 * p[0][2] + w1 * p[1][2] + w2 * p[2][2]
        closer = inside & (zpx > zbuf[y0:y1 + 1, x0:x1 + 1])
        if not closer.any():
            continue

        if image is None:
            color = np.array((170, 190, 210), np.float32)
            region = frame[y0:y1 + 1, x0:x1 + 1]
            region[closer] = color
            zbuf[y0:y1 + 1, x0:x1 + 1][closer] = zpx[closer]
            continue

        key = id(image)
        arr = cache.get(key)
        if arr is None:
            rgba = image.convert('RGBA')
            arr = np.asarray(rgba, dtype=np.uint8)
            cache[key] = arr
        ih, iw = arr.shape[:2]
        if not iw or not ih:
            continue
        ua = w0 * uvs[0][0] + w1 * uvs[1][0] + w2 * uvs[2][0]
        va = w0 * uvs[0][1] + w1 * uvs[1][1] + w2 * uvs[2][1]
        iu = ua
        iv = va
        px = (np.floor(iu * iw).astype(np.int32) % iw)
        py = (np.floor(iv * ih).astype(np.int32) % ih)
        col = arr[py, px].astype(np.float32)
        alpha = col[..., 3:4] / 255.0
        region = frame[y0:y1 + 1, x0:x1 + 1]
        zr = zbuf[y0:y1 + 1, x0:x1 + 1]
        m = closer
        region[m] = region[m] * (1 - alpha[m]) + col[m, :3] * alpha[m]
        zr[m] = zpx[m]

    return Image.fromarray(np.clip(frame, 0, 255).astype(np.uint8), 'RGB')

#!/usr/bin/env python3
"""
scene.py - shared helpers for exporting, importing and previewing Rogue Agent models:
  * MatInfo       : texture / palette / wrap / UV-transform for one material
  * tile_image    : bakes mirrored-repeat textures into a 2x tile
"""
from PIL import Image
from nitro import *


def tile_image(im, flip_s, flip_t):
    """Bake mirrored repeat into the image. Returns (image, wmul, hmul)."""
    w, h = im.size
    wm, hm = (2 if flip_s else 1), (2 if flip_t else 1)
    if wm == 1 and hm == 1:
        return im, 1, 1
    out = Image.new('RGBA', (w * wm, h * hm))
    out.paste(im, (0, 0))
    if flip_s:
        out.paste(im.transpose(Image.FLIP_LEFT_RIGHT), (w, 0))
    if flip_t:
        out.paste(im.transpose(Image.FLIP_TOP_BOTTOM), (0, h))
    if flip_s and flip_t:
        out.paste(im.transpose(Image.ROTATE_180), (w, h))
    return out, wm, hm


class MatInfo:
    """Everything needed to map a material's texels to OBJ/UV space and back."""
    def __init__(self, base, mat, tb):
        self.mat = mat
        self.tb = tb
        self.tex = tb.tex_by_name.get(mat['tex']) if (tb and mat and mat['tex']) else None
        self.palname = None
        self.pal = []
        if self.tex is not None:
            pn = mat['pal'] if mat['pal'] in tb.palettes else next(iter(tb.palettes), None)
            self.palname = pn
            self.pal = tb.palettes.get(pn, [])
        wrap = mat['wrap'] if mat else 0
        self.repeat_s, self.repeat_t = bool(wrap & 1), bool(wrap & 2)
        self.fs, self.ft = bool(wrap & 4), bool(wrap & 8)
        self.wm, self.hm = (2 if self.fs else 1), (2 if self.ft else 1)
        self.sx, self.sy = mat['tm_scale'] if mat else (1.0, 1.0)
        self.tx, self.ty = mat['tm_trans'] if mat else (0.0, 0.0)
        self.mname = '%s_%s' % (base, mat['name'] if mat else 'none')
        self.png_name = None
        if self.tex is not None:
            self.png_name = '%s_%s__%s%s%s.png' % (base, self.tex.name, self.palname,
                                                    '_fs' if self.fs else '', '_ft' if self.ft else '')
        self._img = None

    def image(self):
        if self._img is None and self.tex is not None:
            im = Image.new('RGBA', (self.tex.w, self.tex.h))
            im.putdata(self.tex.decode(self.pal))
            self._img = tile_image(im, self.fs, self.ft)[0]
        return self._img

    # texel coords (s,t) <-> OBJ (u,v)
    def uv(self, s, t):
        if self.tex is None:
            return 0.0, 0.0
        u = (s / self.tex.w) * self.sx + self.tx
        tt = (t / self.tex.h) * self.sy + self.ty
        return u / self.wm, 1.0 - tt / self.hm

    def st(self, u, v):
        if self.tex is None:
            return 0.0, 0.0
        tt = (1.0 - v) * self.hm
        s = ((u * self.wm - self.tx) / (self.sx or 1.0)) * self.tex.w
        t = ((tt - self.ty) / (self.sy or 1.0)) * self.tex.h
        return s, t

    def period(self):
        """Texel distance after which the texture repeats (None if it doesn't repeat)."""
        if self.tex is None:
            return None, None
        return ((self.tex.w * self.wm / abs(self.sx or 1.0)) if self.repeat_s else None,
                (self.tex.h * self.hm / abs(self.sy or 1.0)) if self.repeat_t else None)



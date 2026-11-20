#!/usr/bin/env python3
"""
GoldenEye: Rogue Agent (DS) sound files for the Pandora editor.

The game keeps its audio in two files in Sounds/:

  Sound_*.sdat   a standard NITRO SDAT archive (SYMB / INFO / FAT / FILE).
                 It holds sequenced music (SSEQ), a sequence archive of sound
                 effects (SSAR), sound banks (SBNK) and wave archives (SWAR).
                 The samples inside a SWAR are IMA-ADPCM, 8/16 bit PCM.
  Sound_*.sd     a small table that maps a hash of a sound's name to the index
                 the game uses to request it: u32 hash, u16 index, u16 0xCCCC.
                 The names themselves live in the .sdat's SYMB block, and the
                 hash is the same java31 hash used everywhere else in the game.

What this module can do:

  * parse both files and pair a .sd with its .sdat
  * list every named sound (music and effects) with its sample, rate, length
  * decode samples to 16-bit PCM and write WAV (for listening / export)
  * render a simple SSEQ sequence so music can be listened to as well
  * replace a sample with a WAV (re-encodes to IMA-ADPCM, rewrites the SDAT)
  * rename a sound in SYMB and update its hash in the .sd
  * edit a .sd entry's hash / index

Nothing writes a file by itself: the GUI takes a backup first.
"""
import os
import re
import struct
import wave


class SoundError(Exception):
    pass


# ------------------------------------------------------------------ helpers
def u8(d, o):
    return d[o]


def u16(d, o):
    return struct.unpack_from('<H', d, o)[0]


def u32(d, o):
    return struct.unpack_from('<I', d, o)[0]


def java31(s):
    """The java31 hash used for every named resource in this game."""
    h = 0
    for c in s:
        h = (h * 31 + ord(c)) & 0xffffffff
    return h


# IMA-ADPCM step table (GBATEK, DS Sound Notes)
ADPCM_STEPS = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
    34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143,
    157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544,
    598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878,
    2066, 2273, 2500, 2750, 3025, 3328, 3660, 4026, 4428, 4871, 5358, 5894,
    6483, 7132, 7846, 8630, 9494, 10445, 11491, 12640, 13905, 15295, 16825,
    18507, 20357, 22392, 24632, 27095, 29805, 32784, 36062, 39669, 43638,
    48002, 52802, 58082, 63890, 70279, 77307, 85038,
]
INDEX_STEPS = [-1, -1, -1, -1, 2, 4, 6, 8]


def decode_adpcm(body, header, nsamples):
    """Decode NDS IMA-ADPCM. body starts after the 4-byte ADPCM header."""
    pcm = u16(header, 0)
    if pcm >= 0x8000:
        pcm -= 0x10000
    idx = u16(header, 2) & 0x7f
    out = [pcm]
    step = ADPCM_STEPS[min(idx, 88)]
    for i in range(max(0, nsamples - 1)):
        b = body[i >> 1]
        nib = (b >> (4 * (i & 1))) & 0x0f
        diff = step // 8
        if nib & 1:
            diff += step // 4
        if nib & 2:
            diff += step // 2
        if nib & 4:
            diff += step
        if nib & 8:
            pcm = max(pcm - diff, -0x7fff)
        else:
            pcm = min(pcm + diff, 0x7fff)
        idx = max(0, min(88, idx + INDEX_STEPS[nib & 7]))
        step = ADPCM_STEPS[idx]
        out.append(pcm)
    return out


def encode_adpcm(samples):
    """Encode mono 16-bit samples to NDS IMA-ADPCM: (header4, nibble bytes)."""
    samples = [max(-32767, min(32767, int(s))) for s in samples]
    if not samples:
        samples = [0]
    pcm, idx = samples[0], 16
    nibs = []
    for s in samples[1:]:
        step = ADPCM_STEPS[idx]
        diff = s - pcm
        nib = 0
        if diff < 0:
            nib |= 8
            diff = -diff
        best = 0
        for cand in range(8):
            d = (step // 8)
            if cand & 1:
                d += step // 4
            if cand & 2:
                d += step // 2
            if cand & 4:
                d += step
            if abs(d - diff) < abs(best - diff):
                best = d
                nib = (nib & 8) | cand
        delta = best if not (nib & 8) else -best
        pcm = max(-32767, min(32767, pcm + delta))
        idx = max(0, min(88, idx + INDEX_STEPS[nib & 7]))
        nibs.append(nib)
    out = bytearray((len(nibs) + 1) // 2)
    for i, n in enumerate(nibs):
        if i & 1:
            out[i >> 1] |= n << 4
        else:
            out[i >> 1] = n
    # pad to a multiple of 4 so the sample length field stays exact
    while len(out) % 4:
        out.append(0)
    return struct.pack('<HH', samples[0] & 0xffff, idx), bytes(out)


def pcm8_decode(data, n):
    return [b - 0x80 for b in data[:n]]


def pcm16_decode(data, n):
    return [struct.unpack_from('<h', data, i * 2)[0] for i in range(n // 2)]


def write_wav_bytes(samples, rate):
    """Build a mono 16-bit PCM WAV in memory."""
    import io
    bio = io.BytesIO()
    with wave.open(bio, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(b''.join(struct.pack('<h', max(-32768, min(32767, int(s))))
                               for s in samples))
    return bio.getvalue()


def write_wav(path, samples, rate):
    with open(path, 'wb') as f:
        f.write(write_wav_bytes(samples, rate))


def read_wav_mono(path):
    """Return (samples16, rate) from any PCM WAV (first channel)."""
    with wave.open(path, 'rb') as w:
        rate = w.getframerate()
        nch = w.getnchannels()
        width = w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width == 1:
        vals = list(frames)
        if nch > 1:
            vals = vals[::nch]
        samples = [(v - 128) << 8 for v in vals]
    elif width == 2:
        vals = [struct.unpack_from('<h', frames, i * 2)[0]
                for i in range(len(frames) // 2)]
        if nch > 1:
            vals = vals[::nch]
        samples = vals
    elif width == 4:
        vals = [struct.unpack_from('<i', frames, i * 4)[0] >> 16
                for i in range(len(frames) // 4)]
        if nch > 1:
            vals = vals[::nch]
        samples = vals
    else:
        raise SoundError('Only 8, 16 or 32-bit PCM WAV files can be imported.')
    return samples, rate


def resample(samples, src_rate, dst_rate):
    """Linear interpolation; pure Python so audio works without numpy."""
    import math
    if src_rate == dst_rate or not samples:
        return list(samples)
    ratio = float(src_rate) / float(dst_rate)
    n = max(1, int(len(samples) / ratio))
    src = list(samples)
    last = len(src) - 1
    out = []
    for i in range(n):
        pos = i * ratio
        i0 = int(math.floor(pos))
        if i0 >= last:
            out.append(src[last])
            continue
        f = pos - i0
        out.append(int(round(src[i0] * (1 - f) + src[i0 + 1] * f)))
    return out


# ====================================================== the .sd hash table
class SoundTable:
    """A .sd file: u32 version, u32 count, then {u32 hash, u16 id, u16 0xCCCC}."""

    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            self.raw = f.read()
        if len(self.raw) < 8:
            raise SoundError('%s is too short to be a .sd file' % path)
        self.version, self.count = struct.unpack_from('<II', self.raw, 0)
        self.entries = []
        for i in range(self.count):
            o = 8 + i * 8
            if o + 8 > len(self.raw):
                raise SoundError('%s is truncated (%d entries declared)' % (path, self.count))
            h, sid, marker = struct.unpack_from('<IHH', self.raw, o)
            self.entries.append({'index': i, 'hash': h, 'id': sid,
                                 'marker': marker, 'offset': o})

    def find(self, hash_value):
        for e in self.entries:
            if e['hash'] == hash_value:
                return e
        return None

    def set_field(self, index, hash_value=None, sid=None):
        """Return new .sd bytes with one entry changed."""
        if index < 0 or index >= self.count:
            raise SoundError('No .sd entry %d (there are %d)' % (index, self.count))
        out = bytearray(self.raw)
        e = self.entries[index]
        if hash_value is not None:
            struct.pack_into('<I', out, e['offset'], hash_value & 0xffffffff)
        if sid is not None:
            if not 0 <= sid <= 0xffff:
                raise SoundError('The id must be between 0 and 65535.')
            struct.pack_into('<H', out, e['offset'] + 4, sid)
        return bytes(out)

    def describe(self):
        lines = ['Sound name table (.sd)', '',
                 'Size:      %s bytes' % format(len(self.raw), ','),
                 'Version:   %d' % self.version,
                 'Entries:   %d' % self.count, '',
                 'Each entry is { name hash, sound index, 0xCCCC }. The hash is the']
        lines += ['java31 hash of a name stored in the matching .sdat SYMB block.']
        return '\n'.join(lines)


# ====================================================== the .sdat archive
class SdatSound:
    """One named, playable item (a music sequence or a sound effect)."""

    def __init__(self, name, kind, index, seq, sample, program, bank):
        self.name = name
        self.kind = kind                 # 'music' or 'sfx'
        self.index = index               # index in SYMB / the .sd table order
        self.seq = seq                   # parsed SSEQ/SSAR entry
        self.sample = sample             # Sample or None (sfx)
        self.program = program
        self.bank = bank

    @property
    def duration(self):
        if self.kind == 'sfx' and self.sample:
            return self.sample.duration
        return None     # computed by the renderer when asked


class Sample:
    def __init__(self, archive, index, typ, rate, loop, loopstart,
                 length, raw, data, samples):
        self.archive = archive           # index into sdat.swar
        self.index = index
        self.type = typ                  # 0=PCM8, 1=PCM16, 2=ADPCM
        self.rate = rate
        self.loop = loop
        self.loopstart = loopstart
        self.length = length             # byte length incl. ADPCM header
        self.raw = raw                   # full sample block bytes
        self.data = data                 # bytes after the 12-byte header
        self.samples = samples           # decoded mono 16-bit list

    @property
    def duration(self):
        return len(self.samples) / float(self.rate) if self.rate else 0.0

    def type_name(self):
        return {0: 'PCM8', 1: 'PCM16', 2: 'IMA-ADPCM'}.get(self.type, 'type %d' % self.type)


class SoundArchive:
    """A NITRO SDAT archive: SYMB, INFO, FAT and FILE blocks."""

    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            self.raw = f.read()
        d = self.raw
        if d[:4] != b'SDAT':
            raise SoundError('%s is not a NITRO SDAT archive' % path)
        self.version = u16(d, 6)
        self.filesize = u32(d, 8)
        self.header_size = u16(d, 0x0c)
        self.nblocks = u16(d, 0x0e)
        self.blocks = {}
        for i, n in enumerate(('SYMB', 'INFO', 'FAT', 'FILE')):
            self.blocks[n] = (u32(d, 0x10 + i * 8), u32(d, 0x14 + i * 8))
        self._parse_symb()
        self._parse_info()
        self._parse_fat()
        self._parse_swar()
        self._parse_sbnk()
        self._parse_seq()
        self._build_sounds()

    # ---------------------------------------------------------- blocks
    def _parse_symb(self):
        d = self.raw
        b, size = self.blocks['SYMB']
        if d[b:b + 4] != b'SYMB':
            raise SoundError('SDAT has no SYMB block')
        labels = ('SSEQ', 'SSAR', 'BANK', 'SWAR', 'PLAYER', 'GROUP', 'PLAYER2', 'STRM')
        self.symb_off = [u32(d, b + 8 + 4 * i) for i in range(8)]
        self.symb_names = {}
        for i, ro in enumerate(self.symb_off):
            if not ro:
                self.symb_names[labels[i]] = []
                continue
            p = b + ro
            cnt = u32(d, p)
            if labels[i] == 'SSAR':
                entries = []
                for j in range(cnt):
                    fo = u32(d, p + 4 + j * 8)
                    lo = u32(d, p + 4 + j * 8 + 4)
                    folder = self._symbol(fo)
                    lp = b + lo
                    nfiles = u32(d, lp)
                    files = [self._symbol(u32(d, lp + 4 + k * 4)) for k in range(nfiles)]
                    entries.append({'folder': folder, 'files': files, 'name_off': fo,
                                    'list_off': lo})
                self.symb_names['SSAR'] = entries
            else:
                self.symb_names[labels[i]] = [self._symbol(u32(d, p + 4 + j * 4))
                                              for j in range(cnt)]
        # flat name list for hash lookups
        self.all_names = []
        for m in re.finditer(rb'[ -~]{3,}\x00', d[b + 0x40:b + size]):
            self.all_names.append(m.group()[:-1].decode('latin-1'))

    def _symbol(self, rel):
        d = self.raw
        b = self.blocks['SYMB'][0]
        if not rel or b + rel >= len(d):
            return None
        end = d.find(b'\x00', b + rel)
        return d[b + rel:end].decode('latin-1') if end >= 0 else None

    def _parse_info(self):
        d = self.raw
        b = self.blocks['INFO'][0]
        if d[b:b + 4] != b'INFO':
            raise SoundError('SDAT has no INFO block')
        labels = ('SSEQ', 'SSAR', 'BANK', 'SWAR', 'PLAYER', 'GROUP', 'PLAYER2', 'STRM')
        self.info_off = [u32(d, b + 8 + 4 * i) for i in range(8)]
        self.info = {}
        for i, ro in enumerate(self.info_off):
            if not ro:
                self.info[labels[i]] = []
                continue
            p = b + ro
            cnt = u32(d, p)
            self.info[labels[i]] = [u32(d, p + 4 + j * 4) for j in range(cnt)]

    def _parse_fat(self):
        d = self.raw
        b = self.blocks['FAT'][0]
        if d[b:b + 4] != b'FAT ':
            raise SoundError('SDAT has no FAT block')
        cnt = u32(d, b + 8)
        self.fat = [(u32(d, b + 12 + j * 16), u32(d, b + 12 + j * 16 + 4))
                    for j in range(cnt)]

    def filedata(self, fid):
        if fid >= len(self.fat):
            raise SoundError('FAT file id %d is out of range' % fid)
        s, size = self.fat[fid]
        return self.raw[s:s + size]

    def _parse_swar(self):
        self.swar = []
        names = self.symb_names.get('SWAR', [])
        for k, rel in enumerate(self.info.get('SWAR', [])):
            fid = u16(self.raw, self.blocks['INFO'][0] + rel)
            d = self.filedata(fid)
            if d[:4] != b'SWAR':
                raise SoundError('SWAR file %d has a bad magic' % fid)
            cnt = u32(d, 0x38)
            offs = [u32(d, 0x3c + 4 * j) for j in range(cnt)]
            waves = []
            for j, wo in enumerate(offs):
                try:
                    waves.append(self._decode_sample(d, wo, j))
                except Exception:
                    pass
            self.swar.append({'fid': fid, 'name': names[k] if k < len(names) else '?',
                              'samples': waves, 'offsets': offs})

    def _decode_sample(self, d, wo, index):
        typ = d[wo]
        loopflag = d[wo + 1]
        rate = u16(d, wo + 2)
        loopstart = u16(d, wo + 6) * 4
        length = u32(d, wo + 8) * 4        # 4-byte units -> bytes
        block = d[wo:wo + 12 + length]
        data = d[wo + 12:wo + 12 + length]
        if typ == 0:
            smp = pcm8_decode(data, length)
        elif typ == 1:
            smp = pcm16_decode(data, length)
        elif typ == 2:
            smp = decode_adpcm(data[4:], data[:4], (length - 4) * 2)
        else:
            raise SoundError('unknown sample type %d' % typ)
        return Sample(len(self.swar), index, typ, rate, loopflag, loopstart,
                      length, block, data, smp)

    def _parse_sbnk(self):
        self.sbnk = []
        names = self.symb_names.get('BANK', [])
        b = self.blocks['INFO'][0]
        for k, rel in enumerate(self.info.get('BANK', [])):
            fid = u16(self.raw, b + rel)
            swarids = [u16(self.raw, b + rel + 4 + 2 * j) for j in range(4)]
            d = self.filedata(fid)
            if d[:4] != b'SBNK':
                raise SoundError('SBNK file %d has a bad magic' % fid)
            cnt = u32(d, 0x38)
            instruments = []
            for j in range(cnt):
                rec = d[0x3c + 4 * j]
                off = u16(d, 0x3c + 4 * j + 1)
                notes = []
                if rec == 0:
                    pass
                elif rec < 16:
                    notes = [self._note_def(d, off)]
                elif rec == 16:
                    lo, hi = d[off], d[off + 1]
                    notes = [self._note_def(d, off + 2 + 12 * i)
                             for i in range(max(0, hi - lo + 1))]
                elif rec == 17:
                    notes = [self._note_def(d, off + 8 + 12 * i) for i in range(8)]
                instruments.append({'index': j, 'type': rec, 'offset': off, 'notes': notes})
            self.sbnk.append({'fid': fid, 'name': names[k] if k < len(names) else '?',
                              'swarids': swarids, 'instruments': instruments})

    def _note_def(self, d, o):
        if o + 10 > len(d):
            return None
        return {'swav': u16(d, o), 'swar': u16(d, o + 2), 'note': d[o + 4],
                'attack': d[o + 5], 'decay': d[o + 6], 'sustain': d[o + 7],
                'release': d[o + 8], 'pan': d[o + 9]}

    def _parse_seq(self):
        self.seqs = []
        b = self.blocks['INFO'][0]
        for k, rel in enumerate(self.info.get('SSEQ', [])):
            fid = u16(self.raw, b + rel)
            bank = u16(self.raw, b + rel + 4)
            d = self.filedata(fid)
            if d[:4] == b'SSEQ':
                name = self.symb_names['SSEQ'][k] if k < len(self.symb_names['SSEQ']) else 'SSEQ%d' % k
                self.seqs.append({'group': 'SSEQ', 'name': name, 'kind': 'music',
                                  'fid': fid, 'bank': bank, 'index': k, 'raw': d})
        # The SSAR is a single file shared by all effects; the bank that has a
        # matching instrument count is the one the effects use.
        if self.info.get('SSAR'):
            fid = u16(self.raw, b + self.info['SSAR'][0])
            d = self.filedata(fid)
            fxbank = self._fx_bank()
            folders = self.symb_names.get('SSAR', [])
            idx = 0
            for fol in folders:
                for name in fol['files']:
                    self.seqs.append({'group': 'SSAR', 'name': name, 'kind': 'sfx',
                                      'fid': fid, 'bank': fxbank, 'index': idx,
                                      'sfx_index': idx, 'raw': d})
                    idx += 1

    def _fx_bank(self):
        # Prefer a bank whose name mentions FX/SFX; otherwise the last bank.
        for i, b in enumerate(self.sbnk):
            if re.search(r'(fx|sfx|sound)', (b['name'] or '').lower()):
                return i
        return len(self.sbnk) - 1 if self.sbnk else 0

    def _build_sounds(self):
        self.sounds = []
        # Music: each sequence + the bank named by its INFO entry.
        for seq in self.seqs:
            if seq['kind'] != 'music':
                continue
            bank = seq['bank'] if seq['bank'] < len(self.sbnk) else 0
            self.sounds.append(SdatSound(seq['name'], 'music', len(self.sounds),
                                         seq, None, None, bank))
        # Effects: instrument i of the FX bank -> SWAV number in its note.
        for seq in self.seqs:
            if seq['kind'] != 'sfx':
                continue
            bank_idx = seq['bank']
            sample = None
            program = 0
            if bank_idx < len(self.sbnk):
                bank = self.sbnk[bank_idx]
                i = seq['sfx_index']
                if i < len(bank['instruments']):
                    ins = bank['instruments'][i]
                    notes = [n for n in ins['notes'] if n]
                    if notes:
                        n = notes[0]
                        swar_idx = bank['swarids'][n['swar']] if n['swar'] < 4 else 0xffff
                        if swar_idx < len(self.swar):
                            waves = self.swar[swar_idx]['samples']
                            for w in waves:
                                if w.index == n['swav']:
                                    sample = w
                                    break
                        program = i
            self.sounds.append(SdatSound(seq['name'], 'sfx', len(self.sounds),
                                         seq, sample, program, bank_idx))

    # ---------------------------------------------------------- summary
    def describe(self):
        lines = ['NITRO sound archive (.sdat)', '',
                 'Size:      %s bytes' % format(len(self.raw), ','),
                 'Version:   %04X' % self.version,
                 'Blocks:    %s' % ', '.join('%s @0x%X (%s bytes)' %
                                             (n, self.blocks[n][0], format(self.blocks[n][1], ','))
                                             for n in ('SYMB', 'INFO', 'FAT', 'FILE')),
                 'Files:     %d in the FAT' % len(self.fat), '',
                 'Music:     %d sequence(s)' % len([s for s in self.sounds if s.kind == 'music']),
                 'Effects:   %d sound effect(s)' % len([s for s in self.sounds if s.kind == 'sfx']),
                 'Waves:     %d archive(s), %d sample(s)' %
                 (len(self.swar), sum(len(a['samples']) for a in self.swar)),
                 'Banks:     %d' % len(self.sbnk), '']
        for a in self.swar:
            kinds = {}
            for w in a['samples']:
                kinds[w.type_name()] = kinds.get(w.type_name(), 0) + 1
            lines.append('%s: %d samples (%s)' %
                         (a['name'] or 'wave archive', len(a['samples']),
                          ', '.join('%s x%d' % kv for kv in kinds.items())))
        lines += ['', 'Select a sound to see its details. "Play" makes a temporary WAV',
                  'and plays it; "Export WAV" saves it. Music is rendered by a simple',
                  'SSEQ player (an approximation, not the game\'s own mixer).']
        return '\n'.join(lines)

    def describe_sound(self, snd):
        lines = ['%s' % snd.name, '',
                 'Kind:      %s' % ('sequenced music (SSEQ)' if snd.kind == 'music'
                                    else 'sound effect (SSAR)'),
                 'Archive:   %s (FAT file %d)' %
                 (('SSEQ' if snd.kind == 'music' else 'SSAR'), snd.seq['fid']),
                 'Data:      %s bytes' % format(len(snd.seq['raw']), ','),
                 'Bank:      %s (%d)' %
                 (self.sbnk[snd.bank]['name'] if snd.bank < len(self.sbnk) else '?', snd.bank),
                 'Hash:      0x%08X' % java31(snd.name)]
        if snd.sample:
            w = snd.sample
            lines += ['', 'Sample:    %s #%d in %s' %
                      (w.type_name(), w.index, self.swar[w.archive]['name'] or 'wave archive'),
                      'Rate:      %d Hz' % w.rate,
                      'Length:    %d samples (%.3f s)' % (len(w.samples), w.duration),
                      'Data:      %s bytes in the archive' % format(w.length, ',')]
            notes = [n for n in self.sbnk[snd.bank]['instruments'][snd.program]['notes'] if n] \
                if snd.bank < len(self.sbnk) and snd.program < len(self.sbnk[snd.bank]['instruments']) else []
            if notes:
                n = notes[0]
                lines += ['', 'Bank note: %d, envelope A/D/S/R = %d/%d/%d/%d, pan %d' %
                          (n['note'], n['attack'], n['decay'], n['sustain'], n['release'], n['pan'])]
        else:
            lines += ['', 'The music is sequenced: notes refer to samples in %s.' %
                      (self.sbnk[snd.bank]['name'] if snd.bank < len(self.sbnk) else 'the bank')]
        lines += ['', 'The .sd file maps the hash above to the index the game uses.']
        return '\n'.join(lines)

    # ---------------------------------------------------------- rendering
    def render(self, snd, max_seconds=60.0, rate=32000):
        """Return (samples16, rate). SFX play their raw sample; music is sequenced."""
        if snd.kind == 'sfx':
            if not snd.sample:
                raise SoundError('%s has no sample to play' % snd.name)
            w = snd.sample
            smp = resample(w.samples, w.rate, rate)
            # one-shot; trim to the requested length
            return smp[:int(max_seconds * rate)], rate
        return self._render_seq(snd, max_seconds, rate)

    def _render_seq(self, snd, max_seconds, rate):
        d = snd.seq['raw']
        off = u32(d, 0x18)
        bank = self.sbnk[snd.bank] if snd.bank < len(self.sbnk) else None
        tempo, tracks, notes = 120, [], []
        master = {'pos': off, 'end': len(d), 'time': 0.0, 'stack': [], 'program': 0,
                  'volume': 1.0, 'transpose': 0, 'wait': 0.0, 'depth': 0}
        guard = 0
        tick = 0.0
        end_time = 0.0
        max_ticks = int(max_seconds * 400)
        while guard < 200000:
            guard += 1
            if not tracks:
                tr = master
            else:
                tr = min(tracks, key=lambda t: t['time'])
                if tr['time'] > max_seconds:
                    break
            if tr['pos'] >= tr['end']:
                if tr is master:
                    break
                tracks.remove(tr)
                continue
            p = tr['pos']
            ev = d[p]
            tr['pos'] = p + 1
            sec_per_tick = 60.0 / (max(1, tempo) * 48.0)
            if ev == 0xff:                                   # end of track
                if tr is master:
                    break
                tracks.remove(tr)
                continue
            if ev == 0xfe:                                   # track mask
                tr['pos'] += 2
                continue
            if ev == 0x93:                                   # track definition
                tnum = d[tr['pos']]
                addr = d[tr['pos'] + 1] | (d[tr['pos'] + 2] << 8) | (d[tr['pos'] + 3] << 16)
                tr['pos'] += 4
                if addr < len(d):
                    tracks.append({'pos': addr, 'end': len(d), 'time': tr['time'],
                                   'stack': [], 'program': 0, 'volume': 1.0,
                                   'transpose': 0, 'wait': 0.0, 'depth': 1})
                continue
            if ev == 0xe1:                                   # tempo
                tempo = u16(d, tr['pos'])
                tr['pos'] += 2
                continue
            if ev < 0x80:                                    # note on: note, vel, dur(VLQ)
                note = ev
                vel = d[tr['pos']]
                tr['pos'] += 1
                dur, tr['pos'] = _vlq(d, tr['pos'])
                notes.append((tr['time'], dur * sec_per_tick, note + tr['transpose'],
                              vel / 127.0, tr['program'], tr['volume']))
                tr['time'] += dur * sec_per_tick
                end_time = max(end_time, tr['time'])
                continue
            if ev == 0x80:                                   # rest
                dur, tr['pos'] = _vlq(d, tr['pos'])
                tr['time'] += dur * sec_per_tick
                end_time = max(end_time, tr['time'])
                continue
            if ev == 0x81:                                   # program change
                prog, tr['pos'] = _vlq(d, tr['pos'])
                tr['program'] = prog
                continue
            if ev == 0xc0:                                   # pan (ignored, mono)
                tr['pos'] += 1
                continue
            if ev == 0xc1:                                   # volume
                tr['volume'] = d[tr['pos']] / 127.0
                tr['pos'] += 1
                continue
            if ev == 0xc2:                                   # master volume
                tr['pos'] += 1
                continue
            if ev == 0xc3:                                   # transpose
                tr['transpose'] = d[tr['pos']] - 64
                tr['pos'] += 1
                continue
            if 0xc4 <= ev <= 0xcf:                           # misc controller
                tr['pos'] += 1
                continue
            if ev == 0xd4:                                   # loop start
                tr['stack'].append((d[tr['pos']], tr['pos'] + 1))
                tr['pos'] += 1
                continue
            if ev == 0xfc:                                   # loop end
                if tr['stack']:
                    count, where = tr['stack'][-1]
                    if count == 0 or count > 1:
                        if count > 1:
                            tr['stack'][-1] = (count - 1, where)
                        tr['pos'] = where
                    else:
                        tr['stack'].pop()
                continue
            if ev == 0x94 or ev == 0x95:                     # jump / call
                addr = (d[tr['pos']] | (d[tr['pos'] + 1] << 8) | (d[tr['pos'] + 2] << 16))
                tr['pos'] += 3
                if addr < tr['pos'] and addr < len(d):
                    tr['pos'] = addr
                elif ev == 0x95:
                    continue
                continue
            if ev == 0xfd:                                   # return
                continue
            # unknown event: skip one byte (a few control bytes are 2-byte)
            continue

        if not notes or end_time <= 0:
            raise SoundError('This sequence has no notes this player understands.')

        nout = min(int(end_time * rate) + rate // 2, int(max_seconds * rate))
        out = [0.0] * nout
        for start, dur, note, vel, program, vol in notes:
            w = self._sample_for_note(bank, program, note)
            if not w or not w.samples:
                continue
            i0 = int(start * rate)
            ndur = int(dur * rate)
            if i0 >= nout or ndur <= 0:
                continue
            n = min(ndur, nout - i0)
            factor = 2.0 ** ((note - w.base_note) / 12.0) if hasattr(w, 'base_note') else 1.0
            seg = resample(w.samples, max(1, int(w.rate * factor)), rate)[:n]
            if not seg:
                continue
            seg = _envelope(seg, ndur, rate, w.envelope)
            gain = vel * vol
            for j, v in enumerate(seg):
                out[i0 + j] += v * gain
        # Keep a little headroom: many notes can overlap in sequenced music.
        peak = max((abs(v) for v in out), default=0.0)
        if peak > 30000:
            scale = 30000.0 / peak
            out = [v * scale for v in out]
        return [int(round(max(-32768, min(32767, v)))) for v in out], rate

    def _sample_for_note(self, bank, program, note):
        if not bank or program >= len(bank['instruments']):
            return None
        ins = bank['instruments'][program]
        chosen = None
        for nd in ins['notes']:
            if not nd:
                continue
            if ins['type'] == 16 and note != nd['note'] and len(ins['notes']) > 1:
                continue
            if chosen is None or abs(nd['note'] - note) < abs(chosen['note'] - note):
                chosen = nd
        if not chosen:
            return None
        swar_idx = bank['swarids'][chosen['swar']] if chosen['swar'] < 4 else 0xffff
        if swar_idx >= len(self.swar):
            return None
        for w in self.swar[swar_idx]['samples']:
            if w.index == chosen['swav']:
                w.base_note = chosen['note']
                w.envelope = (chosen['attack'], chosen['decay'], chosen['sustain'], chosen['release'])
                return w
        return None

    # ---------------------------------------------------------- editing
    def replace_sample(self, archive_index, sample_index, wav_path, loop=None,
                       progress=None):
        """Return new SDAT bytes with one sample replaced by a WAV (IMA-ADPCM).

        The new sample is appended at the end of its SWAR, so no other sample
        moves inside the archive; the FAT and FILE blocks are rebuilt.
        """
        if archive_index >= len(self.swar):
            raise SoundError('No wave archive %d' % archive_index)
        arc = self.swar[archive_index]
        old = next((w for w in arc['samples'] if w.index == sample_index), None)
        if old is None:
            raise SoundError('No sample %d in %s' % (sample_index, arc['name']))
        src, srate = read_wav_mono(wav_path)
        if srate < 1000 or srate > 96000:
            raise SoundError('The WAV sample rate (%d Hz) looks wrong.' % srate)
        smp = resample(src, srate, old.rate)     # keep the game's pitch
        if not smp:
            raise SoundError('The WAV is empty.')
        header, body = encode_adpcm(smp)
        # The length field counts the bytes AFTER the 12-byte sample header:
        # the 4-byte ADPCM header plus the nibble data (padded to 4 bytes).
        data_len = len(header) + len(body)
        loopflag = int(old.loop if loop is None else loop)
        # 12-byte sample block: type, loop, rate, time, loop offset, length
        sample = bytearray(struct.pack('<BBHHHI', 2, loopflag, old.rate,
                                       (16757000 // old.rate) & 0xffff, 1, data_len // 4))
        sample += header + body
        # --- build the new SWAR (append the sample, point its offset at it)
        fid = arc['fid']
        swar = bytearray(self.filedata(fid))
        new_off = len(swar)
        while new_off % 4:
            swar.append(0)
            new_off += 1
        struct.pack_into('<I', swar, 0x3c + sample_index * 4, new_off)
        swar += sample
        struct.pack_into('<I', swar, 0x08, len(swar))
        struct.pack_into('<I', swar, 0x14, len(swar) - 0x10)
        # --- rebuild FILE + FAT
        rebuilt = {}
        rebuilt[fid] = bytes(swar)
        for i in range(len(self.fat)):
            if i not in rebuilt:
                rebuilt[i] = self.filedata(i)
        new_file = bytearray(b'FILE' + struct.pack('<III', 0, len(self.fat), 0))
        new_fat = bytearray(b'FAT ' + struct.pack('<II', 0, len(self.fat)))
        # The FAT grows by 0 or more bytes, so compute the new FILE offset from
        # the rebuilt FAT size before filling in the file offsets.
        sym, info = self.blocks['SYMB'], self.blocks['INFO']
        # The header's SYMB/INFO sizes are 2 bytes short (the block's own size
        # field is rounded up to a 4-byte boundary), so copy the full blocks.
        sym_len = u32(self.raw, sym[0] + 4)
        info_len = u32(self.raw, info[0] + 4)
        new_fat_off = sym[0] + sym_len + info_len
        new_file_off = new_fat_off + 12 + len(self.fat) * 16
        offsets = []
        for i in range(len(self.fat)):
            pos = new_file_off + len(new_file)
            while pos % 4:
                new_file.append(0)
                pos += 1
            offsets.append(pos)
            new_fat += struct.pack('<IIQ', pos, len(rebuilt[i]), 0)
            new_file += rebuilt[i]
        struct.pack_into('<I', new_file, 4, len(new_file))
        struct.pack_into('<I', new_fat, 4, len(new_fat))
        # --- header: update FAT/FILE offsets, total size and FILE block size
        head = bytearray(self.raw[:0x40])
        struct.pack_into('<I', head, 0x08, new_file_off + len(new_file))
        struct.pack_into('<I', head, 0x20, new_fat_off)
        struct.pack_into('<I', head, 0x28, new_file_off)
        struct.pack_into('<I', head, 0x2c, len(new_file))
        out = bytes(head) + self.raw[sym[0]:sym[0] + sym_len] + \
            self.raw[info[0]:info[0] + info_len] + bytes(new_fat) + bytes(new_file)
        return out

    def name_offsets(self):
        """Map each SYMB name to its absolute byte offset in the SDAT."""
        b = self.blocks['SYMB'][0]
        d = self.raw
        out = {}
        labels = ('SSEQ', 'SSAR', 'BANK', 'SWAR', 'PLAYER', 'GROUP', 'PLAYER2', 'STRM')
        for i, lab in enumerate(labels):
            ro = self.symb_off[i]
            if not ro:
                continue
            p = b + ro
            cnt = u32(d, p)
            if lab == 'SSAR':
                for j in range(cnt):
                    fo = u32(d, p + 4 + j * 8)
                    lo = u32(d, p + 4 + j * 8 + 4)
                    name = self._symbol(fo)
                    if name:
                        out[name] = b + fo
                    lp = b + lo
                    for k in range(u32(d, lp)):
                        so = u32(d, lp + 4 + k * 4)
                        nm = self._symbol(so)
                        if nm:
                            out[nm] = b + so
            else:
                for j in range(cnt):
                    so = u32(d, p + 4 + j * 4)
                    nm = self._symbol(so)
                    if nm:
                        out[nm] = b + so
        return out

    def rename(self, old_name, new_name):
        """Return (new_sdat, hash_old, hash_new).

        The new name must be ASCII/latin-1 and no longer than the old one: the
        string is overwritten in place so every offset stays valid.
        """
        try:
            new_name.encode('latin-1')
        except UnicodeEncodeError:
            raise SoundError('Sound names must be latin-1 text.')
        if '\x00' in new_name:
            raise SoundError('A name cannot contain a NUL character.')
        b = self.blocks['SYMB'][0]
        abs_off = self.name_offsets().get(old_name)
        if abs_off is None:
            raise SoundError('The name %r is not in the SYMB block.' % old_name)
        old_len = len(old_name.encode('latin-1'))
        if len(new_name.encode('latin-1')) > old_len:
            raise SoundError('The new name is longer than %r (%d characters); it would move '
                             'later strings. Use a name of the same or shorter length.'
                             % (old_name, old_len))
        out = bytearray(self.raw)
        blob = new_name.encode('latin-1') + b'\x00' * (old_len - len(new_name))
        out[abs_off:abs_off + old_len] = blob
        return bytes(out), java31(old_name), java31(new_name)


def _vlq(d, p):
    v = 0
    while p < len(d):
        b = d[p]
        p += 1
        v = (v << 7) | (b & 0x7f)
        if not b & 0x80:
            return v, p
    return v, p


def _envelope(seg, total, rate, env):
    """Simple linear ADSR over one note (pure Python)."""
    if not env:
        return seg
    a, dcy, s, rel = env
    n = len(seg)
    if n < 4:
        return seg
    atk = max(1, int((127 - a) / 127.0 * 0.20 * rate))
    dec = max(1, int((127 - dcy) / 127.0 * 0.40 * rate))
    rls = max(1, int((127 - rel) / 127.0 * 0.40 * rate))
    sus = max(0.05, s / 127.0)
    out = []
    for i, v in enumerate(seg):
        if i < atk:
            g = (i + 1) / float(atk)
        elif i < atk + dec:
            g = 1.0 - (1.0 - sus) * (i - atk) / float(dec)
        else:
            g = sus
        if i >= n - rls:
            g *= (n - i) / float(rls)
        out.append(v * g)
    return out


# ====================================================== convenience opener
def sd_entry_for_name(table, name):
    """Find the .sd entry for a SYMB name (effects are hashed upper-case)."""
    if table is None:
        return None
    return table.find(java31(name)) or table.find(java31(name.upper()))


def is_sound_file(path):
    return path.lower().endswith(('.sd', '.sdat'))


def companion(path):
    """Return (sd_path, sdat_path); whichever is missing is None."""
    low = path.lower()
    base = os.path.splitext(path)[0]
    sd, sdat = None, None
    if low.endswith('.sd'):
        sd = path
        for name in os.listdir(os.path.dirname(path)):
            if name.lower() == os.path.basename(base).lower() + '.sdat':
                sdat = os.path.join(os.path.dirname(path), name)
                break
    elif low.endswith('.sdat'):
        sdat = path
        for name in os.listdir(os.path.dirname(path)):
            if name.lower() == os.path.basename(base).lower() + '.sd':
                sd = os.path.join(os.path.dirname(path), name)
                break
    return sd, sdat


def open_sound(path):
    """Open a .sd or .sdat and return (SoundTable|None, SoundArchive|None)."""
    sd, sdat = companion(path)
    table = SoundTable(sd) if sd and os.path.exists(sd) else None
    arch = SoundArchive(sdat) if sdat and os.path.exists(sdat) else None
    if table is None and arch is None:
        raise SoundError('No .sd or .sdat file was found.')
    return table, arch

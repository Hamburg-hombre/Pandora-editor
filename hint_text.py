"""Help Hint Panel text stored in Templates.CRT.

Fort Knox's STR_HINT1..11 placements are Help Hint Panels (w1 0x3973BB0B).
The text they display is one of the NUL-terminated strings in a block that
starts with "YOUR PERSONAL SIDEARM..." in Templates.CRT.  No pointer table
addresses the strings directly; they are found by walking the block.

Editing is deliberately done **in place**: the new text is padded with spaces
to exactly the original byte length.  Templates.CRT has several offset tables
that index into this text by character position; changing the block length
would invalidate them and corrupt unrelated level/menu text.
"""
import re

HINT_TEXT_OFF = 0x9585             # first hint string ("YOUR PERSONAL SIDEARM...")
HINT_MARKER = b'J VT Bot\x00'      # immediately precedes the hint block
HINT_COUNT = 19


def hint_strings(crt):
    """Return [(file offset, text)] for the hint block, in display order."""
    m = crt.find(HINT_MARKER)
    if m < 0:
        return []
    start = m + len(HINT_MARKER)
    out = []
    off = start
    while len(out) < HINT_COUNT:
        end = crt.find(b'\x00', off)
        if end < 0 or end - off > 2000:
            break
        if end > off:                       # skip the empty separator strings
            out.append((off, crt[off:end].decode('latin-1').rstrip()))
        off = end + 1
    return out


def read_hint(crt, number):
    items = hint_strings(crt)
    if 1 <= number <= len(items):
        return items[number - 1][1]
    return ''


def hint_capacity(crt, number):
    """Maximum number of characters that can be written to hint `number`."""
    items = hint_strings(crt)
    if not (1 <= number <= len(items)):
        return 0
    off = items[number - 1][0]
    end = crt.find(b'\x00', off)
    return end - off


def write_hint(crt, number, text):
    """Replace hint `number` (1-based) in place; pad with spaces to fit."""
    items = hint_strings(crt)
    if not (1 <= number <= len(items)):
        raise ValueError('no hint %d in Templates.CRT' % number)
    off = items[number - 1][0]
    end = crt.find(b'\x00', off)
    capacity = end - off
    blob = str(text).encode('latin-1')
    if b'\x00' in blob:
        raise ValueError('hint text cannot contain NUL bytes')
    if len(blob) > capacity:
        raise ValueError('hint text is %d characters; this slot holds at most %d'
                         % (len(blob), capacity))
    blob += b' ' * (capacity - len(blob))
    data = bytearray(crt)
    data[off:end] = blob
    return bytes(data)

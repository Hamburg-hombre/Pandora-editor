"""Where the 3D viewer keeps its JSON settings.

Both files used to be written into the unpacked ROM work folder:

    <work>/_viewer3d_overrides.json
    <work>/_viewer3d_env.json

They now live in a dedicated folder next to pandora_editor.py:

    <project folder>/viewer3d_data/

`read_path()` still finds an old file in the work folder if no new one exists,
so existing overrides/environment settings are not lost.  `save_path()` always
writes to the new folder (creating it if needed).
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, 'viewer3d_data')


def ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)
    return DATA_DIR


def read_path(name, work=None):
    new = os.path.join(DATA_DIR, name)
    if not os.path.isfile(new) and work:
        old = os.path.join(work, name)
        if os.path.isfile(old):
            return old
    return new


def save_path(name):
    ensure_dir()
    return os.path.join(DATA_DIR, name)

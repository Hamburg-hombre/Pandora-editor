"""Used by Pandora (Tk side, standard library only): open a level in the 3D viewer window.

If a viewer is already running the level name is sent to it (one window is reused); otherwise
a new viewer process is started."""
import os, sys, json, socket, tempfile, subprocess, importlib.util

PORT_FILE = os.path.join(tempfile.gettempdir(), 'pandora_viewer3d.port')
PARENT = os.path.dirname(os.path.abspath(__file__))


def missing_packages():
    return [p for p in ('PySide6', 'moderngl', 'numpy', 'PIL') if importlib.util.find_spec(p) is None]


def _send(work, level):
    try:
        port = int(open(PORT_FILE).read().strip())
        with socket.create_connection(('127.0.0.1', port), timeout=0.5) as s:
            s.sendall((json.dumps({'cmd': 'open', 'work': work, 'level': level, 'pid': os.getpid()}) + '\n').encode('utf-8'))
        return True
    except (OSError, ValueError):
        return False


def open_level(work, level):
    """Returns None on success or an error message for the caller to show."""
    miss = missing_packages()
    if miss:
        names = {'PIL': 'pillow'}
        return ('The 3D viewer needs extra packages: %s\n\nOpen a command prompt and run:\n    pip install PySide6 moderngl numpy pillow'
                % ', '.join(names.get(m, m) for m in miss))
    if _send(work, level):
        return None
    exe = sys.executable
    if os.name == 'nt':
        w = os.path.join(os.path.dirname(exe), 'pythonw.exe')
        if os.path.isfile(w):
            exe = w
    try:
        subprocess.Popen([exe, os.path.join(PARENT, 'viewer3d_main.py'), '--work', work, '--level', level,
                          '--parent-pid', str(os.getpid())], cwd=PARENT)
    except OSError as e:
        return 'Could not start the 3D viewer: %s' % e
    return None

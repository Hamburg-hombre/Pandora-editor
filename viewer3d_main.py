"""Entry point:  python viewer3d_main.py --work <unpacked folder> [--level <name>]

One viewer window is reused: later launches send the new level name to the running window
over a local socket (127.0.0.1) instead of opening another one."""
import sys, os, json, argparse, tempfile, traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PORT_FILE = os.path.join(tempfile.gettempdir(), 'pandora_viewer3d.port')


def pid_alive(pid):
    if os.name == 'nt':
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)           # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259           # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main():
    PARENT = os.path.dirname(os.path.abspath(__file__))
    log = os.path.join(PARENT, 'viewer3d_error.log')
    def hook(t, v, tb):
        txt = ''.join(traceback.format_exception(t, v, tb))
        try:
            open(log, 'a').write(txt + '\n')
        except OSError:
            pass
        sys.__excepthook__(t, v, tb)
    sys.excepthook = hook

    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True)
    ap.add_argument('--level')
    ap.add_argument('--parent-pid', type=int, default=0)
    a = ap.parse_args()

    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QSurfaceFormat, QFont
    from PySide6.QtNetwork import QTcpServer, QHostAddress
    from viewer3d_gl import GLView
    from viewer3d_ui import MainWindow, PANDORA_QSS, apply_light_theme

    QSurfaceFormat.setDefaultFormat(GLView.default_format())
    app = QApplication(sys.argv)
    apply_light_theme(app)
    app.setFont(QFont('Segoe UI', 9))
    app.setStyleSheet(PANDORA_QSS)
    win = MainWindow()
    win.show()

    state = {'pid': a.parent_pid}            # the Pandora process this window belongs to
    from PySide6.QtCore import QTimer
    watch = QTimer()

    def check_parent():
        if state['pid'] and not pid_alive(state['pid']):
            watch.stop()                     # closing may ask about unsaved edits; ask only once
            win.close()
    watch.timeout.connect(check_parent)
    watch.start(1000)

    server = QTcpServer()
    server.listen(QHostAddress.LocalHost, 0)
    with open(PORT_FILE, 'w') as f:
        f.write(str(server.serverPort()))

    def on_conn():
        sock = server.nextPendingConnection()
        def ready():
            while sock.canReadLine():
                try:
                    msg = json.loads(bytes(sock.readLine()).decode('utf-8'))
                except ValueError:
                    continue
                if msg.get('pid'):
                    state['pid'] = int(msg['pid'])
                if msg.get('cmd') == 'open':
                    win.open_level(msg['work'], msg['level'])
        sock.readyRead.connect(ready)
        ready()
    server.newConnection.connect(on_conn)

    def cleanup():
        try:
            if open(PORT_FILE).read().strip() == str(server.serverPort()):
                os.remove(PORT_FILE)
        except OSError:
            pass
    app.aboutToQuit.connect(cleanup)

    if a.level:
        QTimer.singleShot(50, lambda: win.open_level(a.work, a.level))
    sys.exit(app.exec())


if __name__ == '__main__':
    main()

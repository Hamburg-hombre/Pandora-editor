3D level viewer for the Pandora editor
======================================
Install (Windows):   pip install PySide6 moderngl numpy pillow   (or: pip install -r viewer3d_requirements.txt)
Files:               keep every viewer3d_*.py next to pandora_editor.py (use the patched pandora_editor.py).
Use:                 Pandora > Simple mode > double-click a level name. The same window is reused for the next level.
Manual start:        python viewer3d_main.py --work <unpacked folder> --level 1_0_FortKnox
Errors:              viewer3d_error.log next to pandora_editor.py

viewer3d_scene_data.py  level loading (numpy only, testable without a GPU)
viewer3d_camera.py      camera maths and the control scheme
viewer3d_gl.py          OpenGL 3.3 renderer (moderngl), input, picking, move/rotate handles
viewer3d_ui.py          window, panels, toolbars, cube-net gizmo, environment dialog
viewer3d_launcher.py    what Pandora calls (reuses a running viewer through a local socket)
viewer3d_main.py        entry point

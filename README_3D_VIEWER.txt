3D level viewer for the Pandora editor
======================================
Install (Windows):   pip install PySide6 moderngl numpy pillow   (or: pip install -r viewer3d_requirements.txt)
Files:               keep every viewer3d_*.py next to pandora_editor.py (use the patched pandora_editor.py).
Use:                 Pandora > Simple mode > double-click a level name. The same window is reused for the next level.
Manual start:        python viewer3d_main.py --work <unpacked folder> --level 1_0_FortKnox
Errors:              viewer3d_error.log next to pandora_editor.py

Object models
-------------
The viewer draws the real .nsbmd for each Objects.LP placement at its saved
position/facing.  Which model is used is decided in this order:
  1. your override (Properties panel -> "Assign to name"/"Assign to template")
  2. the built-in verified map in viewer3d_model_map.py
  3. a direct match of the placement's CHR name to a model file
  4. a model found for another placement with the same template hash

Each record stores w0 = java31(instance name.upper()) and w1 = java31(template
name).  w0 was verified against every placement in the game, and for props the
instance name names the model the game draws (e.g. fortKnoxDr01 -> fortknoxdr,
w_RPG -> rpg).  The built-in map comes from that evidence; the full table is in
../mapping/objects_to_models.md.

Waypoints, spawns, hint panels, ladders and AI spawns are deliberately drawn as
markers only - a guessed model there is wrong (the old name-based guesser drew
blinky at every Fort Knox waypoint).  Enemy looks come from the AI template
data in Templates.CRT, not from the spawn's name.  If a model is missing or
wrong, pick the correct one in the Properties panel; overrides are saved in
<work>/_viewer3d_overrides.json and always win over the built-in map.

viewer3d_scene_data.py  level loading (numpy only, testable without a GPU)
viewer3d_model_map.py   verified Objects.LP template -> model map (no 3rd-party modules)
viewer3d_camera.py      camera maths and the control scheme
viewer3d_gl.py          OpenGL 3.3 renderer (moderngl), input, picking, move/rotate handles
viewer3d_ui.py          window, panels, toolbars, cube-net gizmo, environment dialog
viewer3d_launcher.py    what Pandora calls (reuses a running viewer through a local socket)
viewer3d_main.py        entry point

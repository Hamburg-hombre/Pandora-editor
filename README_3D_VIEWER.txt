3D level viewer for the Pandora editor
======================================
Install (Windows):   pip install PySide6 moderngl numpy pillow   (or: pip install -r viewer3d_requirements.txt)
Files:               keep every viewer3d_*.py next to pandora_editor.py (use the patched pandora_editor.py).
Use:                 Pandora > Simple mode > double-click a level name. The same window is reused for the next level.
Manual start:        python viewer3d_main.py --work <unpacked folder> --level 1_0_FortKnox
Errors:              viewer3d_error.log next to pandora_editor.py
Settings:            viewer3d_data\_viewer3d_env.json and _viewer3d_overrides.json
                     next to pandora_editor.py (old copies in the work folder are
                     still read, so existing settings are kept).

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

Waypoints, spawns, hint panels and ladders are deliberately drawn as markers
only - a guessed model there is wrong (the old name-based guesser drew blinky
at every Fort Knox waypoint).

Enemy models
------------
All 711 AI spawns get a character model from the authoritative table in
Templates.CRT: 95 AI template hashes at 0xB60+16*i and a parallel list of 95
NUL-separated model names at 0x90B5, indexed by the same i.  The loader
`viewer3d_model_map.load_ai_model_table()` reads it; the old instance-name
patterns were unreliable and are now only a fallback.  Examples:
  * Fort Knox: 37 of 40 spawns are GFT1 (Goldfinger's guards, w1 P90 Example
    etc.), 3 are Oddjob (Long Bow / Jackal / Mamba templates).
  * e_*_dnl_* in Hong Kong/Midas are mostly GFT1 or Oddjob, not dnl1.
  * e_vtm_* in multiplayer maps are playable characters (Goldfinger, etc.).
The class hash at +0x0C of the 44-byte AI record is behaviour, not model:
0x76fd69ba = CROUCH, 0x04102685 = GUARD, 0x00267729 = ROAM.

AI NEO AGENT in Global.LVL is a spawn-name list, not a template-name list -
matching it positionally to the kind-11 hashes gives 0/95 hash matches.
objects_lp.py resolves template names by hash instead.

Fog colour is read from the level's Script.NSLBC (nslbc_fog.py) when a level opens
and shown in the Environment dialog with its source offset; picking a colour there
overrides it. Fog near/far, sky background and lighting are still manual because the
game stores no per-level values for them in the extracted files.

If a model is missing or wrong, pick the correct one in the Properties panel;
overrides are saved in viewer3d_data\_viewer3d_overrides.json (next to
pandora_editor.py) and always win.

viewer3d_scene_data.py  level loading (numpy only, testable without a GPU)
viewer3d_model_map.py   verified Objects.LP template -> model map (no 3rd-party modules)
viewer3d_camera.py      camera maths and the control scheme
viewer3d_gl.py          OpenGL 3.3 renderer (moderngl), input, picking, move/rotate handles
viewer3d_ui.py          window, panels, toolbars, cube-net gizmo, environment dialog
viewer3d_launcher.py    what Pandora calls (reuses a running viewer through a local socket)
viewer3d_main.py        entry point

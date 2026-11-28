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
All 711 AI spawns get a character model.  Two sources are combined:
  * Templates.CRT: 95 AI template hashes at 0xB60+16*i and a parallel list of
    95 NUL-separated model names at 0x90B5, indexed by the same i.  The loader
    `viewer3d_model_map.load_ai_model_table()` reads it.  This is exact for
    multiplayer `e_vtm_*` spawns and the named character templates.
  * The per-level spawn name in CodeStrings.CHR, which is authoritative for
    story levels (see below).
The class hash at +0x0C of the 44-byte AI record is behaviour, not model:
0x76fd69ba = CROUCH, 0x04102685 = GUARD, 0x00267729 = ROAM.
The full per-level mapping is in ../documentation/Object_Models_By_Level.md.

For story levels the spawn's CHR name is authoritative, not the CRT string:
`e_gfe_*`/`e_guards_*` are GFT1 guards, `e_*_dnl_*` are DNL1, `e_*_spc_*` are
Spectre guards, `e_*_tt_*` are Triad thugs, plus explicit aliases such as
`finalocto` -> octoguard and `TankTop*` -> tank.  This is why Fort Knox has
zero Oddjob models: three spawns there carry Long Bow / Jackal / Mamba
templates whose CRT string is OddJob, but their names mark them as guards.
Multiplayer `e_vtm_*` names carry no role, so those use the CRT table.

AI NEO AGENT in Global.LVL is a spawn-name list, not a template-name list -
matching it positionally to the kind-11 hashes gives 0/95 hash matches.
objects_lp.py resolves template names by hash instead.

Fog colour is read from the level's Script.NSLBC (nslbc_fog.py) when a level opens
and shown in the Environment dialog with its source offset; picking a colour there
overrides it. Fog near/far, sky background and lighting are still manual because the
game stores no per-level values for them in the extracted files.

Placed props also use level-specific models when there is no global name: the
Elevator template picks `elev_hk1` (Fort Knox/Auric/Hong Kong), `cas_elev`
(Midas Casino) or `ck_elev` (Crab Key).

AI spawns never use the placed-prop table: the `TankTop` hash is also a prop
template, but the three `TankTop*` spawns in Crab Key are enemies and use the
`tank` character model (from the spawn-name alias), not the `tanktop` prop.

Properties panel
----------------
Selecting a placement shows every field of its Objects.LP record and makes the
editable ones writable. Hover over a field name or its input box for an
explanation of what it stores and what editing it does. Tooltips use a long
timeout so they stay visible while you read them:

  * Instance name (CodeStrings.CHR), name index h1, and the w0 instance hash.
    Editing the name updates w0 = java31(name) and the CHR string; Save writes
    both Objects.LP and CodeStrings.CHR (each backed up to <work>/_backup).
  * Kind (flags 0/2/8), w1 template hash (with the resolved template name),
    `sent` reference, `typ` flags and the record size.  Changing kind also
    changes the record size and adds/removes the 24 trigger-box bytes.
  * Position X/Y/Z, rotation X/Y/Z in degrees and the three raw 16-bit values
    in the record's tail (4096 steps per turn).
  * For triggers: all six 12.12 box offsets (min/max X/Y/Z relative to the
    placement) and the resulting world-space min/max.
  * Model shown, its source, the three name/template override buttons and the
    viewer-only visibility checkbox.

The `0xCCCC` markers and the trailing zero are format constants: they are shown
but cannot be edited because the Objects.LP parser rejects any other value.
The record size is derived from the kind for the same reason.

Adding and removing level data
------------------------------
The left dock's Objects tab has Add object / Duplicate / Remove buttons:
  * Add asks for kind (object, trigger, AI), template, instance name and position.
    A new name is appended to CodeStrings.CHR, or an unused name slot can be reused.
  * Duplicate copies the selected record with a new name slot.
  * Remove deletes the selected record (with a confirmation).
The Paths tab has Add path / Remove path.  A new path is empty with hash
max(existing)+1 (or 0x1000); removing one also clears that path hash from any
record's `sent` field.  Add/remove edits are marked unsaved until Save.

To make an object follow a patrol path, select it and choose the path in the
Properties field "Points to path".  This writes the path's hash into the
record's `sent` field (FFFFFFFF when "None" is selected).  Save writes Objects.LP
and, if names changed, CodeStrings.CHR.

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

GoldenEye: Rogue Agent (DS) Pandora editor
==========================================
Setup
  1. Install Python 3.8+ from python.org (tick "Add python.exe to PATH"; it includes Tkinter).
  2. Open a command prompt and run:   pip install pillow numpy
  3. Keep all the files from this zip in one folder and double-click  run_pandora_editor.bat
     (or run:  python pandora_editor.py).  If something is wrong the window stays open and
     tells you what; errors are also saved to pandora_editor_error.log next to the tool.
  numpy is only needed for the Import buttons; everything else works without it.

Workflow
  - Pick your .nds, click "Unpack ROM", browse the files on the left.
  - The left panel starts in "Simple" mode, which lists only the level folder names under
    <work folder>/Levels (1_0_FortKnox, MP_Funhouse, ...). Picking a name only highlights it for
    now; press "Advanced" to get the full file tree and open the files inside a level.
  - The filter box narrows whatever list is showing (level names in Simple, file names in Advanced).
  - The progress bar and log box stay pinned to the bottom of the window; if the window is short,
    the file list and the tabs get shorter instead of the log disappearing.
  - The small ↻ button on a tab's toolbar reloads that file from disk; hover it for a tooltip.
  - .nsbmd = models, .nsbtx = shared textures, .cvhm = collision, .roo = room boxes (now editable),
    .lp = enemy/object placements and paths (now viewable and editable), .chr = the level name table
    (now editable), .txt and .lvl = text (now editable), .char/.plt/.ico = pictures and palettes, and
    .crt/.nsbca/.nsbma/.nsbta/.nsbtp/.nslbc/.tex/credits.bin = the Data tab (info + hex view/edit).
    Sounds\Sound_*.sdat and .sd hold
    the music, sound effects and their name/index table (new Sounds tab).
  - Edit, then "Repack ROM..." writes a new .nds. The first edit of any file keeps a copy in
    <work folder>/_backup ; "Revert file to original" restores it.

Textures (Textures tab)
  - "Import PNG..." replaces the selected texture in place, in its original DS format.
  - Size is fixed (the PNG is resized if needed). Palette formats are limited to the colours the
    original palette slot has (shown under the picture), so very colourful art gets quantised.

Models (Model tab)
  Animation playback: clicking an animation file (.nsbca/.nsbta/.nsbtp/.nsbma) in Advanced mode
    plays it on its matching model immediately; opening a .nsbmd also auto-loads its .nsbca from
    the Animations folder (exact name first, then names like auric_door_open for auric_door). The
    dropdown lists every match so you can switch without browsing files; Play/Pause, the Frame
    slider and Stop control playback. "Textured" draws the real textures into the view (needs
    numpy); without it the animated model is shown as flat triangles. Companion NSBTA/NSBTP files
    are applied when present. Keyframes use the nearest stored value (no blending), NSBMA material
    colours and billboard nodes are not applied.
  View controls (all 3D views): drag = rotate, right-drag = pan, wheel = zoom, "Reset view" recentres.
  Export OBJ...            one OBJ object per shape, named shpNNN_<node>.
  Replace with any OBJ...  puts ANY OBJ into the selected model, whatever its object names or where it came
                           from. For each part you choose one of the model's own materials, or "NEW" to give
                           it its own material and append its texture to the level's .nsbtx. Parts left on
                           an existing material can copy the OBJ's image (from its .mtl) into that material's
                           texture slot.
      * "NEW" parts keep their own texture instead of overwriting an existing one: the level's .nsbtx grows
        by a new named texture + palette, and a matching material is added to the .nsbmd. The dialog asks
        which DS texture format and size to use for each NEW part (guessed from the image). Parts without
        a texture image in the .mtl cannot use NEW. Both edited files are backed up before saving.
      * Shape slots are still reused, so a NEW part can share a slot with another part when the model has
        fewer slots than the OBJ has materials; the tool says so. Keeping parts on existing materials still
        lets you cap the number of different textures without growing the files.
      * The dialog also tells you up front whether your OBJ actually brings images (it needs the .mtl next
        to the .obj, with "map_Kd <image>" lines, and the images on disk). After the replacement the log
        says how many textures were imported and into which file.
      * The target's shape slots are reused: unused slots become empty. If the OBJ needs more materials
        than the model has shape slots, the smallest parts are merged (the tool says so).
      * Billboard nodes: some models (e.g. Fort Knox MAINRM) have "BB" nodes whose parts the game turns to
        face the camera (glows, flame cards). New geometry that lands in one of those slots would swing
        around with the camera, so the dialog's "Turn off billboard nodes..." option (on by default)
        makes those nodes static for the parts being replaced.
      * The model's position scale is adjusted automatically if your geometry is bigger/smaller.
      * New geometry is stored as plain triangle lists, so it takes more space than the original.
      * The DS draws roughly 2048 triangles per frame; the tool warns above that.
      * The model's bounding box is not changed, and animations (.nsbca) of the old model still expect
        its old bones - replace static props, or animated models only with similar structure.
  Blender: use the default OBJ axes (Forward -Z, Up Y), and export with "Triangulated Mesh" on if you can.

Collision (Model tab, .cvhm files)
  Levels/... each level folder has .CVHM files next to its .nsbmd models. They hold the simple
  collision geometry the player walks into. Select one to see it in the 3D view; the info line
  shows its mesh names, vertices and triangles.
  Export collision OBJ...          saves the collision mesh as an OBJ, in the same world coordinates
                                   as an exported .nsbmd. One object per internal mesh.
  Generate collision from OBJ...   rebuilds the selected .CVHM from any OBJ (triangulated fan), welds
                                   duplicate vertices, rebuilds the collision tree (BVH) and keeps the
                                   original mesh names and file version.
      * Keep the coordinates (default): the OBJ is stored as it is. Exported models and collision use
        the same level coordinates, and the model importer also keeps the OBJ position, so this is
        what you normally want.
      * Move onto the old collision: only if you built a model around its own origin (0,0,0) and
        want it placed where the old collision was. You can also add a manual X/Y/Z offset.
      * The two-mesh .CVHM files (a low-detail "T_*" and a detailed "H_*" mesh) get the geometry of
        the matching OBJ object when the names match; otherwise the whole OBJ is used for both.
      * The surface flag of triangles that did not move is kept from the old file.
      * Room size: the game only runs collision inside a room's bounding box, which is stored in the
        level's Global.ROO next to the .CVHM files. If your new collision is bigger than the old one,
        the tool automatically grows the matching room's box in Global.ROO (it only ever grows, never
        shrinks), so you can walk on the new parts too. The log says which room was expanded and its
        old/new limits. Without this, collision stops working past the original extents.
  'Export ALL models + collision (OBJ + PNG)...' also writes one <name>_collision.obj for every .CVHM
  in the level, in level coordinates, next to the model OBJs of that level.
  Typical "I swapped a level model and the collision is still the old one" fix:
      1. Replace the .nsbmd as usual (keep the OBJ you used, e.g. level.obj).
      2. Select the matching .CVHM in the same folder.
      3. "Generate collision from OBJ..." -> pick level.obj -> Generate.
      4. Repack and test.
  If several .CVHM files belong to one room, repeat for each. Collision does not need to look good,
  it only has to follow the floor/walls you can reach.

Rooms (Rooms tab, Global.ROO)
  Each level folder has a Global.ROO next to its models: one box per room, in the same level
  coordinates as the models and collision. The game only runs collision while the player is inside
  the room's box, so the box decides where you can walk - that is why a bigger collision model needs
  a bigger box.
  - Select a Global.ROO in the list on the left. The view under the table (and the Model tab) shows
    every box as a wireframe (yellow = selected room, blue = the others) with the room names;
    drag = rotate, right-drag = pan, wheel = zoom.
  - Picking a room centres the view on that room's box; the rotation and zoom stay exactly as they
    were, so you can compare rooms at the same angle and distance.
  - The table lists every room with its flags and Min/Max X/Y/Z. Pick a room (or double-click a row),
    edit the six values under the table and click "Apply box and save".
  - Apply writes the file straight away; the first change keeps the original under
    <work folder>/_backup, and "Revert file to original" restores it. Room names and flags are not
    touched.
  - "Zoom to room" frames one box, "Show all rooms" returns to the whole level.
  - Make a box at least as big as the walkable part of the room. Growing a box is always safe;
    shrinking it can make collision stop working in that room.
  - Repack the ROM to test it.

Placement (Placement tab, Objects.LP)
  Every level folder has an Objects.LP next to the models. It places everything the level
  spawns: objects, weapons, waypoints, hints and trigger boxes (flags 0 and 2), the patrol
  path graph the AI walks (flags 5) and the enemy spawn points with their facing (flags 8).
  Positions are 12.12 fixed point in the same world space as the collision, the models and
  the room boxes, so the existing 3D view shows them in place. The level's collision meshes
  are drawn faintly behind the placements for reference.
  - Select a level's Objects.LP in the list on the left. The view shows every placement as a
    dot (red = enemy, green = object), every trigger box as a wireframe box, and every path
    as lines with node dots. Yellow bracket = selected.
  - "Show" and the name filter narrow the table below the view (Everything / Objects /
    Enemies (AI) / Trigger boxes). Double-click a row (or the view) to centre the camera.
  - The table lists every placement: id, its name from the level's CodeStrings.CHR, the
    template it uses (from Templates.CRT; e.g. "Waypoint", "Barrel", "P90 Example"), and its
    X/Y/Z. Pick one and edit the three coordinates, then "Apply and save".
  - Enemies also store a Facing (a whole number of brads; 4096 brads = 360°, so 2048 = 180°).
    Trigger boxes store a centred box (half X, half Z, height); the position is the box
    centre on the floor.
  - "Add placement…" asks for the kind (Object / AI enemy / Trigger box), the template and a
    name. Either reuse an unused name from the level's list, or type a new one - new names
    are appended to the level's CodeStrings.CHR (backed up like everything else). Enemies
    added this way remember the facing of the placement you had selected, if any.
  - "Duplicate placement…" copies the selected placement with a new id and a new name.
  - "Delete placement" removes the record (a backup is kept).
  - The Paths page lists each path section with its nodes and edges. Pick a node, edit its
    X/Y/Z and "Apply and save"; "Add node" adds one at the camera centre, "Remove node"
    deletes it and its edges, "Link nodes…" joins two nodes, "Unlink" removes the edge. Edge
    lengths are always rewritten correctly when you apply. "Add path" creates a new empty
    path; "Delete path" removes one.
  - "Apply and save" rewrites Objects.LP (or CodeStrings.CHR) with a backup; repack the ROM
    to test it. The log says what changed.
  - The path graph is how the AI walks patrol routes: each node is a spot, each edge is a
    walkable link. Moving nodes is safe. Deleting a path or a node removes it for good;
    "Revert file to original" restores the backup.

If something looks wrong
  - Some parts turn/spin with the camera: those shapes sit under billboard ("BB") nodes. Redo the
    replacement with "Turn off billboard nodes" ticked.
  - Collision stops working past the edge of your new model: generate the collision again for that
    .CVHM (the tool then also grows the room box in Global.ROO). If you only replaced the .nsbmd,
    the .CVHM was not touched at all.
  - Collision works only in part of a room: the room box in Global.ROO is too small for the new
    geometry. Open that level's Global.ROO in the Rooms tab and grow the matching room's box.
  - The replaced model still shows the old textures: the log line says how many textures were imported
    and into which file. If it says "no texture was imported", the OBJ itself does not carry usable
    images: the .mtl must be next to the .obj, must contain "map_Kd <image>" lines, and those images
    must be in the same folder (the tool also looks in subfolders). Re-exporting the model from this
    tool's Export button always produces that setup.
  - Textures changed but the game still shows old ones: when the model has no textures of its own the
    images are written into the model's shared room_textures.nsbtx. That file has to go into the ROM
    together with the .nsbmd (the tool's repack does this automatically).
  - Collision is still the old shape: regenerate the matching .CVHM (see above). Collision is a separate
    file from the .nsbmd model.
  - Repacked ROM does not boot: keep a copy of the original .nds, and test one small change at a time.

Text (Text tab)
  - .txt and .lvl files are now editable in the window. "Save text" writes the file back with the
    original encoding and line endings (CRLF for this game), after keeping a backup copy.
  - .lvl is the level's Global.LVL: it stores resource lists in BEGIN "section" / END blocks. The
    status line lists the sections it found, so you can see where you are while editing.
  - The ↻ button reloads the file from disk (hover it for a tooltip) and throws the unsaved
    changes away. Ctrl+S also saves.
  - Only text characters the original file can store are allowed (ASCII for these files).

Data tab (.char, .plt, .ico, .crt, .nsbca, .nsbma, .nsbta, .nsbtp, .nslbc, .tex, credits.bin)
  Switch the file list to Advanced mode and select one of these files. The Data tab opens with a
  description on the left and three views on the right: Preview, Bytes (hex), and Structured.
  Structured lists the decoded fields with their byte offsets; select a row, edit its value and
  press "Apply edit" to change just that field (a backup is kept in _backup, then Repack the ROM).
  The byte patch box at the bottom remains available for raw edits.
  - ".char" is a 32x32 picture with 16 colours (4 bits per pixel, 8x8 tile order). The colours
    live in the matching ".plt" file. "Save PNG..." exports the preview; "Import PNG..." resizes
    your picture to 32x32, reduces it to 16 colours and rewrites the .char plus its matching .plt
    (index 0 stays transparent). Both originals go into _backup first.
  - ".plt" holds the 16 colours. The preview uses the matching .char. Click one of the colour
    chips, pick a new colour and click "Apply palette and save"; or "Import PNG..." to take the
    16 main colours from a picture. Only the palette changes, not the pixels.
  - ".ico" is a 64x64, 256-colour game-icon portrait (palette first, then the pixels). "Save PNG..."
    exports it; "Import PNG..." rewrites both the palette and the pixels at 64x64.
  - ".crt" is the object template table. The info panel lists the header, how many object and AI
    templates exist, and their readable names (from Global.LVL when it is nearby).
  - ".nsbca" is a NITRO skeletal animation. The info panel lists frames, tracks, each track's
    target node and decoded channels (constant values or sampled keys with first/last values),
    and the referenced rotation matrices.
  - ".nsbma" is a NITRO material animation; ".nsbta" a texture/UV animation; ".nsbtp" a texture
    flipbook. The info panel lists material names, channel flags/key counts, UV keyframes and
    flipbook texture/palette frames. The exact GPU property of each material channel is inferred.
    These files are fully decoded in nitro_anim.py and documented in documentation/Animation_Formats.md.
    Byte-level edits are still possible; the viewer does not play animations.
  - ".nslbc" is a level's event script. The layout (four hash tables + bytecode + strings) is decoded
    and the info panel lists the callback blocks and disassembles their first instructions into
    readable operations (e.g. TIMER, SETPROP COLLIDE, USE_STR "dWhiteToRed", STR "message").
    Full disassembly: "python nslbc_dis.py <work> <level>". See documentation/NSLBC_Format.md.
    Operation semantics of a few opcode families and ~1000 name hashes remain unknown; patch
    bytes with care (changing code/string sizes requires fixing callback offsets).
  - ".tex" (Menus/menu.tex) is a bank of 122 LZSS-compressed, palettised 8-bpp images. The Preview
    tab shows the images one at a time: use the ◀ Prev / Next ▶ buttons or the Left/Right arrow
    keys (when the Data tab's Preview page is active) to browse, and "Save this PNG..." to keep
    the current image. The "Structured" tab lists every block with its offset, raw size and
    dimensions taken from menu.bin's block records. The large menu screens are assembled from a
    8x8 tileset plus a u16 tilemap (32 tiles wide), so the text and background line up correctly.
    "Export PNGs..." writes them all to a folder. The hex view and patch box still work on raw bytes.
  - "credits.bin" is the end-credits string table (382 strings); the info panel lists them.
  - The "Bytes (hex)" page shows 8 KB at a time. Type an offset (hex) and press Go, or use the
    8 KB page buttons. The view is read-only.
  - "Byte patch" writes bytes at an offset (both hexadecimal). Example: offset 200, bytes
    00 FF 7A. The first edit of a file keeps the original in _backup; "Revert file to original"
    restores it. Repack the ROM to test a change.

Names (Names (.CHR) tab, CodeStrings.CHR)
  Every level folder has a CodeStrings.CHR next to Objects.LP. It is the level's name table:
  placements in Objects.LP store an id (h1) that indexes into it, so one name can be shared.
  - Select a CodeStrings.CHR in the list on the left. The table lists every name with its id,
    the byte offset where it starts, its stored length (including the terminating NUL) and how
    many Objects.LP placements use it ("?" when that level has no readable Objects.LP).
  - Pick a row, edit the name and click "Apply and save". Every id stays the same, so the
    placements keep pointing at the right entry even when the text gets longer or shorter.
  - "Add name..." appends a new name with the next free id. Use it when you need a new name for
    the Placement tab. Existing entries are never reordered or deleted, because that would
    change the ids stored in Objects.LP.
  - Names are latin-1 text (letters, digits, punctuation and common accents); a NUL byte is not
    allowed. A backup is kept in _backup and "Revert file to original" restores the table.
    Repack the ROM to test a change.

Sounds (Sounds tab, Sounds\Sound_*.sd and .sdat)
  The game's audio lives in two files in the Sounds folder. Sound_*.sdat is a standard NITRO
  sound archive: sequenced music (SSEQ), a table of sound effects (SSAR), the sound banks (SBNK)
  that tie effects to samples, and wave archives (SWAR) with the actual samples (all IMA-ADPCM).
  Sound_*.sd is the name table: each entry is { hash of the sound's name, sound index, 0xCCCC }.
  The names themselves are in the .sdat, and the hash is the same java31 hash used in other files.
  - Select either file in the list. Opening one automatically reads its companion, so you see both
    the sounds and the hash table. The first page lists every named sound with its kind, sample,
    rate and length; the second page ("IDs (.sd)") lists every hash/index entry.
  - "Play" renders the selected sound to a temporary WAV and plays it with Windows sound; "Stop"
    cuts it off. Sound effects play their sample exactly. Music is rendered by a simple SSEQ
    player: it understands notes, rests, tempo, program/volume/pan changes and loops, but it is an
    approximation, not the game's own mixer. On other systems use Export WAV to listen elsewhere.
  - "Export WAV..." saves the selected sound (up to 2 minutes); "Export all WAVs..." writes one WAV
    per sound into a folder (up to 30 seconds each). Useful for finding a sound by ear.
  - "Replace with WAV..." re-encodes your WAV as IMA-ADPCM at the game's sample rate and puts it in
    that sound's sample slot. The WAV may have any rate/channels; it is downmixed to mono and
    resampled so the pitch stays the same. The .sdat grows and the other samples are left in place.
    Pick a short WAV: the game's samples are small. A backup is kept in _backup.
  - "Rename..." changes a name in the .sdat's SYMB block and updates its hash in the .sd. The new
    name may be any length: the old string is left in place as unused bytes and the new one is
    appended to the name table, with all offsets (and the FAT) rewritten. A backup is kept.
  - On the "IDs (.sd)" page, pick an entry and edit its hash (hex) and index, then "Apply and
    save". Changing the index redirects that sound to another one of the same kind (e.g. make a
    weapon use a different effect); changing the hash changes which name the game looks up.
  - "Revert file to original" restores both the .sd and the .sdat from _backup. Repack the ROM to
    test a change in an emulator.

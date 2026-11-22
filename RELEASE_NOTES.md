# MOOOV_player 1.1

## New in 1.1: Playlists
- A playlist panel on the left (P or Cmd/Ctrl+L): all the builds for a review in one player, no digging through folders.
- Each row shows a thumbnail, the name, frame range, duration and the number of comments.
- Add files by dropping them on the panel, with the new "Add to playlist" zone when dropping on the window, or from File → Add to Playlist.
- Drag rows to set the order before the client review; right-click to remove, reveal in Finder or add to the timeline.
- Several playlists: create, rename, switch, clear, delete. Search inside a playlist.
- ‹ › buttons and Page Up / Page Down go to the previous and next build.
- "Play All on Timeline" puts the whole playlist into edit mode.
- Playlists are kept between sessions; thumbnails are cached.

- EXR layers (AOVs): when a file has more than one layer (diffuse, specular, Z, cryptomatte …), a layer list appears next to the channel list at the bottom. The picked layer plays with the cache, exposure, channels and layer compare; one-channel layers (Z) show in grey, use exposure to bring depth into range. Info shows which layer you are on. The choice stays when you switch to another shot that has the same layer.
- The Comments panel opens only when you open it (Tab, Cmd/Ctrl+P or its tab on the right). A new drawing or comment no longer pops it open: you type the comment in a bubble right on the frame.

## Also in 1.1: layers — stack clips, blend them, compare them
- Drop files on the window: the middle zone asks "Add to edit" (top) or "Add as layer" (bottom). Or use File → Add Layers… (Cmd/Ctrl+Shift+C) and pick several sequences at once.
- Each clip goes on top of the stack and gets its own strip on the timeline, above A (or above the edit's clips). The top layer is at the top. There is no separate panel: everything is on the strip.
  - Right on the strip: blend mode (click it for the list: Normal, Add, Screen, Multiply, Overlay, Soft Light, Lighten, Darken, Difference), an opacity slider in 5% steps (drag it, the picture follows live), an eye to hide the layer, and × to remove it.
  - Icons on the strip: **i** shows the clip's metadata (path, format, resolution, bit depth, frames, size, look, offset); the half-circle turns the split compare on and off; the sun opens brightness (EV, per layer), opacity, blend mode, time offset and Reset.
  - ≡ in the middle opens the layer's menu. Right-click the strip for the same menu: brightness, opacity, offset, split, move up / down, swap with A, reset split.
  - Drag a strip left or right to shift the layer in time. It snaps to the start and to the playhead, and the tooltip shows the frame it starts on. Before its start the layer is not shown. The offset is also in the sun menu: type it, "Start here" (at the playhead) or "Sync" (back to 0).
  - Drag a strip up or down to change the order. A is a strip too: drag it up into the stack, and the clip that ends up at the bottom becomes the new A (it takes over the comments and the timeline). Each clip keeps its own blend mode, opacity, brightness and visibility when it moves.
  - A has the same controls: mode, opacity and the eye (hidden or faded A shows the viewer background under the layers). × on A removes it, and the layer above becomes A.
- Sequences with alpha (PNG, EXR, TIFF) lie over the layers below with their own transparency, like passes in a comp.
- Split shows a layer on one side of a wipe. Drag the yellow knob to move it, and drag either dot beside it to rotate it (Shift gives 15° steps). Double-click to reset. New layers come in with the split off; turn it on with the half-circle icon on the strip.
- All layers play and step in sync, frame by frame. Exposure, channels and view apply to all of them.
- A shorter layer holds its last frame. A layer's brightness adds to the global exposure.
- Edit → Swap A and Top Layer, Reset Split, Remove All Layers.

## Also in 1.1: In / Out — watch just the piece you need
- Press I to set the In point and O to set the Out point (or use the [▸ ◂] buttons next to Loop). Click a lit button again to clear that point; Alt+X clears both.
- Playback and the loop stay inside In–Out. Pressing Play outside the range starts from In.
- On the timeline the piece is highlighted between yellow brackets: drag a bracket to move it, right-click the track to set or clear In and Out.
- Home / End go to In / Out first, a second press goes to the very first / last frame.
- File info moved to Shift+I.

## Also in 1.1: reference images in comments
- New Image tool in the drawing panel: click the frame (or paste with Cmd/Ctrl+V, or drop an image file) to add a reference picture to the comment.
- Drag the picture to move it, drag its corners to scale it; Delete removes it.
- The picture belongs to its comment: it fades in and out with the ghost frames like the drawing, and goes into the PDF report.
- The picture is stored inside the comment file, so it travels with the shot.
- Opacity for drawings and reference images: 25%, 50%, 75% or 100%, next to the stroke size.
- Frame step buttons are now centred in their circles.
- Rounded drop-down lists and menus no longer show square corners.

# MOOOV_player 1.0

A fast, quiet player for VFX review: videos and image sequences, drawing on frames, comments and PDF reports, all in one window.

Developed by Ivaniuchenko Roman for [mooov.team](https://mooov.team).

## Playback
- Videos (MOV, MP4, MXF, MKV, AVI, WebM and more) and image sequences (EXR, PNG, JPG, TIFF, HDR, WebP).
- Open a single frame and the whole sequence is picked up, or open a folder.
- Frames are read ahead in parallel and kept in RAM: after the first pass, scrubbing and playback are instant.
- Speed from 0.25× to 4×, loop, frame-accurate stepping.
- Frame number in the source's own numbering (e.g. 1001) plus timecode.

## Viewing
- Exposure (EV), channels R / G / B / A / Luma, sRGB or Linear view, half resolution for heavy EXRs.
- Mattes 2.39, 2.35, 2:1, 1.85, 16:9, 4:3, 1:1, 4:5, 9:16, solid or see-through.
- Zoom, pan, 1:1 and fit to window.
- Info overlay (Shift+I): name, path, format, resolution, bit depth, duration, size. Click the path to open the folder.

## Drawing and comments
- Brush, arrow, rectangle and ellipse in several colours and sizes.
- Every drawing becomes a numbered comment on its frame.
- Hover a number on the frame to read the comment. Click it to write or edit the comment right on the image.
- Comments panel with cards: click to jump to the frame, double-click to edit.
- Nearby comments show as ghosts for a few frames around.
- Comments are saved automatically next to the media file, in a small .mooov.json file.

## PDF report
- One page per comment: number, frame, timecode, author, date, file, the text and the frame with the drawing.
- Dark layout in the app's style, ready to send to the team.

## Edit mode
- Drop several files onto the window and choose "Add to timeline".
- Clips play one after another on one timeline.
- Drag clips to change their order; right-click to remove one.
- Comments stay with their own clip and frame.

## Comfort
- Drag and drop: drop a file onto the window and choose "Open" or "Add to timeline".
- Space always plays and stops; almost everything has a hotkey.
- Opens from Finder or Explorer with "Open With", and from the folder context menu on Windows.
- One calm design: dark, round controls, the yellow accent of MOOOV.

## Platforms
- macOS 11+ (Apple Silicon), Windows 10/11 (64-bit).

## Known limits
- The order of clips in edit mode is not saved between sessions.
- The app is not signed: on first launch, macOS and Windows ask to confirm opening it.

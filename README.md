# sp-proteus-packager

A [Substance Painter](https://www.adobe.com/products/substance3d-painter.html) plugin that builds a Penumbra-compatible `.pmp` modpack with a [Proteus](https://github.com/solona-m/proteus) sidecar by reading your layer stack folder structure.

---

## How it works

The plugin reads the **layer folder hierarchy** inside your texture sets:

```
Texture Set (e.g. "Body")
└── Style          ← top-level folder  →  Penumbra option group
    ├── Roses      ← sub-folder        →  Penumbra option
    ├── Stripes    ← sub-folder        →  Penumbra option
    └── Fishnet    ← sub-folder        →  Penumbra option
```

When you click **Export PMP** the plugin:

1. Hides all layers.
2. For each option folder, shows only that folder and runs SP's export to a temporary directory using your configured output template.
3. Restores original layer visibility.
4. Either zips everything into `<ProjectName>.pmp` in your output directory, merges the exported options into an existing `.pmp` (when **Existing PMP** is set), or copies the mod folder straight into Penumbra (when **Install to Penumbra** is checked).

Each option is exported in isolation so the textures contain only that option's artwork.

---

## Install

### 1. Plugin

Copy `proteus_packager.py` into your Substance Painter plugins folder:

```
%USERPROFILE%\Documents\Adobe\Adobe Substance 3D Painter\python\plugins\
```

Then in SP: **Python > Reload All Plugins** (or restart SP).

The panel appears as a dockable widget titled **Proteus Packager**.

### 2. Proteus output template

Copy `Proteus.spexp` into your Substance Painter user assets folder:

```
%USERPROFILE%\Documents\Adobe\Adobe Substance 3D Painter\assets\export-presets\
```

The plugin scans this folder on startup and makes the template available in the **Export Preset** dropdown automatically.

---

## Layer stack structure

- **Top-level group folders** become Penumbra option groups.
- **Sub-folders within those** become the individual options.
- Non-group layers (fill layers, paint layers) at the group level are treated as shared base content — they stay visible for every option export.
- Texture sets that don't have any group folders are ignored.

Multiple texture sets work fine — the plugin walks all texture sets and builds the union of group/option names.

---

## Settings panel

| Setting | Description |
|---|---|
| **Author** | Written into `meta.json` and `Proteus/metadata.json`. |
| **Output Dir** | Where the `.pmp` is saved. Defaults to the SP project's directory. Ignored in merge mode and when **Install to Penumbra** is checked. |
| **Install to Penumbra** | When checked, copy the mod folder directly into your Penumbra mod root (loose, not zipped) instead of producing a `.pmp`. Penumbra picks it up when you refresh the mod (under edit mod). The mod root is auto-detected from XIVLauncher's `Penumbra.json` config — no manual path required. |
| **Existing PMP** | Optional. Leave blank to build a fresh pack. If set to an existing `.pmp`, the newly exported groups/options are merged into that pack (see [Merging](#merging-into-an-existing-pmp)). |
| **Colorset metadata** | Optional. Select an existing Proteus `metadata.json`; exported options reuse its `ColorTableRows` (matched by option name) instead of the default white colorset. Takes precedence over any in-project [Colorset sub-folder](#colorset-sub-folder). |
| **Export Preset** | Dropdown listing all available SP output templates. Select **[User] Proteus** when using the included template. Use **↻** to refresh after installing new templates. |
| **Material Game Paths** | One Penumbra game path per line. All listed materials receive the overlay. |
| **Preset / Load** | Quick-load a saved set of material paths. Ships with a **Bibo+** preset. |
| **Suffix mappings** | How exported filenames map to overlay types. Comma-separated, longest match wins. |
| **Mutually exclusive options** | Checked → Penumbra group type `Single`; unchecked → `Multi`. |
| **Export PMP** | Manual trigger. |

### Suffix mappings

Exported filenames are matched to overlay types by suffix (longest match wins). The Proteus template outputs bare channel names, which are also recognised automatically without any suffix configuration:

| Auto-detected bare names | Type |
|---|---|
| `diffuse`, `color`, `basecolor`, `albedo` | Diffuse |
| `normal`, `normalgl`, `normal_opengl` | Normal |
| `index`, `indexcolor`, `id` | Index |
| `mask` | Mask |

For other templates you can configure custom suffixes (e.g. `_d`, `_n`, `_id`, `_m`).

### Bibo+ preset material paths

```
chara/human/c0201/obj/body/b0001/material/v0001/mt_c0201b0001_bibo.mtrl
chara/human/c0401/obj/body/b0001/material/v0001/mt_c0401b0001_bibo.mtrl
chara/human/c1401/obj/body/b0001/material/v0001/mt_c1401b0001_bibo.mtrl
chara/human/c1401/obj/body/b0001/material/v0001/mt_c1401b0101_bibo.mtrl
chara/human/c1801/obj/body/b0001/material/v0001/mt_c1801b0001_bibo.mtrl
chara/human/c1601/obj/body/b0001/material/v0001/mt_c1601b0001_bibo.mtrl
```

Edit the list before exporting to add/remove races or to target a different mesh body.

---

## Colorset sub-folder

You can author per-option dye rows directly inside your SP layer stack — no external `metadata.json` required.

If an option folder contains a sub-folder literally named **`Colorset`** (case-insensitive), each fill layer inside it becomes one entry in that option's `ColorTableRows`:

- The **layer name** identifies the affected row, e.g. `16A`, `16B`, `1a` (case-insensitive). Valid rows are 1–16; sub-row is `A` or `B`. Layers with names that don't match this pattern are silently skipped.
- The fill layer's **BaseColor** → `Diffuse` (sRGB hex string).
- The fill layer's **Emissive** channel → `Emissive` (float intensity 0–1, derived from the channel color's luminance). Toggle the Emissive channel on the layer to use it.
- The fill layer's **Opacity** channel slider → `Opacity` (integer adjustment, −100…0). Slider at 1.0 → `0` (no change); slider at 0.5 → `-50` (fade 50% toward transparent); slider at 0.0 → `-100` (fully transparent). Toggle the Opacity channel on the layer to use it.

Channels that aren't active on the layer are simply omitted from that sub-row. Layers `16A` and `16B` are coalesced into a single `Row 16` entry with both `SubRowA` and `SubRowB` populated.

```
Body
└── Style                  ← group
    └── Roses              ← option
        ├── Colorset       ← (case-insensitive) — drives ColorTableRows
        │   ├── 1A         ← fill layer, base color = dye for row 1 sub-row A
        │   ├── 16A        ← fill layer, base color = dye for row 16 sub-row A
        │   └── 16B        ← fill layer, base color = dye for row 16 sub-row B
        ├── roses_diffuse  ← regular paint/fill content (exported as textures)
        └── roses_normal
```

Colorset layers are **hidden automatically during that option's texture export** so the dye-preview colors never bleed into the diffuse PNG. Their pre-export visibility is restored when the export finishes.

**Precedence:** if the **Colorset metadata** picker is also set and supplies rows for the same option, the picker wins and the in-project Colorset folder is ignored for that option. If neither is configured, the option falls back to a single white row 16.

---

## Generated `.pmp` structure

```
MyMod.pmp  (renamed .zip)
├── meta.json
├── default_mod.json
├── group_001_style.json
└── Proteus/
    ├── metadata.json
    └── Style/
        ├── Roses/
        │   ├── diffuse.png
        │   └── normal.png
        ├── Stripes/
        │   └── diffuse.png
        └── Fishnet/
            ├── diffuse.png
            └── normal.png
```

Every exported PNG receives a small `tEXt` chunk stamped with its option's path (e.g. `Style/Roses`). Penumbra auto-deduplicates identical files, which would otherwise collapse pixel-identical index/mask textures across options into a single file and break per-option overlays. The stamp keeps the pixel data identical but the file bytes distinct, so Penumbra leaves each option's textures alone.

---

## Merging into an existing .pmp

Set the **Existing PMP** field to fold a fresh export into a modpack you
already have instead of producing a standalone pack:

- The selected `.pmp` is **overwritten in place** (the Output Dir is ignored).
- Everything already in the pack is preserved — existing `group_*.json`,
  `default_mod.json`, and any existing `Proteus/` options stay intact.
- The pack's `Name`/`Author` (from its `meta.json`) are kept, so Penumbra
  treats it as the same mod being updated.
- New options are appended to the matching group, creating the group's
  `group_NNN_*.json` and/or `Proteus/metadata.json` if the pack doesn't
  have them yet (works on a plain non-Proteus Penumbra pack too).
- If the pack already has an option with the same name in that group, the
  existing option is **replaced** — its textures and metadata entry are
  overwritten with the freshly exported version (stale texture files for that
  option are cleared first).

## Settings persistence

Settings are saved to `proteus_packager.ini` in the plugins folder. They survive SP restarts.

---

## Requirements

- Adobe Substance 3D Painter 10+ (requires `substance_painter.layerstack` Python API)
- [Proteus](https://github.com/solona-m/proteus) Dalamud plugin installed in FFXIV
- [Penumbra](https://github.com/xivdev/Penumbra) mod framework

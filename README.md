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
2. For each option folder, shows only that folder and runs SP's export to a temporary directory using your configured export preset.
3. Restores original layer visibility.
4. Packages everything into `<ProjectName>.pmp`.

Each option is exported in isolation so the textures contain only that option's artwork, not a composite of all of them.

---

## Install

Copy `proteus_packager.py` into the Substance Painter plugins folder:

```
%LOCALAPPDATA%\Adobe\Adobe Substance 3D Painter\plugins\
```

Then in SP: **Python > Reload All Plugins** (or restart SP).

The panel appears as a dockable widget titled **Proteus Packager**.

---

## Layer stack structure

- **Top-level group folders** become Penumbra option groups.
- **Sub-folders within those** become the individual options.
- Non-group layers (fill layers, paint layers) at the group level are treated as shared base content — they stay visible for every option export.
- Texture sets that don't have any group folders are ignored.

Multiple texture sets (e.g. separate UV islands) work fine — the plugin walks all texture sets and builds the union of group/option names.

---

## Settings panel

| Setting | Description |
|---|---|
| **Author** | Written into `meta.json` and `Proteus/metadata.json`. |
| **Output Dir** | Where the `.pmp` is saved. Defaults to the SP project's directory. |
| **Export Preset** | The **exact name** of an SP export preset to use when exporting each option. Must match an existing preset in SP. |
| **Material Game Paths** | One Penumbra game path per line. All listed materials receive the overlay. |
| **Preset / Load** | Quick-load a saved set of material paths. Ships with a **Bibo+** preset. |
| **Suffix mappings** | How exported filenames map to overlay types. Comma-separated, longest match wins. |
| **Mutually exclusive options** | Checked → Penumbra group type `Single`; unchecked → `Multi`. |
| **Auto-package when SP export finishes** | After any SP texture export completes, automatically trigger the layer-toggle packaging cycle. |
| **Export PMP** | Manual trigger. |
| **Log / Clear** | Timestamped activity log. |

### Finding the export preset name

In Substance Painter: **File > Export Textures** — the preset name appears in the **Config** dropdown at the top of the export dialog. Copy it exactly (it is case-sensitive).

### Default suffix mappings

| Type | Default suffix |
|---|---|
| Diffuse | `_d` |
| Normal | `_n` |
| Index | `_id` |
| Mask | `_m` |

Longest match wins: a file ending in `_id` is classified as Index before Diffuse even though `_d` is a substring.

### Bibo+ preset material paths

```
chara/human/c0201/obj/body/b0001/material/v0001/mt_c0201b0001_bibo.mtrl
chara/human/c0401/obj/body/b0001/material/v0001/mt_c0401b0001_bibo.mtrl
chara/human/c1401/obj/body/b0001/material/v0001/mt_c1401b0001_bibo.mtrl
chara/human/c1801/obj/body/b0001/material/v0001/mt_c1801b0001_bibo.mtrl
chara/human/c1601/obj/body/b0001/material/v0001/mt_c1601b0001_bibo.mtrl
```

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
        │   ├── Body_d.png
        │   └── Body_n.png
        ├── Stripes/
        │   └── Body_d.png
        └── Fishnet/
            ├── Body_d.png
            └── Body_n.png
```

---

## Settings persistence

Settings are saved to `proteus_packager.ini` in the same directory as the plugin file.

---

## Requirements

- Adobe Substance 3D Painter (recent version with `substance_painter.layerstack` Python API)
- [Proteus](https://github.com/solona-m/proteus) Dalamud plugin installed in FFXIV
- [Penumbra](https://github.com/xivdev/Penumbra) mod framework

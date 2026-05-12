# sp-proteus-packager

A [Substance Painter](https://www.adobe.com/products/substance3d-painter.html) plugin that automatically builds a Penumbra-compatible `.pmp` modpack with a [Proteus](https://github.com/solona-m/proteus) sidecar whenever you export textures.

---

## What it does

After a SP texture export the plugin:

1. Groups your exported PNGs by texture set name (`GroupName/OptionName` convention).
2. Creates a `Proteus/metadata.json` sidecar wiring each texture file to one or more FFXIV material game paths.
3. Builds a complete Penumbra mod structure (`meta.json`, `default_mod.json`, `group_NNN_*.json`).
4. Zips everything and renames it to `<ProjectName>.pmp` — ready to drag into Penumbra.

---

## Install

Copy `proteus_packager.py` into the Substance Painter plugins folder:

```
%LOCALAPPDATA%\Adobe\Adobe Substance 3D Painter\plugins\
```

Then in SP: **Python > Reload All Plugins** (or restart SP).

The panel appears as a dockable widget titled **Proteus Packager**.

---

## Texture set naming

The plugin reads option grouping from the texture set name using a `/` separator:

| Texture set name | Penumbra group | Penumbra option |
|---|---|---|
| `Style/Roses` | Style | Roses |
| `Style/Stripes` | Style | Stripes |
| `Gloves/None` | Gloves | None |
| `BareLegs` | *(project name)* | BareLegs |

Texture sets that share the same `Group/Option` path merge their textures into one overlay descriptor.

---

## Settings panel

| Setting | Description |
|---|---|
| **Author** | Written into `meta.json` and `Proteus/metadata.json`. |
| **Output Dir** | Where the `.pmp` is saved. Defaults to the SP project's directory. |
| **Material Game Paths** | One Penumbra game path per line. Overlays are applied to every listed material. |
| **Preset / Load** | Quick-load a saved set of material paths. Ships with a **Bibo+** preset. |
| **Suffix mappings** | Maps exported file suffixes to overlay types. Accepts comma-separated values. |
| **Mutually exclusive options** | Checked → Penumbra group type `Single`; unchecked → `Multi`. |
| **Auto-export on SP texture export** | Package automatically whenever SP finishes exporting textures. |
| **Export PMP** | Manual trigger — scans the output directory for PNGs matching known texture sets. |
| **Log / Clear** | Timestamped activity log. |

### Default suffix mappings

| Type | Default suffix |
|---|---|
| Diffuse | `_d` |
| Normal | `_n` |
| Index | `_id` |
| Mask | `_m` |

Suffixes are matched against the exported filename stem (longest match wins, so `_id` takes priority over `_d`).

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
├── group_002_gloves.json
└── Proteus/
    ├── metadata.json
    ├── Style/
    │   ├── Roses/
    │   │   ├── textureset_d.png
    │   │   └── textureset_n.png
    │   └── Stripes/
    │       └── textureset_d.png
    └── Gloves/
        └── None/
            └── textureset_d.png
```

---

## Settings persistence

Settings are saved to `proteus_packager.ini` in the same directory as the plugin file, so they survive SP restarts.

---

## Requirements

- Adobe Substance 3D Painter (any recent version with Python plugin support)
- [Proteus](https://github.com/solona-m/proteus) Dalamud plugin installed in FFXIV
- [Penumbra](https://github.com/xivdev/Penumbra) mod framework

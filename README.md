# sp-proteus-packager

A [Substance Painter](https://www.adobe.com/products/substance3d-painter.html) plugin that builds a Penumbra-compatible `.pmp` modpack with a [Proteus](https://github.com/solona-m/proteus) sidecar by reading your layer stack folder structure.


A [Rue Mannequin](https://github.com/solona-m/substance-proteus-packager/blob/main/Rue%20Mannequin.fbx) is an fbx use can use for creating a substance painter project to paint on for bibo-uv bodies if you're doing skin overlays. The overlays will work on all bibo-based bodies. If you prefer to paint on Neolithe, there's a mannequin in the [neolithe download folder](https://neolithe.carrd.co/#download)

After you create your textures, it's recommended to adjust your default colorsets using the /proteus ui in game. Then reexport your mod from penumbra. You can also point the substance plugin to your metadata.json in the penumbra mod directory to load any colorset values before substance packs the mod.

---

## How it works

The plugin reads the **layer folder hierarchy** inside your texture sets in substance:

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
4. Packages everything into `<ProjectName>.pmp`.

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
| **Output Dir** | Where the `.pmp` is saved. Defaults to the SP project's directory. |
| **Export Preset** | Dropdown listing all available SP output templates. Select **[User] Proteus** when using the included template. Use **↻** to refresh after installing new templates. |
| **Material Game Paths** | One Penumbra game path per line. All listed materials receive the overlay. |
| **Preset / Load** | Quick-load a saved set of material paths. Ships with a **Bibo+** preset. |
| **Suffix mappings** | How exported filenames map to overlay types. Comma-separated, longest match wins. |
| **Mutually exclusive options** | Checked → Penumbra group type `Single`; unchecked → `Multi`. |
| **Auto-package when SP export finishes** | After any SP texture export completes, automatically trigger the layer-toggle packaging cycle. |
| **Export PMP** | Manual trigger. |
| **Log / Clear** | Timestamped activity log. |

### Suffix mappings

Exported filenames are matched to overlay types by suffix (longest match wins). The Proteus template outputs bare channel names, which are also recognised automatically without any suffix configuration:

| Auto-detected bare names | Type |
|---|---|
| `diffuse`, `color`, `basecolor`, `albedo` | Diffuse |
| `normal`, `normalgl`, `normal_opengl` | Normal |
| `index`, `indexcolor`, `id` | Index |
| `mask` | Mask |

For other templates you can configure custom suffixes (e.g. `_d`, `_n`, `_id`, `_m`).

### Bibo+ preset material paths for all races

```
chara/human/c0201/obj/body/b0001/material/v0001/mt_c0201b0001_bibo.mtrl
chara/human/c0401/obj/body/b0001/material/v0001/mt_c0401b0001_bibo.mtrl
chara/human/c1401/obj/body/b0001/material/v0001/mt_c1401b0001_bibo.mtrl
chara/human/c1401/obj/body/b0101/material/v0001/mt_c1401b0101_bibo.mtrl
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
        │   ├── diffuse.png
        │   └── normal.png
        ├── Stripes/
        │   └── diffuse.png
        └── Fishnet/
            ├── diffuse.png
            └── normal.png
```

---

## Settings persistence

Settings are saved to `proteus_packager.ini` in the plugins folder. They survive SP restarts.

---

## Requirements

- Adobe Substance 3D Painter 10+ (requires `substance_painter.layerstack` Python API)
- [Proteus](https://github.com/solona-m/proteus) Dalamud plugin installed in FFXIV
- [Penumbra](https://github.com/xivdev/Penumbra) mod framework

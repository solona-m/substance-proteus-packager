"""
Proteus Packager — Substance Painter plugin.

Builds a Penumbra-compatible .pmp sidecar with a Proteus/metadata.json
by reading the layer stack folder hierarchy:

  Top-level folder  →  Penumbra option group  (e.g. "Style")
  Sub-folder        →  Penumbra option        (e.g. "Roses", "Stripes")

When "Export PMP" is clicked the plugin:
  1. Hides all layers.
  2. For each option sub-folder, shows only that folder and exports textures
     to a per-option temp directory using the configured SP export preset.
  3. Restores original layer visibility.
  4. Packages everything into <ProjectName>.pmp.

Install: copy this file to
  %LOCALAPPDATA%\\Adobe\\Adobe Substance 3D Painter\\plugins\\
"""

import os
import re
import json
import shutil
import struct
import tempfile
import zlib
import configparser
from pathlib import Path

import substance_painter.ui
import substance_painter.project
import substance_painter.textureset
import substance_painter.export
import substance_painter.event
import substance_painter.resource

try:
    import substance_painter.layerstack as _ls
    _HAS_LAYERSTACK = True
except ImportError:
    _ls = None
    _HAS_LAYERSTACK = False

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui


PLUGIN_NAME = "Proteus Packager"
_INI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proteus_packager.ini")

_BIBO_PLUS_PATHS = "\n".join([
    "chara/human/c0201/obj/body/b0001/material/v0001/mt_c0201b0001_bibo.mtrl",
    "chara/human/c0401/obj/body/b0001/material/v0001/mt_c0401b0001_bibo.mtrl",
    "chara/human/c1401/obj/body/b0001/material/v0001/mt_c1401b0001_bibo.mtrl",
    "chara/human/c1401/obj/body/b0001/material/v0001/mt_c1401b0101_bibo.mtrl",
    "chara/human/c1801/obj/body/b0001/material/v0001/mt_c1801b0001_bibo.mtrl",
    "chara/human/c1601/obj/body/b0001/material/v0001/mt_c1601b0001_bibo.mtrl",
])

_plugin_instance = None


def start_plugin():
    global _plugin_instance
    _plugin_instance = ProteusPackagerPlugin()


def close_plugin():
    global _plugin_instance
    if _plugin_instance:
        _plugin_instance.cleanup()
        _plugin_instance = None


class ProteusPackagerPlugin:
    def __init__(self):
        self._widget = None
        self._author = ""
        self._output_dir = ""
        self._existing_pmp = ""
        self._colorset_meta = ""
        self._export_preset = ""
        self._mutually_exclusive = True
        self._install_to_penumbra = False
        self._material_paths = _BIBO_PLUS_PATHS
        self._suffixes = {
            "Diffuse": ["_d"],
            "Normal":  ["_n"],
            "Index":   ["_id"],
            "Mask":    ["_m"],
        }
        self._presets: dict[str, str] = {"Bibo+": _BIBO_PLUS_PATHS}

        self._load_settings()
        self._create_ui()
        self._connect_events()
        # Defer the network check so the dock paints first.
        QtCore.QTimer.singleShot(0, self._check_for_plugin_update)

    # ── Settings ──────────────────────────────────────────────────────────────

    def _load_settings(self):
        cfg = configparser.RawConfigParser()
        cfg.read(_INI_FILE, encoding="utf-8")

        g = cfg["General"] if "General" in cfg else {}
        self._author = g.get("Author", "")
        self._output_dir = g.get("OutputDir", "")
        self._existing_pmp = g.get("ExistingPmp", "")
        self._colorset_meta = g.get("ColorsetMeta", "")
        self._export_preset = g.get("ExportPreset", "")
        self._mutually_exclusive = cfg.getboolean("General", "MutuallyExclusive", fallback=True)
        self._install_to_penumbra = cfg.getboolean("General", "InstallToPenumbra", fallback=False)

        s = cfg["Suffixes"] if "Suffixes" in cfg else {}
        self._suffixes = {
            "Diffuse": _split_csv(s.get("Diffuse", "_d")),
            "Normal":  _split_csv(s.get("Normal",  "_n")),
            "Index":   _split_csv(s.get("Index",   "_id")),
            "Mask":    _split_csv(s.get("Mask",    "_m")),
        }

        m = cfg["MaterialPaths"] if "MaterialPaths" in cfg else {}
        self._material_paths = m.get("Default", _BIBO_PLUS_PATHS).replace("\\n", "\n")

        self._presets = {"Bibo+": _BIBO_PLUS_PATHS}
        if "Presets" in cfg:
            for k, v in cfg["Presets"].items():
                if k.lower() != "bibo+":
                    self._presets[k] = v.replace("\\n", "\n")

    def _save_settings(self):
        cfg = configparser.RawConfigParser()
        cfg["General"] = {
            "Author": self._author,
            "OutputDir": self._output_dir,
            "ExistingPmp": self._existing_pmp,
            "ColorsetMeta": self._colorset_meta,
            "ExportPreset": self._export_preset,
            "MutuallyExclusive": str(self._mutually_exclusive),
            "InstallToPenumbra": str(self._install_to_penumbra),
        }
        cfg["Suffixes"] = {k: ",".join(v) for k, v in self._suffixes.items()}
        cfg["MaterialPaths"] = {"Default": self._material_paths.replace("\n", "\\n")}
        user_presets = {k: v.replace("\n", "\\n") for k, v in self._presets.items()
                        if k != "Bibo+"}
        if user_presets:
            cfg["Presets"] = user_presets
        with open(_INI_FILE, "w", encoding="utf-8") as f:
            cfg.write(f)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _create_ui(self):
        self._widget = QtWidgets.QWidget()
        self._widget.setWindowTitle(PLUGIN_NAME)
        root = QtWidgets.QVBoxLayout(self._widget)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Update banner — hidden unless a newer plugin.py exists on GitHub.
        self._update_btn = QtWidgets.QPushButton()
        self._update_btn.setVisible(False)
        self._update_btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self._update_btn.setStyleSheet(
            "QPushButton { text-align: left; color: #f7c948; padding: 6px 10px; "
            "border: 1px solid #f7c948; border-radius: 3px; "
            "background: transparent; }"
            "QPushButton:hover { background: rgba(247, 201, 72, 0.15); }"
            "QPushButton:disabled { color: #6aa84f; border-color: #6aa84f; }"
        )
        self._update_btn.clicked.connect(self._apply_plugin_update)
        root.addWidget(self._update_btn)

        # Author
        author_row = QtWidgets.QHBoxLayout()
        author_row.addWidget(QtWidgets.QLabel("Author"))
        self._author_edit = QtWidgets.QLineEdit(self._author)
        author_row.addWidget(self._author_edit)
        root.addLayout(author_row)

        # Output Dir
        out_row = QtWidgets.QHBoxLayout()
        out_row.addWidget(QtWidgets.QLabel("Output Dir"))
        self._output_edit = QtWidgets.QLineEdit(self._output_dir)
        out_row.addWidget(self._output_edit)
        browse_btn = QtWidgets.QPushButton("...")
        browse_btn.setFixedWidth(30)
        browse_btn.clicked.connect(self._browse_output)
        out_row.addWidget(browse_btn)
        root.addLayout(out_row)

        # Install to Penumbra (copy mod folder into Penumbra root instead of
        # zipping). Penumbra's mod root is read from XIVLauncher's
        # Penumbra.json at export time — no manual path needed.
        pen_row = QtWidgets.QHBoxLayout()
        self._install_penumbra_check = QtWidgets.QCheckBox("Install to Penumbra")
        self._install_penumbra_check.setChecked(self._install_to_penumbra)
        self._install_penumbra_check.setToolTip(
            "When checked, copy the mod folder into Penumbra's mod root "
            "(auto-detected from XIVLauncher) instead of producing a .pmp."
        )
        pen_row.addWidget(self._install_penumbra_check)
        pen_row.addStretch()
        root.addLayout(pen_row)

        # Existing PMP (merge target)
        pmp_row = QtWidgets.QHBoxLayout()
        pmp_row.addWidget(QtWidgets.QLabel("Existing PMP"))
        self._existing_pmp_edit = QtWidgets.QLineEdit(self._existing_pmp)
        self._existing_pmp_edit.setToolTip(
            "Leave blank to build a new pack. If set, new options are merged "
            "into this pack and it is overwritten in place."
        )
        pmp_row.addWidget(self._existing_pmp_edit)
        pmp_browse_btn = QtWidgets.QPushButton("...")
        pmp_browse_btn.setFixedWidth(30)
        pmp_browse_btn.clicked.connect(self._browse_existing_pmp)
        pmp_row.addWidget(pmp_browse_btn)
        root.addLayout(pmp_row)

        # Colorset metadata.json (pull ColorTableRows from an existing pack)
        cset_row = QtWidgets.QHBoxLayout()
        cset_row.addWidget(QtWidgets.QLabel("Colorset metadata"))
        self._colorset_meta_edit = QtWidgets.QLineEdit(self._colorset_meta)
        self._colorset_meta_edit.setToolTip(
            "Optional. Select an existing Proteus metadata.json; exported "
            "options reuse its ColorTableRows (matched by option name) "
            "instead of the default white colorset."
        )
        cset_row.addWidget(self._colorset_meta_edit)
        cset_browse_btn = QtWidgets.QPushButton("...")
        cset_browse_btn.setFixedWidth(30)
        cset_browse_btn.clicked.connect(self._browse_colorset_meta)
        cset_row.addWidget(cset_browse_btn)
        root.addLayout(cset_row)

        # Export Preset
        preset_row = QtWidgets.QHBoxLayout()
        preset_row.addWidget(QtWidgets.QLabel("Export Preset"))
        self._export_preset_combo = QtWidgets.QComboBox()
        self._export_preset_combo.setEditable(True)  # allow typing if list is empty
        preset_row.addWidget(self._export_preset_combo)
        refresh_btn = QtWidgets.QPushButton("↻")
        refresh_btn.setFixedWidth(28)
        refresh_btn.setToolTip("Refresh preset list from SP")
        refresh_btn.clicked.connect(self._refresh_export_presets)
        preset_row.addWidget(refresh_btn)
        root.addLayout(preset_row)
        self._refresh_export_presets(select=self._export_preset)

        # Material Game Paths
        mat_label_row = QtWidgets.QHBoxLayout()
        mat_label_row.addWidget(QtWidgets.QLabel("Material Game Paths"))
        mat_label_row.addStretch()
        mat_label_row.addWidget(QtWidgets.QLabel("Preset:"))
        self._mat_preset_combo = QtWidgets.QComboBox()
        for name in self._presets:
            self._mat_preset_combo.addItem(name)
        mat_label_row.addWidget(self._mat_preset_combo)
        load_btn = QtWidgets.QPushButton("Load")
        load_btn.clicked.connect(self._load_mat_preset)
        mat_label_row.addWidget(load_btn)
        root.addLayout(mat_label_row)

        self._material_edit = QtWidgets.QPlainTextEdit(self._material_paths)
        self._material_edit.setFixedHeight(110)
        root.addWidget(self._material_edit)

        # Suffix mappings
        root.addWidget(QtWidgets.QLabel("Suffix mappings"))
        suffix_grid = QtWidgets.QGridLayout()
        self._suffix_edits: dict[str, QtWidgets.QLineEdit] = {}
        for col, (key, label) in enumerate([
            ("Diffuse", "Diffuse"),
            ("Normal",  "Normal"),
            ("Index",   "Index"),
            ("Mask",    "Mask"),
        ]):
            suffix_grid.addWidget(QtWidgets.QLabel(label), 0, col * 2)
            edit = QtWidgets.QLineEdit(",".join(self._suffixes[key]))
            edit.setFixedWidth(60)
            suffix_grid.addWidget(edit, 0, col * 2 + 1)
            self._suffix_edits[key] = edit
        root.addLayout(suffix_grid)

        self._mutex_check = QtWidgets.QCheckBox("Mutually exclusive options (Single select)")
        self._mutex_check.setChecked(self._mutually_exclusive)
        root.addWidget(self._mutex_check)

        export_btn = QtWidgets.QPushButton("Export PMP")
        export_btn.clicked.connect(self._on_export_pmp_clicked)
        root.addWidget(export_btn)

        preview_btn = QtWidgets.QPushButton("Generate previews")
        preview_btn.setToolTip(
            "For each option in the layer stack, switch to 3D-only view, "
            "show its Colorset layers, screenshot the viewport, then save "
            "individual PNGs to <OutputDir>/<ModName>/ plus a combined grid."
        )
        preview_btn.clicked.connect(self._on_generate_previews_clicked)
        root.addWidget(preview_btn)

        substance_painter.ui.add_dock_widget(self._widget)
        self._connect_ui_autosave()

    def _connect_ui_autosave(self):
        self._author_edit.editingFinished.connect(self._read_ui_settings)
        self._output_edit.editingFinished.connect(self._read_ui_settings)
        self._existing_pmp_edit.editingFinished.connect(self._read_ui_settings)
        self._colorset_meta_edit.editingFinished.connect(self._read_ui_settings)
        self._export_preset_combo.currentIndexChanged.connect(self._read_ui_settings)
        self._mutex_check.stateChanged.connect(self._read_ui_settings)
        self._install_penumbra_check.stateChanged.connect(self._read_ui_settings)
        for edit in self._suffix_edits.values():
            edit.editingFinished.connect(self._read_ui_settings)

    def _browse_output(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self._widget, "Select Output Directory", self._output_edit.text()
        )
        if d:
            self._output_edit.setText(d)

    def _browse_existing_pmp(self):
        f, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._widget, "Select Existing PMP", self._existing_pmp_edit.text(),
            "Proteus Mod Pack (*.pmp)"
        )
        if f:
            self._existing_pmp_edit.setText(f)
            self._read_ui_settings()

    def _browse_colorset_meta(self):
        f, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._widget, "Select Proteus metadata.json",
            self._colorset_meta_edit.text(), "Proteus metadata (*.json)"
        )
        if f:
            self._colorset_meta_edit.setText(f)
            self._read_ui_settings()

    def _load_mat_preset(self):
        name = self._mat_preset_combo.currentText()
        if name in self._presets:
            self._material_edit.setPlainText(self._presets[name])

    def _refresh_export_presets(self, select: str = ""):
        """Repopulate the export preset combo. select is a saved resource URL."""
        presets = _list_export_presets(log=self._log)
        current_url = select or self._export_preset_combo.currentData() or ""
        self._export_preset_combo.blockSignals(True)
        self._export_preset_combo.clear()
        for display_name, url in presets:
            self._export_preset_combo.addItem(display_name, userData=url)
        # Restore by URL; fall back to text match; fall back to editable text
        idx = self._export_preset_combo.findData(current_url)
        if idx >= 0:
            self._export_preset_combo.setCurrentIndex(idx)
        elif current_url:
            self._export_preset_combo.setEditText(current_url)
        elif not presets:
            self._export_preset_combo.setPlaceholderText("No presets found — type URL manually")
        self._export_preset_combo.blockSignals(False)

    def _log(self, msg: str):
        print(f"[ProteusPackager] {msg}")

    # ── Events ────────────────────────────────────────────────────────────────

    def _connect_events(self):
        for event_type in (
            substance_painter.event.ProjectOpened,
            substance_painter.event.ProjectCreated,
        ):
            substance_painter.event.DISPATCHER.connect(event_type, self._on_project_opened)

    def _on_project_opened(self, _ev=None):
        self._refresh_export_presets(select=self._export_preset)

    # ── Update check ──────────────────────────────────────────────────────────

    def _check_for_plugin_update(self):
        """Compare the published plugin file's Last-Modified timestamp on
        GitHub against this file's on-disk mtime. If GitHub is newer, reveal
        the update banner."""
        remote_mtime = _remote_last_modified()
        if remote_mtime is None:
            return
        try:
            local_mtime = os.path.getmtime(__file__)
        except Exception:
            return
        # 60s grace so trivial drift / clock skew doesn't fire spuriously.
        if remote_mtime > local_mtime + 60:
            self._update_btn.setText("⚠ Update available — click to install")
            self._update_btn.setVisible(True)
            self._log(f"Update available: {_PLUGIN_REMOTE_URL}")

    def _apply_plugin_update(self):
        content = _fetch_remote_file(_PLUGIN_REMOTE_URL)
        if content is None:
            self._update_btn.setText("Update failed — see Python console")
            return
        try:
            target = os.path.abspath(__file__)
            tmp = target + ".tmp"
            with open(tmp, "wb") as f:
                f.write(content)
            os.replace(tmp, target)
        except Exception as exc:
            self._update_btn.setText(f"Update failed: {exc}")
            self._log(f"Plugin update write failed: {exc}")
            return
        self._update_btn.setText("✓ Updated — Python > Reload All Plugins")
        self._update_btn.setEnabled(False)
        self._log(f"Plugin file updated: {target}")

    # ── Button handler ────────────────────────────────────────────────────────

    def _on_export_pmp_clicked(self):
        self._read_ui_settings()
        if not substance_painter.project.is_open():
            self._log("No project open.")
            return
        self._build_pmp()

    def _on_generate_previews_clicked(self):
        self._read_ui_settings()
        if not substance_painter.project.is_open():
            self._log("No project open.")
            return
        self._generate_previews()

    def _generate_previews(self):
        if not _HAS_LAYERSTACK:
            self._log("Layerstack API not available — cannot generate previews.")
            return
        all_ts = list(substance_painter.textureset.all_texture_sets())
        structure, ts_node_map, _colorset_layers = _discover_structure(all_ts)
        if not structure:
            self._log("No group/option folder hierarchy found in the layer stack.")
            return

        mod_name = substance_painter.project.name() or "UnnamedMod"
        out_dir = self._resolve_output_dir()
        os.makedirs(out_dir, exist_ok=True)

        # Wipe + recreate the per-option folder so stale files / cached
        # thumbnails can't shadow the fresh run.
        per_opt_dir = os.path.join(out_dir, mod_name)
        if os.path.isdir(per_opt_dir):
            shutil.rmtree(per_opt_dir, ignore_errors=True)
        os.makedirs(per_opt_dir, exist_ok=True)

        _set_viewport_3d_only(self._log)

        saved_vis = _save_visibility(ts_node_map)
        tiles: list = []
        try:
            for group in structure.keys():
                for option in structure[group]:
                    _set_visibility_for_option(ts_node_map, group, option)
                    _show_colorset_layers_for_option(ts_node_map, group, option)
                    pm = _grab_viewport_pixmap(self._log)
                    if pm is None:
                        self._log(f"  [preview] Skipped {group}/{option} — no pixmap.")
                        continue
                    sg = _safe_path_component(group)
                    so = _safe_path_component(option)
                    group_dir = os.path.join(per_opt_dir, sg)
                    os.makedirs(group_dir, exist_ok=True)
                    out = os.path.join(group_dir, so + ".png")
                    if pm.save(out, "PNG"):
                        tiles.append((f"{group} / {option}", pm))
                    else:
                        self._log(f"  [preview] Could not save {out}")
        finally:
            _restore_visibility(ts_node_map, saved_vis)

        self._log(f"Saved {len(tiles)} per-option preview(s) to {per_opt_dir}")
        if tiles:
            grid_path = os.path.join(per_opt_dir, f"{mod_name}_preview.png")
            if os.path.isfile(grid_path):
                try:
                    os.remove(grid_path)
                except Exception:
                    pass
            _composite_preview_grid(tiles, grid_path, log=self._log)

    def _read_ui_settings(self):
        self._author = self._author_edit.text().strip()
        self._output_dir = self._output_edit.text().strip()
        self._existing_pmp = self._existing_pmp_edit.text().strip()
        self._colorset_meta = self._colorset_meta_edit.text().strip()
        # Prefer the stored URL (item data); fall back to typed text for manual entry
        self._export_preset = (self._export_preset_combo.currentData()
                               or self._export_preset_combo.currentText().strip())
        self._material_paths = self._material_edit.toPlainText().strip()
        self._mutually_exclusive = self._mutex_check.isChecked()
        self._install_to_penumbra = self._install_penumbra_check.isChecked()
        for key, edit in self._suffix_edits.items():
            self._suffixes[key] = _split_csv(edit.text())
        self._save_settings()

    # ── Core packaging ────────────────────────────────────────────────────────

    def _build_pmp(self):
        if not _HAS_LAYERSTACK:
            self._log("substance_painter.layerstack not available — update Substance Painter.")
            return

        if not self._export_preset:
            self._log("Export Preset is required. Enter your SP export preset name in the settings.")
            return

        # Resolve file:/// URIs to resource:// by importing as a session resource
        resolved_preset = _resolve_preset_url(self._export_preset, log=self._log)

        mod_name = substance_painter.project.name() or "UnnamedMod"
        author = self._author or "Unknown"
        mat_paths = [p.strip() for p in self._material_paths.splitlines() if p.strip()]
        group_type = "Single" if self._mutually_exclusive else "Multi"
        all_ts = list(substance_painter.textureset.all_texture_sets())
        ts_names = [ts.name() for ts in all_ts]

        # 1. Discover structure from layer stack
        structure, ts_node_map, colorset_layers = _discover_structure(all_ts)
        if not structure:
            self._log("No group/option folder hierarchy found in the layer stack. "
                      "Add a top-level group folder (group) with sub-folders (options).")
            return

        self._log("Groups: " + ", ".join(
            f"{g}({', '.join(opts)})" for g, opts in structure.items()
        ))

        # 2. Build temp directories
        merge_path = self._existing_pmp
        merge = bool(merge_path)
        if merge and not (merge_path.lower().endswith(".pmp")
                          and os.path.isfile(merge_path)):
            self._log(f"Existing PMP not found or not a .pmp file: {merge_path}")
            return
        if merge and self._install_to_penumbra:
            self._log("Existing PMP is set — ignoring 'Install to Penumbra' and "
                      "updating the pack in place.")

        tmpdir = tempfile.mkdtemp(prefix="proteus_pmp_")
        export_root = os.path.join(tmpdir, "_exports")  # intermediate SP exports
        pmp_root = os.path.join(tmpdir, "_pmp")         # final .pmp content
        proteus_dir = os.path.join(pmp_root, "Proteus")
        os.makedirs(export_root)

        if merge:
            os.makedirs(pmp_root)
            try:
                shutil.unpack_archive(merge_path, pmp_root, format="zip")
            except Exception as exc:
                self._log(f"Failed to open existing PMP: {exc}")
                shutil.rmtree(tmpdir, ignore_errors=True)
                return
            os.makedirs(proteus_dir, exist_ok=True)
            meta_path = os.path.join(pmp_root, "meta.json")
            if os.path.isfile(meta_path):
                try:
                    with open(meta_path, encoding="utf-8") as f:
                        em = json.load(f)
                    mod_name = em.get("Name") or mod_name
                    author = em.get("Author") or author
                except Exception as exc:
                    self._log(f"Could not read existing meta.json: {exc}")
        else:
            os.makedirs(proteus_dir)

        # 3. Save current layer visibility
        saved_vis = _save_visibility(ts_node_map)

        try:
            groups_order = list(structure.keys())
            option_groups_meta = []          # fresh-mode Proteus OptionGroups
            option_images: dict = {}         # (group, option) -> rel image path

            colorset_map: dict = {}
            if self._colorset_meta:
                if os.path.isfile(self._colorset_meta):
                    colorset_map = _load_colorset_map(self._colorset_meta, log=self._log)
                    self._log(f"Loaded colorsets from {self._colorset_meta}")
                else:
                    self._log(f"Colorset metadata not found: {self._colorset_meta}")

            # Merge-mode: load existing Proteus/Penumbra content to append to
            proteus_meta = None
            pg_by_name: dict = {}            # group name -> Proteus OptionGroup
            grp_files: dict = {}             # group name -> [json path, data]
            touched_group_files: dict = {}   # json path -> data to rewrite
            max_idx = 0
            if merge:
                pm_path = os.path.join(proteus_dir, "metadata.json")
                if os.path.isfile(pm_path):
                    try:
                        with open(pm_path, encoding="utf-8") as f:
                            proteus_meta = json.load(f)
                    except Exception as exc:
                        self._log(f"Could not read existing Proteus/metadata.json: {exc}")
                if not isinstance(proteus_meta, dict):
                    proteus_meta = {"FormatVersion": 1, "Name": mod_name,
                                    "Author": author, "OptionGroups": []}
                proteus_meta.setdefault("OptionGroups", [])
                for og in proteus_meta["OptionGroups"]:
                    pg_by_name[og.get("PenumbraGroupName")] = og
                for gp in sorted(Path(pmp_root).glob("group_*.json")):
                    try:
                        with open(gp, encoding="utf-8") as f:
                            gdata = json.load(f)
                    except Exception:
                        continue
                    m = re.match(r"group_(\d+)_", gp.name)
                    if m:
                        max_idx = max(max_idx, int(m.group(1)))
                    gname = gdata.get("Name")
                    if gname is not None:
                        grp_files[gname] = [str(gp), gdata]

            for group in groups_order:
                if merge:
                    og = pg_by_name.get(group)
                    if og is None:
                        og = {"PenumbraGroupName": group, "Options": []}
                        proteus_meta["OptionGroups"].append(og)
                        pg_by_name[group] = og
                    og.setdefault("Options", [])

                    if group in grp_files:
                        gpath, gdata = grp_files[group]
                    else:
                        max_idx += 1
                        safe = re.sub(r"[^\w]", "_", group).lower()
                        gpath = os.path.join(pmp_root, f"group_{max_idx:03d}_{safe}.json")
                        gdata = {"Version": 0, "Name": group, "Description": "",
                                 "Image": "", "Page": 0, "Priority": 0,
                                 "Type": group_type, "DefaultSettings": 0,
                                 "Options": []}
                        grp_files[group] = [gpath, gdata]
                    gdata.setdefault("Options", [])
                    touched_group_files[gpath] = gdata

                    proteus_opts = og["Options"]
                    penumbra_opts = gdata["Options"]
                    taken = {o.get("Name") for o in proteus_opts}
                    taken |= {o.get("Name") for o in penumbra_opts}
                    if "None" not in taken:
                        proteus_opts.append(_none_proteus_option())
                        penumbra_opts.append(_none_penumbra_option())
                        taken.add("None")
                else:
                    proteus_opts = []
                    penumbra_opts = None

                for option in structure[group]:
                    self._log(f"Exporting {group}/{option}…")

                    # Show only this option; hide everything else
                    _set_visibility_for_option(ts_node_map, group, option)

                    # Ask SP to export
                    opt_export_dir = os.path.join(export_root, group, option)
                    os.makedirs(opt_export_dir, exist_ok=True)
                    exported_files = self._do_sp_export(ts_names, opt_export_dir, resolved_preset)

                    if not exported_files:
                        self._log(f"  No files produced — skipping {group}/{option}")
                        continue

                    final_name = option

                    # Copy recognised files into the Proteus sidecar tree.
                    # On merge, wipe any existing folder for this option so a
                    # replace can't leave stale textures behind.
                    rel_subdir = f"{group}/{final_name}"
                    abs_subdir = os.path.join(proteus_dir, group, final_name)
                    if merge and os.path.isdir(abs_subdir):
                        shutil.rmtree(abs_subdir, ignore_errors=True)
                    os.makedirs(abs_subdir, exist_ok=True)

                    overlay: dict = {}
                    overlay["MaterialGamePath"] = mat_paths[0] if len(mat_paths) == 1 else mat_paths

                    for fpath in exported_files:
                        tex_type = self._detect_type(fpath)
                        if tex_type is None:
                            self._log(f"  Skipping unrecognised: {Path(fpath).name}")
                            continue
                        fname = Path(fpath).name
                        _png_copy_stamped(fpath, os.path.join(abs_subdir, fname),
                                          rel_subdir)
                        overlay[tex_type] = f"{rel_subdir}/{fname}"
                        self._log(f"  {tex_type}: {rel_subdir}/{fname}")

                    color_rows = (colorset_map.get((group, option))
                                  or colorset_map.get((None, option)))
                    if color_rows is not None and self._colorset_meta:
                        self._log(f"  Colorset reused for '{option}'")
                    if color_rows is None:
                        entries = colorset_layers.get((group, option), [])
                        if entries:
                            self._log(f"  Colorset folder found for '{option}' ({len(entries)} layer(s))")
                            color_rows = _rows_from_colorset_layers(entries, self._log)
                            if color_rows:
                                self._log(f"  Colorset produced {len(color_rows)} row(s) for '{option}'")
                    if not color_rows:
                        color_rows = [{"Row": 16, "SubRowA": {"Diffuse": "#FFFFFF"}}]

                    pro_entry = {
                        "Name": final_name,
                        "Overlays": [overlay],
                        "ColorTableRows": color_rows,
                    }
                    image_rel = _maybe_copy_preview_into_pack(
                        self._resolve_output_dir(), mod_name,
                        group, final_name, pmp_root)
                    if image_rel:
                        option_images[(group, final_name)] = image_rel
                    if merge:
                        replaced = _upsert_option(proteus_opts, pro_entry)
                        _upsert_option(penumbra_opts, {
                            "Name": final_name, "Description": "",
                            "Image": image_rel,
                            "Files": {}, "FileSwaps": {}, "Manipulations": []})
                        if replaced:
                            self._log(f"  Replaced existing option '{final_name}'")
                    else:
                        proteus_opts.append(pro_entry)

                if not merge:
                    option_groups_meta.append({
                        "PenumbraGroupName": group,
                        "Options": [_none_proteus_option()] + proteus_opts,
                    })

            if merge:
                # Update the Proteus sidecar + only the touched group files;
                # preserve the pack's existing meta.json / default_mod.json.
                _write_json(os.path.join(proteus_dir, "metadata.json"), proteus_meta)
                for gpath, gdata in touched_group_files.items():
                    _write_json(gpath, gdata)
                if not os.path.isfile(os.path.join(pmp_root, "meta.json")):
                    _write_json(os.path.join(pmp_root, "meta.json"), {
                        "FileVersion": 3, "Name": mod_name, "Author": author,
                        "Description": "", "Version": "1.0", "Website": "",
                        "ModTags": [],
                    })
                if not os.path.isfile(os.path.join(pmp_root, "default_mod.json")):
                    _write_json(os.path.join(pmp_root, "default_mod.json"),
                                {"Files": {}, "Swaps": {}, "Manipulations": []})

                # Zip and atomically overwrite the source .pmp in place
                archive = shutil.make_archive(
                    os.path.join(tmpdir, "_merged"), "zip", pmp_root)
                tmp_dest = merge_path + ".tmp"
                if os.path.exists(tmp_dest):
                    os.remove(tmp_dest)
                shutil.move(archive, tmp_dest)
                os.replace(tmp_dest, merge_path)
                self._log(f"Done (merged in place): {merge_path}")
            else:
                # Proteus/metadata.json
                _write_json(os.path.join(proteus_dir, "metadata.json"), {
                    "FormatVersion": 1,
                    "Name": mod_name,
                    "Author": author,
                    "OptionGroups": option_groups_meta,
                })

                # meta.json
                _write_json(os.path.join(pmp_root, "meta.json"), {
                    "FileVersion": 3, "Name": mod_name, "Author": author,
                    "Description": "", "Version": "1.0", "Website": "", "ModTags": [],
                })

                # default_mod.json
                _write_json(os.path.join(pmp_root, "default_mod.json"),
                            {"Files": {}, "Swaps": {}, "Manipulations": []})

                # group_NNN_{name}.json
                for idx, group in enumerate(groups_order, start=1):
                    opts = [_none_penumbra_option()] + [
                        {"Name": o, "Description": "",
                         "Image": option_images.get((group, o), ""),
                         "Files": {}, "FileSwaps": {}, "Manipulations": []}
                        for o in structure[group]]
                    safe = re.sub(r"[^\w]", "_", group).lower()
                    _write_json(os.path.join(pmp_root, f"group_{idx:03d}_{safe}.json"), {
                        "Version": 0, "Name": group, "Description": "", "Image": "",
                        "Page": 0, "Priority": 0, "Type": group_type,
                        "DefaultSettings": 0, "Options": opts,
                    })

                if self._install_to_penumbra:
                    penumbra_root = _read_penumbra_root_from_xivlauncher(self._log)
                    if not penumbra_root:
                        self._log("Install to Penumbra is checked but the "
                                  "Penumbra mod directory could not be read "
                                  "from XIVLauncher.")
                        return
                    if not os.path.isdir(penumbra_root):
                        self._log(f"Penumbra root directory not found: "
                                  f"{penumbra_root}")
                        return
                    target = os.path.join(penumbra_root, mod_name)
                    if os.path.isdir(target):
                        shutil.rmtree(target)
                    shutil.copytree(pmp_root, target)
                    self._log(f"Installed to Penumbra: {target}")
                    if _reload_penumbra_mod(target, name=mod_name, log=self._log):
                        self._log(f"Reloaded in Penumbra: {mod_name}")
                else:
                    # ZIP pmp_root → .pmp
                    out_dir = self._resolve_output_dir()
                    os.makedirs(out_dir, exist_ok=True)
                    zip_base = os.path.join(out_dir, mod_name)
                    archive = shutil.make_archive(zip_base, "zip", pmp_root)
                    pmp_path = zip_base + ".pmp"
                    if os.path.exists(pmp_path):
                        os.remove(pmp_path)
                    os.rename(archive, pmp_path)
                    self._log(f"Done: {pmp_path}")

        except Exception as exc:
            self._log(f"Error: {exc}")
        finally:
            _restore_visibility(ts_node_map, saved_vis)
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _do_sp_export(self, ts_names: list[str], output_dir: str,
                      preset_url: str = "") -> list[str]:
        config = {
            "exportShaderParams": False,
            "exportPath": output_dir,
            "exportList": [{"rootPath": ts} for ts in ts_names],
            "defaultExportPreset": preset_url or self._export_preset,
            "exportParameters": [{"parameters": {"paddingAlgorithm": "infinite"}}],
        }
        try:
            result = substance_painter.export.export_project_textures(config)
            files: list[str] = []
            for file_list in result.textures.values():
                files.extend(file_list)
            return files
        except Exception as exc:
            self._log(f"  SP export error: {exc}")
            return []

    def _detect_type(self, fpath: str):
        stem = Path(fpath).stem
        # Configured suffix match (longest suffix wins)
        for key in ("Diffuse", "Normal", "Index", "Mask"):
            for sfx in sorted(self._suffixes[key], key=len, reverse=True):
                if stem.endswith(sfx):
                    return key
        # Fallback: bare channel name (presets that output "diffuse.png" etc.)
        _BARE = {
            "diffuse": "Diffuse", "color": "Diffuse", "colour": "Diffuse",
            "basecolor": "Diffuse", "albedo": "Diffuse",
            "normal": "Normal", "normalgl": "Normal", "normal_opengl": "Normal",
            "index": "Index", "indexcolor": "Index", "id": "Index",
            "mask": "Mask",
        }
        return _BARE.get(stem.lower())

    def _resolve_output_dir(self) -> str:
        if self._output_dir:
            return self._output_dir
        fp = substance_painter.project.file_path()
        if fp:
            return str(Path(fp).parent)
        return tempfile.gettempdir()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def cleanup(self):
        for event_type in (
            substance_painter.event.ProjectOpened,
            substance_painter.event.ProjectCreated,
        ):
            substance_painter.event.DISPATCHER.disconnect(event_type, self._on_project_opened)
        if self._widget:
            substance_painter.ui.delete_ui_element(self._widget)
            self._widget = None


# ── Viewport screenshot + grid composite ──────────────────────────────────────

_VIEWPORT_RENDER_WAIT_MS = 600


def _wait_for_viewport(ms: int = _VIEWPORT_RENDER_WAIT_MS):
    """Block for `ms` milliseconds while the Qt event loop keeps running.
    Lets SP finish baking textures and repainting the viewport after a layer
    visibility change. Cheap processEvents() loops aren't enough — they return
    immediately when the queue is empty even if the GL render is still
    in-flight."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _grab_viewport_pixmap(log=None):
    """Return a QPixmap of just SP's 3D viewport region, or None on failure.

    Strategy: find the viewport widget (SP exposes it as a plain QWidget) to
    determine its on-screen rect, but capture pixels via QScreen.grabWindow()
    against the desktop. That avoids two problems with widget.grab(): it can't
    capture OpenGL surfaces, and SP recreates the viewport widget during the
    render loop ('Internal C++ object already deleted'). We read the widget's
    geometry into plain ints and drop the ref before calling grabWindow."""
    _wait_for_viewport()
    try:
        mw = substance_painter.ui.get_main_window()
    except Exception as exc:
        if log:
            log(f"  [preview] get_main_window failed: {exc}")
        return None
    if mw is None:
        return None
    screen = mw.screen() if hasattr(mw, "screen") else None
    if screen is None:
        screen = QtGui.QGuiApplication.primaryScreen()
    if screen is None:
        return None

    try:
        # Pick the largest visible plain QWidget — that's the 3D viewport
        # container in SP. We only read geometry, never call grab() on it.
        candidates = []
        for w in mw.findChildren(QtWidgets.QWidget):
            if type(w) is not QtWidgets.QWidget:
                continue
            if not w.isVisible():
                continue
            sz = w.size()
            if sz.width() < 200 or sz.height() < 200:
                continue
            origin = w.mapToGlobal(QtCore.QPoint(0, 0))
            candidates.append((sz.width() * sz.height(),
                               origin.x(), origin.y(),
                               sz.width(), sz.height()))
        if candidates:
            candidates.sort(reverse=True)
            _, x, y, gw, gh = candidates[0]
        else:
            if log:
                log("  [preview] No viewport candidate found; grabbing whole window.")
            origin = mw.mapToGlobal(QtCore.QPoint(0, 0))
            x, y, gw, gh = origin.x(), origin.y(), mw.width(), mw.height()

        pm = screen.grabWindow(0, x, y, gw, gh)
        if pm.isNull():
            return None
        # Crop the viewport's own chrome:
        #   top ~6%   — brush toolbar + Material dropdown (top-right)
        #   bottom ~5% — MASK indicator (bottom-left) + nav compass (bottom-right)
        # Percentages, so this scales with widget size / DPI / SP version.
        W, H = pm.width(), pm.height()
        top_crop = int(H * 0.06)
        bot_crop = int(H * 0.05)
        if H - top_crop - bot_crop > 100:
            pm = pm.copy(0, top_crop, W, H - top_crop - bot_crop)
        return pm
    except RuntimeError as exc:
        if log:
            log(f"  [preview] Viewport grab failed: {exc}")
        return None


def _composite_preview_grid(tiles, out_path, tile_w=256, label_h=24, log=None):
    """tiles: list of (label:str, QPixmap). Save a near-square grid PNG to
    out_path. Returns True on success."""
    if not tiles:
        return False
    import math
    cols = max(1, math.ceil(math.sqrt(len(tiles))))
    rows = math.ceil(len(tiles) / cols)
    cell_h = tile_w + label_h
    canvas = QtGui.QPixmap(cols * tile_w, rows * cell_h)
    canvas.fill(QtGui.QColor(32, 32, 32))
    painter = QtGui.QPainter(canvas)
    try:
        font = painter.font()
        font.setPointSize(9)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(220, 220, 220))
        for i, (label, pm) in enumerate(tiles):
            col, row = i % cols, i // cols
            x, y = col * tile_w, row * cell_h
            scaled = pm.scaled(tile_w, tile_w,
                               QtCore.Qt.KeepAspectRatio,
                               QtCore.Qt.SmoothTransformation)
            ox = x + (tile_w - scaled.width()) // 2
            oy = y + (tile_w - scaled.height()) // 2
            painter.drawPixmap(ox, oy, scaled)
            painter.drawText(QtCore.QRect(x, y + tile_w, tile_w, label_h),
                             QtCore.Qt.AlignCenter, label)
    finally:
        painter.end()
    ok = canvas.save(out_path, "PNG")
    if log:
        log(f"Preview saved: {out_path}" if ok else f"Preview save failed: {out_path}")
    return ok


# ── Layer stack helpers ───────────────────────────────────────────────────────

def _discover_structure(all_ts: list):
    """
    Walk the layer stack for every texture set and collect the group/option
    folder hierarchy.

    Returns:
        structure:        dict[group_name → list[option_name]]  (insertion order)
        ts_node_map:      dict[ts_name → {"_top": [...], group_name: {"_node": node,
                                                                       option_name: node}}]
        colorset_layers:  dict[(group, option) → list[(row, subrow, node)]]
    """
    structure: dict[str, list[str]] = {}
    ts_node_map: dict[str, dict] = {}
    colorset_layers: dict = {}

    for ts in all_ts:
        ts_name = ts.name()
        top_nodes = list(_ls.get_root_layer_nodes(ts.get_stack()))
        ts_data: dict = {"_top": top_nodes}
        ts_node_map[ts_name] = ts_data

        for node in top_nodes:
            if not _is_group(node):
                continue
            group_name = _node_name(node)
            if not group_name:
                continue

            if group_name not in structure:
                structure[group_name] = []

            ts_data.setdefault(group_name, {})["_node"] = node

            for child in _node_children(node):
                if not _is_group(child):
                    continue
                opt_name = _node_name(child)
                if not opt_name:
                    continue
                if opt_name not in structure[group_name]:
                    structure[group_name].append(opt_name)
                ts_data[group_name][opt_name] = child

                # One level deeper: scan for a "Colorset" sub-folder of this option
                for sub in _node_children(child):
                    if not _is_group(sub):
                        continue
                    if not _COLORSET_FOLDER_RE.match(_node_name(sub) or ""):
                        continue
                    for layer in _node_children(sub):
                        parsed = _parse_colorset_row_id(_node_name(layer) or "")
                        if parsed is None:
                            continue
                        row, subrow = parsed
                        colorset_layers.setdefault((group_name, opt_name), []) \
                            .append((row, subrow, layer))

    return structure, ts_node_map, colorset_layers


def _set_visibility_for_option(ts_node_map: dict, group: str, option: str):
    """
    For every texture set:
      - Hide all top-level nodes except the target group folder.
      - Within the target group folder, hide all GroupLayer children except
        the target option folder (non-group children are left untouched —
        they are shared base content that should always render).
    """
    for ts_data in ts_node_map.values():
        for node in ts_data.get("_top", []):
            is_target_group = _is_group(node) and _node_name(node) == group
            node.set_visible(is_target_group)

            if is_target_group:
                for child in _node_children(node):
                    if not _is_group(child):
                        continue
                    is_target_opt = _node_name(child) == option
                    child.set_visible(is_target_opt)
                    if is_target_opt:
                        for sub in _node_children(child):
                            if _is_group(sub) and _COLORSET_FOLDER_RE.match(_node_name(sub) or ""):
                                for layer in _node_children(sub):
                                    layer.set_visible(False)


def _show_colorset_layers_for_option(ts_node_map: dict, group: str, option: str):
    """Inverse of the 'hide colorset layers' step inside _set_visibility_for_option:
    force-show every layer inside the Colorset sub-folder of the target option.
    Used by the previews flow so each tile renders with its dye colors applied."""
    for ts_data in ts_node_map.values():
        opt_node = ts_data.get(group, {}).get(option)
        if opt_node is None:
            continue
        for sub in _node_children(opt_node):
            if _is_group(sub) and _COLORSET_FOLDER_RE.match(_node_name(sub) or ""):
                sub.set_visible(True)
                for layer in _node_children(sub):
                    layer.set_visible(True)


def _set_viewport_3d_only(log=None):
    """Best-effort: trigger SP's '3D View only' menu action so the 2D pane
    isn't part of the screenshot. Silent no-op if the action can't be found —
    the user can switch to 3D-only manually beforehand."""
    try:
        mw = substance_painter.ui.get_main_window()
    except Exception:
        return
    if mw is None:
        return
    targets = ("3d view only", "3d viewport only", "show 3d view only",
               "3d view", "view 3d only")
    for act in mw.findChildren(QtGui.QAction):
        try:
            text = (act.text() or "").lower().replace("&", "").strip()
        except Exception:
            continue
        if text in targets:
            try:
                act.trigger()
                if log:
                    log(f"  [preview] Switched layout via menu action: {act.text()!r}")
            except Exception as exc:
                if log:
                    log(f"  [preview] Could not trigger '{act.text()}': {exc}")
            return
    if log:
        log("  [preview] No 3D-only layout action found — switch manually if 2D is visible.")


def _save_visibility(ts_node_map: dict) -> dict:
    saved: dict[str, bool] = {}
    for ts_name, ts_data in ts_node_map.items():
        for node in ts_data.get("_top", []):
            _save_recursive(node, f"{ts_name}/{_node_name(node)}", saved)
    return saved


def _restore_visibility(ts_node_map: dict, saved: dict):
    for ts_name, ts_data in ts_node_map.items():
        for node in ts_data.get("_top", []):
            _restore_recursive(node, f"{ts_name}/{_node_name(node)}", saved)


def _save_recursive(node, key: str, saved: dict):
    saved[key] = _node_visible(node)
    for child in _node_children(node):
        _save_recursive(child, f"{key}/{_node_name(child)}", saved)


def _restore_recursive(node, key: str, saved: dict):
    if key in saved:
        node.set_visible(saved[key])
    for child in _node_children(node):
        _restore_recursive(child, f"{key}/{_node_name(child)}", saved)


# ── SP API compatibility shims ────────────────────────────────────────────────

def _is_group(node) -> bool:
    # Try NodeType enum — value name changed across SP versions
    for attr in ("GroupLayer", "GroupLayerNode", "GROUP_LAYER", "GROUP"):
        try:
            return node.node_type == getattr(_ls.NodeType, attr)
        except AttributeError:
            continue
    # Fall back to isinstance check — class also renamed across versions
    for cls_name in ("GroupLayerNode", "GroupLayer"):
        try:
            return isinstance(node, getattr(_ls, cls_name))
        except Exception:
            continue
    return False


def _node_name(node) -> str:
    for getter in ("get_name", "name"):
        try:
            val = getattr(node, getter)
            return (val() if callable(val) else val) or ""
        except Exception:
            continue
    return ""


def _node_visible(node) -> bool:
    for getter in ("is_visible", "get_visible", "visible"):
        try:
            val = getattr(node, getter)
            return bool(val() if callable(val) else val)
        except Exception:
            continue
    return True


def _node_children(node) -> list:
    for getter in ("sub_layers", "nodes", "children", "get_children", "layers"):
        try:
            return list(getattr(node, getter)())
        except Exception:
            continue
    return []


# ── Colorset sub-folder helpers ───────────────────────────────────────────────

_COLORSET_FOLDER_RE = re.compile(r"^\s*colorset\s*$", re.IGNORECASE)
_COLORSET_ROW_RE    = re.compile(r"^\s*(\d{1,2})\s*([AB])\s*$", re.IGNORECASE)


def _parse_colorset_row_id(name: str):
    """Return (row:int, subrow:'A'|'B') or None when unparseable / out of range."""
    if not name:
        return None
    m = _COLORSET_ROW_RE.match(name)
    if not m:
        return None
    row = int(m.group(1))
    if not 1 <= row <= 16:
        return None
    return row, m.group(2).upper()


def _color_components(c):
    """Return the SP Color's components as a list of floats in sRGB space.
    Falls back to .value if conversion isn't available. Returns None if no
    extraction path works."""
    if c is None:
        return None
    # SP Color: convert(sRGB) returns a list of floats directly (NOT a Color).
    try:
        target = getattr(c, "sRGB", None)
        convert = getattr(c, "convert", None)
        if target is not None and callable(convert):
            srgb = convert(target)
            # In some versions srgb may itself be a Color with .value.
            if hasattr(srgb, "value"):
                v = srgb.value
                v = v() if callable(v) else v
                return [float(x) for x in v]
            # Newer versions: srgb IS the list.
            return [float(x) for x in srgb]
    except Exception:
        pass
    # Fallback: raw .value (working / linear space)
    try:
        v = getattr(c, "value", None)
        v = v() if callable(v) else v
        if v is not None:
            return [float(x) for x in v]
    except Exception:
        pass
    # Plain sequence
    try:
        return [float(x) for x in c]
    except Exception:
        pass
    return None


def _color_to_rgb(c):
    """Extract (r, g, b) floats from common SP / Qt / tuple color shapes."""
    comps = _color_components(c)
    if comps is not None and len(comps) >= 3:
        return comps[0], comps[1], comps[2]
    # Attribute-based: .r/.g/.b or .red/.green/.blue (last-resort)
    for trio in (("r", "g", "b"), ("red", "green", "blue")):
        try:
            vals = []
            for name in trio:
                v = getattr(c, name)
                v = v() if callable(v) else v
                vals.append(float(v))
            return tuple(vals)
        except Exception:
            continue
    return None


def _rgb_to_hex(c):
    """Accept various SP color shapes; return '#RRGGBB' or None."""
    rgb = _color_to_rgb(c)
    if rgb is None:
        return None
    def _to8(v):
        return max(0, min(255, int(round(v * 255 if v <= 1.0 else v))))
    try:
        return f"#{_to8(rgb[0]):02X}{_to8(rgb[1]):02X}{_to8(rgb[2]):02X}"
    except Exception:
        return None


_CHANNEL_ALIASES = {
    "diffuse":  ("basecolor", "diffuse", "albedo", "color", "base_color"),
    "emissive": ("emissive", "emission", "emissivecolor", "emissive_color"),
    "opacity":  ("opacity", "alpha"),
}


def _channel_label(ch) -> str:
    """Best-effort string for a Channel object — its label, type name, or repr."""
    for attr in ("label", "name"):
        try:
            v = getattr(ch, attr)
            v = v() if callable(v) else v
            if v:
                return str(v)
        except Exception:
            continue
    for attr in ("type", "channel_type"):
        try:
            v = getattr(ch, attr)
            v = v() if callable(v) else v
            if v is not None:
                return getattr(v, "name", str(v))
        except Exception:
            continue
    return repr(ch)


def _active_channels(node):
    """Return the iterable of active channel objects on a fill layer, or []."""
    try:
        v = getattr(node, "active_channels", None)
        if v is None:
            return []
        if callable(v):
            v = v()
        return list(v)
    except Exception:
        return []


def _source_value(src):
    """Pull a uniform value (RGBA tuple or scalar) off an SP source object."""
    if src is None:
        return None
    for attr in ("color", "color_rgba", "rgba", "value", "uniform_color",
                 "base_color"):
        try:
            v = getattr(src, attr)
            v = v() if callable(v) else v
            if v is not None:
                return v
        except Exception:
            continue
    # set_source_uniform_color counterpart: some versions expose get_*
    for getter in ("get_uniform_color", "get_color", "get_value"):
        try:
            fn = getattr(src, getter, None)
            if callable(fn):
                v = fn()
                if v is not None:
                    return v
        except Exception:
            continue
    return None


def _node_fill_channel_raw(node, kind: str, log=None):
    """Read a fill layer's channel value (RGBA tuple for colors, scalar/list
    for Opacity).  `kind` is one of 'diffuse', 'emissive', 'opacity' and is
    matched against the actual channel label/type, since the ChannelType
    enum lives in different modules across SP versions."""
    aliases = _CHANNEL_ALIASES.get(kind, (kind,))
    matched_channel = None
    for ch in _active_channels(node):
        label = _channel_label(ch).lower().replace(" ", "").replace("_", "")
        if any(a.replace("_", "") in label or label in a.replace("_", "")
               for a in aliases):
            matched_channel = ch
            break
    # Try get_source with the matched channel object (Split mode)
    if matched_channel is not None:
        fn = getattr(node, "get_source", None)
        if callable(fn):
            try:
                v = _source_value(fn(matched_channel))
                if v is not None:
                    return v
            except Exception:
                pass
    # Non-Split mode: get_source() with no argument returns a single source
    # used for every channel.
    fn = getattr(node, "get_source", None)
    if callable(fn):
        try:
            v = _source_value(fn())
            if v is not None:
                return v
        except Exception:
            pass
    # Material-mode fill layers expose a material source instead.
    fn = getattr(node, "get_material_source", None)
    if callable(fn):
        try:
            ms = fn()
            v = _source_value(ms)
            if v is not None:
                return v
            # Some material sources have per-channel sub-sources
            if matched_channel is not None:
                for getter in ("get_source", "get_channel_source"):
                    g = getattr(ms, getter, None)
                    if callable(g):
                        try:
                            v = _source_value(g(matched_channel))
                            if v is not None:
                                return v
                        except Exception:
                            continue
        except Exception:
            pass
    return None


def _read_color_channel(node, kind, log=None):
    """Pull a hex color string for a color channel ('diffuse' or 'emissive'),
    or None if SP returned nothing for that channel."""
    raw = _node_fill_channel_raw(node, kind, log)
    if raw is None:
        return None
    hexv = _rgb_to_hex(raw)
    if hexv is None and log:
        log(f"  [colorset-debug] got {kind} value but couldn't parse: "
            f"type={type(raw).__name__} attrs="
            f"{[a for a in dir(raw) if not a.startswith('_')][:15]}")
        _probe_color(raw, log)
    return hexv


def _probe_color(c, log):
    """Diagnostic: try every plausible Color accessor and log shape/repr."""
    for attr in ("value", "value_raw", "color_space", "sRGB", "working"):
        try:
            v = getattr(c, attr)
            if callable(v):
                try:
                    called = v()
                    log(f"  [colorset-probe] {attr}() -> {type(called).__name__}: "
                        f"{repr(called)[:120]}")
                except Exception as e:
                    log(f"  [colorset-probe] {attr}() raised: "
                        f"{type(e).__name__}: {e}")
            else:
                log(f"  [colorset-probe] {attr} attr -> {type(v).__name__}: "
                    f"{repr(v)[:120]}")
        except Exception as e:
            log(f"  [colorset-probe] {attr} lookup raised: "
                f"{type(e).__name__}: {e}")
    convert = getattr(c, "convert", None)
    if callable(convert):
        target = getattr(c, "sRGB", None)
        if callable(target):
            try:
                target = target()
            except Exception:
                target = None
        try:
            conv = convert(target) if target is not None else convert()
            log(f"  [colorset-probe] convert(sRGB) -> {type(conv).__name__}: "
                f"attrs={[a for a in dir(conv) if not a.startswith('_')][:15]}")
            for a in ("value", "value_raw"):
                try:
                    v = getattr(conv, a)
                    v = v() if callable(v) else v
                    log(f"  [colorset-probe] convert(sRGB).{a} -> "
                        f"{type(v).__name__}: {repr(v)[:120]}")
                except Exception as e:
                    log(f"  [colorset-probe] convert(sRGB).{a} raised: "
                        f"{type(e).__name__}: {e}")
        except Exception as e:
            log(f"  [colorset-probe] convert(sRGB) raised: "
                f"{type(e).__name__}: {e}")


def _node_fill_base_color(node, log=None):
    return _read_color_channel(node, "diffuse", log)


def _node_fill_emissive(node, log=None):
    """Return emissive intensity (0-1 float) or None.
    Proteus stores Emissive as a scalar intensity, not a color — collapse the
    SP Emissive channel's uniform color to its luminance."""
    raw = _node_fill_channel_raw(node, "emissive", log)
    comps = _color_components(raw)
    if not comps:
        return None
    if len(comps) == 1:
        return max(0.0, min(1.0, comps[0]))
    r = comps[0]
    g = comps[1] if len(comps) > 1 else r
    b = comps[2] if len(comps) > 2 else r
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return max(0.0, min(1.0, lum))


def _node_fill_opacity(node, log=None):
    """Return Proteus Opacity adjustment as an int in [-100, 100].
    Proteus's Opacity is a *delta* on the .mtrl's existing opacity, not an
    absolute value: 0 = leave alone, -100 = fully transparent, +100 = fully
    opaque. The SP Opacity channel's slider (0..1) naturally maps to the
    negative half: slider 1.0 → 0 (no change), slider 0.0 → -100. Above 1
    would map to positive, but SP's UI caps at 1."""
    raw = _node_fill_channel_raw(node, "opacity", log)
    comps = _color_components(raw)
    if not comps:
        return None
    v = comps[0]
    return max(-100, min(100, int(round((v - 1.0) * 100))))


def _diagnose_fill_layer(node, log):
    """Dump what SP exposes on this node so we can refine the channel shim."""
    try:
        node_type = getattr(node, "get_type", lambda: type(node).__name__)
        node_type = node_type() if callable(node_type) else node_type
        log(f"  [colorset-debug] node type: {node_type}")
    except Exception as e:
        log(f"  [colorset-debug] node type lookup failed: {e}")
    try:
        mode = getattr(node, "source_mode", None)
        mode = mode() if callable(mode) else mode
        log(f"  [colorset-debug] source_mode: {mode}")
    except Exception as e:
        log(f"  [colorset-debug] source_mode lookup failed: {e}")
    chans = _active_channels(node)
    log(f"  [colorset-debug] active_channels ({len(chans)}): "
        f"{[_channel_label(c) for c in chans]}")
    fn = getattr(node, "get_source", None)
    if callable(fn):
        for ch in chans + [None]:
            try:
                src = fn(ch) if ch is not None else fn()
                log(f"  [colorset-debug] get_source({_channel_label(ch) if ch else '∅'}) "
                    f"-> {type(src).__name__}: attrs="
                    f"{[a for a in dir(src) if not a.startswith('_')][:25]}")
            except Exception as e:
                log(f"  [colorset-debug] get_source({_channel_label(ch) if ch else '∅'}) "
                    f"raised: {type(e).__name__}: {e}")
    ms_fn = getattr(node, "get_material_source", None)
    if callable(ms_fn):
        try:
            ms = ms_fn()
            log(f"  [colorset-debug] get_material_source() -> {type(ms).__name__}: "
                f"attrs={[a for a in dir(ms) if not a.startswith('_')][:25]}")
        except Exception as e:
            log(f"  [colorset-debug] get_material_source() raised: "
                f"{type(e).__name__}: {e}")


def _rows_from_colorset_layers(entries, log=None):
    """entries: iterable of (row:int, subrow:'A'|'B', node).
    Coalesce by Row; SubRowA / SubRowB go on the same dict.
    Multi-TS policy: UNION, last-write-wins per (row, subrow)."""
    by_row: dict = {}
    diagnosed = False
    for row, subrow, node in entries:
        diffuse  = _node_fill_base_color(node, log)
        emissive = _node_fill_emissive(node, log)
        opacity  = _node_fill_opacity(node, log)
        if diffuse is None and emissive is None and opacity is None:
            if log:
                log(f"  [colorset] Skipping {row}{subrow}: no readable channels")
                if not diagnosed:
                    _diagnose_fill_layer(node, log)
                    diagnosed = True
            continue
        sub: dict = {}
        if diffuse  is not None: sub["Diffuse"]  = diffuse
        if emissive is not None: sub["Emissive"] = round(emissive, 4)
        if opacity  is not None: sub["Opacity"]  = opacity
        by_row.setdefault(row, {"Row": row})[f"SubRow{subrow}"] = sub
    return [by_row[r] for r in sorted(by_row)]


# ── Penumbra root auto-detect ─────────────────────────────────────────────────

# ── Plugin self-update ────────────────────────────────────────────────────────

_PLUGIN_REMOTE_URL = (
    "https://raw.githubusercontent.com/solona-m/substance-proteus-packager/"
    "main/proteus_packager.py"
)
_PLUGIN_COMMITS_API = (
    "https://api.github.com/repos/solona-m/substance-proteus-packager/"
    "commits?path=proteus_packager.py&per_page=1"
)


def _remote_last_modified(timeout: float = 3.0):
    """Return the latest commit's Unix timestamp for the published plugin
    file via the GitHub commits API, or None on any failure. (raw.github
    doesn't expose a Last-Modified header.)"""
    import urllib.request
    from datetime import datetime
    try:
        req = urllib.request.Request(
            _PLUGIN_COMMITS_API,
            headers={"Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        if not data:
            return None
        iso = data[0]["commit"]["committer"]["date"]  # e.g. "2026-05-21T16:17:09Z"
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _fetch_remote_file(url: str, timeout: float = 10.0):
    """Return remote file content as bytes, or None on failure."""
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return None


def _reload_penumbra_mod(path: str, name: str = "", log=None) -> bool:
    """POST to Penumbra's HTTP API to reload a mod in place. Both Path and Name are
    optional from Penumbra's side; we send Path (the on-disk folder) and Name
    (the mod's directory name) so Penumbra can find it either way.
    Returns True on HTTP 2xx; logs and returns False otherwise."""
    import urllib.request
    body = {}
    if path:
        body["Path"] = path
    if name:
        body["Name"] = name
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:42069/api/reloadmod",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            if 200 <= resp.status < 300:
                return True
            if log:
                log(f"Penumbra /reloadmod returned HTTP {resp.status}")
            return False
    except Exception as exc:
        if log:
            log(f"Penumbra /reloadmod failed (is the game running?): {exc}")
        return False


def _read_penumbra_root_from_xivlauncher(log=None) -> str:
    """Return the `ModDirectory` value from XIVLauncher's Penumbra config, or
    '' if anything fails. Mirrors Pickles-Playlist-Editor's GetPenumbraDirectory."""
    appdata = os.environ.get("APPDATA") or os.path.expandvars("%APPDATA%")
    cfg_path = os.path.join(appdata, "XIVLauncher", "pluginConfigs", "Penumbra.json")
    if not os.path.isfile(cfg_path):
        if log:
            log(f"Penumbra config not found: {cfg_path}")
        return ""
    try:
        with open(cfg_path, encoding="utf-8") as f:
            data = json.load(f)
        return (data.get("ModDirectory") or "").strip()
    except Exception as exc:
        if log:
            log(f"Could not read Penumbra config {cfg_path}: {exc}")
        return ""


# ── Misc helpers ──────────────────────────────────────────────────────────────

def _resolve_preset_url(preset_url: str, log=None) -> str:
    """
    If preset_url is a file:/// URI pointing to a .spexp file, import it as a
    session resource so SP's export API can resolve it, and return the resulting
    resource:// URL.  All other URL schemes are returned unchanged.
    """
    if not preset_url.startswith("file:///"):
        return preset_url

    import urllib.parse
    import substance_painter.resource as spres

    # file:///C:/path/Proteus.spexp  ->  C:/path/Proteus.spexp
    file_path = urllib.parse.unquote(preset_url[len("file:///"):])

    try:
        resource = spres.import_session_resource(file_path, spres.Usage.EXPORT)
        url = resource.identifier().url()
        if log:
            log(f"[preset] Registered '{Path(file_path).stem}' as session resource: {url}")
        return url
    except Exception as e:
        if log:
            log(f"[preset] Session import failed: {e}. Using URL as-is.")
        return preset_url


def _list_export_presets(log=None) -> list[tuple[str, str]]:
    """
    Return sorted list of (display_name, resource_url) for all SP output templates.

    Sources (in priority order):
      1. Predefined presets via SP export API  — correct working URLs guaranteed.
      2. Resource (shelf) presets via SP export API — 0 currently but future-proof.
      3. Filesystem scan of starter_assets .spexp files.
      4. Filesystem scan of user assets .spexp files (URL may not resolve).
    """
    import substance_painter
    import substance_painter.export as spexp

    def _dbg(msg):
        if log:
            log(f"[preset-scan] {msg}")

    seen: set[str] = set()
    presets: list[tuple[str, str]] = []

    # 1. Predefined presets (embedded in application, guaranteed resolvable)
    try:
        for pp in spexp.list_predefined_export_presets():
            if pp.url not in seen:
                seen.add(pp.url)
                presets.append((pp.name, pp.url))
                _dbg(f"predefined: {pp.name!r} -> {pp.url}")
    except Exception as e:
        _dbg(f"list_predefined_export_presets error: {e}")

    # 2. Resource (shelf) presets — includes user-imported ones when registered
    try:
        before = len(presets)
        for rp in spexp.list_resource_export_presets():
            url = rp.resource_id.url()
            if url not in seen:
                seen.add(url)
                presets.append((rp.resource_id.name, url))
        added = len(presets) - before
        if added:
            _dbg(f"resource presets: {added}")
    except Exception as e:
        _dbg(f"list_resource_export_presets error: {e}")

    # 3. Filesystem: built-in .spexp files
    try:
        sp_pkg = Path(substance_painter.__file__).parent
        resources_dir = sp_pkg.parent.parent.parent / "starter_assets" / "export-presets"
        for f in sorted(resources_dir.glob("*.spexp")):
            if f.stem.startswith("."):
                continue
            url = f"resource://starter_assets/{f.stem}"
            if url not in seen:
                seen.add(url)
                presets.append((f.stem, url))
    except Exception as e:
        _dbg(f"built-in scan error: {e}")

    # 4. Filesystem: user .spexp files — try file:// URI (absolute path)
    try:
        user_dir = Path(__file__).parent.parent.parent / "assets" / "export-presets"
        for f in sorted(user_dir.glob("*.spexp")):
            if f.stem.startswith("."):
                continue
            url = f.as_uri()  # file:///C:/Users/.../Proteus.spexp
            if url not in seen:
                seen.add(url)
                presets.append((f"[User] {f.stem}", url))
                _dbg(f"user: {f.stem!r} -> {url}")
    except Exception as e:
        _dbg(f"user scan error: {e}")

    _dbg(f"total presets found: {len(presets)}")
    return sorted(presets, key=lambda t: t[0].lower())


def _split_csv(text: str) -> list[str]:
    return [x.strip() for x in text.split(",") if x.strip()]


def _upsert_option(options: list, entry: dict) -> bool:
    """Insert entry into options, or replace an existing entry with the same
    Name. Returns True if an existing option was replaced."""
    for i, o in enumerate(options):
        if o.get("Name") == entry["Name"]:
            options[i] = entry
            return True
    options.append(entry)
    return False


def _none_penumbra_option() -> dict:
    return {"Name": "None", "Description": "", "Image": "", "Files": {},
            "FileSwaps": {}, "Manipulations": []}


_FS_UNSAFE = re.compile(r'[<>:"/\\|?*]+')


def _safe_path_component(name: str) -> str:
    """Strip filesystem-illegal chars (Windows superset) and collapse runs."""
    return _FS_UNSAFE.sub("_", name).strip(" .") or "_"


def _option_preview_src(out_dir: str, mod_name: str, group: str, option: str) -> str:
    """Where Generate Previews wrote the per-option preview PNG (may not exist)."""
    return os.path.join(out_dir, mod_name,
                        _safe_path_component(group),
                        _safe_path_component(option) + ".png")


def _maybe_copy_preview_into_pack(out_dir: str, mod_name: str,
                                  group: str, option: str,
                                  pmp_root: str) -> str:
    """If a preview PNG exists for (group, option), copy it into the pack at
    images/<group>/<option>.png and return the forward-slash relative path
    that goes into the Penumbra option's "Image" field. If no preview exists,
    return ''."""
    src = _option_preview_src(out_dir, mod_name, group, option)
    if not os.path.isfile(src):
        return ""
    sg = _safe_path_component(group)
    so = _safe_path_component(option)
    rel = f"images/{sg}/{so}.png"
    dst = os.path.join(pmp_root, "images", sg, so + ".png")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return rel


def _none_proteus_option() -> dict:
    return {"Name": "None", "Overlays": [], "ColorTableRows": []}


def _load_colorset_map(path: str, log=None) -> dict:
    """Read an existing Proteus metadata.json and map its options'
    ColorTableRows. Keyed by (group, name) and (None, name) so a lookup can
    fall back to matching on option name alone."""
    cmap: dict = {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        if log:
            log(f"[colorset] Could not read {path}: {exc}")
        return cmap
    for og in data.get("OptionGroups", []):
        g = og.get("PenumbraGroupName")
        for o in og.get("Options", []):
            rows = o.get("ColorTableRows")
            name = o.get("Name")
            if rows is None or name is None:
                continue
            cmap[(g, name)] = rows
            cmap.setdefault((None, name), rows)
    return cmap


_PNG_SIG  = b"\x89PNG\r\n\x1a\n"
_PNG_IEND = b"\x00\x00\x00\x00IEND\xae\x42\x60\x82"


def _png_copy_stamped(src: str, dst: str, label: str) -> None:
    """Copy a PNG to dst, injecting a tEXt chunk so identical pixel data
    produces distinct files — preventing Penumbra's auto-deduplicate from
    collapsing shared index textures across options."""
    with open(src, "rb") as f:
        data = f.read()

    if not data.startswith(_PNG_SIG) or not data.endswith(_PNG_IEND):
        shutil.copy2(src, dst)
        return

    chunk_data = b"Proteus-Option\x00" + label.encode("latin-1", errors="replace")
    crc = zlib.crc32(b"tEXt" + chunk_data) & 0xFFFFFFFF
    text_chunk = struct.pack(">I", len(chunk_data)) + b"tEXt" + chunk_data + struct.pack(">I", crc)

    with open(dst, "wb") as f:
        f.write(data[:-12])   # everything before IEND
        f.write(text_chunk)
        f.write(_PNG_IEND)


def _write_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

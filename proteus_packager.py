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
  %USERPROFILE%\Documents\Adobe\Adobe Substance 3D Painter\python

Copy the Proteus.spexp file to %USERPROFILE%\Documents\Adobe\Adobe Substance 3D Painter\assets\export-presets
"""

import os
import re
import json
import shutil
import tempfile
import configparser
from datetime import datetime
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
    from PySide6 import QtWidgets, QtCore
except ImportError:
    from PySide2 import QtWidgets, QtCore


PLUGIN_NAME = "Proteus Packager"
_INI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proteus_packager.ini")

_BIBO_PLUS_PATHS = "\n".join([
    "chara/human/c0201/obj/body/b0001/material/v0001/mt_c0201b0001_bibo.mtrl",
    "chara/human/c0401/obj/body/b0001/material/v0001/mt_c0401b0001_bibo.mtrl",
    "chara/human/c1401/obj/body/b0001/material/v0001/mt_c1401b0001_bibo.mtrl",
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
        self._export_preset = ""
        self._mutually_exclusive = True
        self._auto_export = False
        self._source_metadata_path = ""
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

    # ── Settings ──────────────────────────────────────────────────────────────

    def _load_settings(self):
        cfg = configparser.RawConfigParser()
        cfg.read(_INI_FILE, encoding="utf-8")

        g = cfg["General"] if "General" in cfg else {}
        self._author = g.get("Author", "")
        self._output_dir = g.get("OutputDir", "")
        self._export_preset = g.get("ExportPreset", "")
        self._mutually_exclusive = cfg.getboolean("General", "MutuallyExclusive", fallback=True)
        self._auto_export = cfg.getboolean("General", "AutoExport", fallback=False)
        self._source_metadata_path = g.get("SourceMetadata", "")

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
            "ExportPreset": self._export_preset,
            "MutuallyExclusive": str(self._mutually_exclusive),
            "AutoExport": str(self._auto_export),
            "SourceMetadata": self._source_metadata_path,
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

        # Source Metadata
        src_meta_row = QtWidgets.QHBoxLayout()
        src_meta_row.addWidget(QtWidgets.QLabel("Copy Colorset From"))
        self._source_meta_edit = QtWidgets.QLineEdit(self._source_metadata_path)
        self._source_meta_edit.setPlaceholderText("Optional — existing metadata.json")
        src_meta_row.addWidget(self._source_meta_edit)
        src_meta_browse_btn = QtWidgets.QPushButton("...")
        src_meta_browse_btn.setFixedWidth(30)
        src_meta_browse_btn.clicked.connect(self._browse_source_metadata)
        src_meta_row.addWidget(src_meta_browse_btn)
        root.addLayout(src_meta_row)

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

        self._auto_check = QtWidgets.QCheckBox("Auto-package when SP export finishes")
        self._auto_check.setChecked(self._auto_export)
        root.addWidget(self._auto_check)

        export_btn = QtWidgets.QPushButton("Export PMP")
        export_btn.clicked.connect(self._on_export_pmp_clicked)
        root.addWidget(export_btn)

        log_label_row = QtWidgets.QHBoxLayout()
        log_label_row.addWidget(QtWidgets.QLabel("Log"))
        log_label_row.addStretch()
        clear_btn = QtWidgets.QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_log)
        log_label_row.addWidget(clear_btn)
        root.addLayout(log_label_row)

        self._log_edit = QtWidgets.QPlainTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setFixedHeight(120)
        root.addWidget(self._log_edit)

        substance_painter.ui.add_dock_widget(self._widget)
        self._connect_ui_autosave()

    def _connect_ui_autosave(self):
        self._author_edit.editingFinished.connect(self._read_ui_settings)
        self._output_edit.editingFinished.connect(self._read_ui_settings)
        self._source_meta_edit.editingFinished.connect(self._read_ui_settings)
        self._export_preset_combo.currentIndexChanged.connect(self._read_ui_settings)
        self._mutex_check.stateChanged.connect(self._read_ui_settings)
        self._auto_check.stateChanged.connect(self._read_ui_settings)
        for edit in self._suffix_edits.values():
            edit.editingFinished.connect(self._read_ui_settings)

    def _browse_output(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self._widget, "Select Output Directory", self._output_edit.text()
        )
        if d:
            self._output_edit.setText(d)

    def _browse_source_metadata(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._widget,
            "Select existing metadata.json",
            self._source_meta_edit.text() or self._output_edit.text(),
            "Metadata JSON (metadata.json);;JSON Files (*.json);;All Files (*)",
        )
        if path:
            self._source_meta_edit.setText(path)
            self._read_ui_settings()

    def _load_mat_preset(self):
        name = self._mat_preset_combo.currentText()
        if name in self._presets:
            self._material_edit.setPlainText(self._presets[name])

    def _clear_log(self):
        self._log_edit.clear()

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
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        print(f"[ProteusPackager] {msg}")  # always visible in SP Python console
        if not hasattr(self, "_log_edit") or self._log_edit is None:
            return
        try:
            conn = QtCore.Qt.ConnectionType.QueuedConnection  # PySide6
        except AttributeError:
            conn = QtCore.Qt.QueuedConnection                 # PySide2
        try:
            QtCore.QMetaObject.invokeMethod(
                self._log_edit, "appendPlainText", conn,
                QtCore.Q_ARG(str, line),
            )
        except TypeError:
            self._log_edit.appendPlainText(line)

    # ── Events ────────────────────────────────────────────────────────────────

    def _connect_events(self):
        substance_painter.event.DISPATCHER.connect(
            substance_painter.event.ExportTexturesEnded,
            self._on_sp_export_finished,
        )
        for event_type in (
            substance_painter.event.ProjectOpened,
            substance_painter.event.ProjectCreated,
        ):
            substance_painter.event.DISPATCHER.connect(event_type, self._on_project_opened)

    def _on_project_opened(self, _ev=None):
        self._refresh_export_presets(select=self._export_preset)

    def _on_sp_export_finished(self, _res):
        self._read_ui_settings()
        if not self._auto_export:
            return
        if not substance_painter.project.is_open():
            return
        self._build_pmp()

    # ── Button handler ────────────────────────────────────────────────────────

    def _on_export_pmp_clicked(self):
        self._read_ui_settings()
        if not substance_painter.project.is_open():
            self._log("No project open.")
            return
        self._build_pmp()

    def _read_ui_settings(self):
        self._author = self._author_edit.text().strip()
        self._output_dir = self._output_edit.text().strip()
        self._source_metadata_path = self._source_meta_edit.text().strip()
        # Prefer the stored URL (item data); fall back to typed text for manual entry
        self._export_preset = (self._export_preset_combo.currentData()
                               or self._export_preset_combo.currentText().strip())
        self._material_paths = self._material_edit.toPlainText().strip()
        self._mutually_exclusive = self._mutex_check.isChecked()
        self._auto_export = self._auto_check.isChecked()
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
        structure, ts_node_map = _discover_structure(all_ts)
        if not structure:
            self._log("No group/option folder hierarchy found in the layer stack. "
                      "Add a top-level group folder (group) with sub-folders (options).")
            return

        self._log("Groups: " + ", ".join(
            f"{g}({', '.join(opts)})" for g, opts in structure.items()
        ))

        # Load ColorTableRows from a previously-built metadata.json if provided
        existing_color_rows: dict[tuple[str, str], list] = {}
        if self._source_metadata_path and os.path.exists(self._source_metadata_path):
            try:
                with open(self._source_metadata_path, encoding="utf-8") as _f:
                    _src = json.load(_f)
                for _og in _src.get("OptionGroups", []):
                    _gname = _og.get("PenumbraGroupName", "")
                    for _opt in _og.get("Options", []):
                        _rows = _opt.get("ColorTableRows")
                        if _rows is not None:
                            existing_color_rows[(_gname, _opt.get("Name", ""))] = _rows
                self._log(f"Source metadata: {len(existing_color_rows)} ColorTableRow entries loaded.")
            except Exception as _exc:
                self._log(f"Warning: could not read source metadata — {_exc}")

        # 2. Build temp directories
        tmpdir = tempfile.mkdtemp(prefix="proteus_pmp_")
        export_root = os.path.join(tmpdir, "_exports")  # intermediate SP exports
        pmp_root = os.path.join(tmpdir, "_pmp")         # final .pmp content
        proteus_dir = os.path.join(pmp_root, "Proteus")
        os.makedirs(export_root)
        os.makedirs(proteus_dir)

        # 3. Save current layer visibility
        saved_vis = _save_visibility(ts_node_map)

        try:
            groups_order = list(structure.keys())
            option_groups_meta = []

            for group in groups_order:
                options_meta = []

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

                    # Copy recognised files into the Proteus sidecar tree
                    rel_subdir = f"{group}/{option}"
                    abs_subdir = os.path.join(proteus_dir, group, option)
                    os.makedirs(abs_subdir, exist_ok=True)

                    overlay: dict = {}
                    overlay["MaterialGamePath"] = mat_paths[0] if len(mat_paths) == 1 else mat_paths

                    for fpath in exported_files:
                        tex_type = self._detect_type(fpath)
                        if tex_type is None:
                            self._log(f"  Skipping unrecognised: {Path(fpath).name}")
                            continue
                        fname = Path(fpath).name
                        shutil.copy2(fpath, os.path.join(abs_subdir, fname))
                        overlay[tex_type] = f"{rel_subdir}/{fname}"
                        self._log(f"  {tex_type}: {rel_subdir}/{fname}")

                    options_meta.append({
                        "Name": option,
                        "Overlays": [overlay],
                        "ColorTableRows": existing_color_rows.get(
                            (group, option),
                            [{"Row": 16, "SubRowA": {"Diffuse": "#FFFFFF"}}],
                        ),
                    })

                option_groups_meta.append({
                    "PenumbraGroupName": group,
                    "Options": options_meta,
                })

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
                opts = [{"Name": o, "Description": "", "Files": {}, "FileSwaps": {}, "Manipulations": []}
                        for o in structure[group]]
                safe = re.sub(r"[^\w]", "_", group).lower()
                _write_json(os.path.join(pmp_root, f"group_{idx:03d}_{safe}.json"), {
                    "Version": 0, "Name": group, "Description": "", "Image": "",
                    "Page": 0, "Priority": 0, "Type": group_type,
                    "DefaultSettings": 0, "Options": opts,
                })

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
        substance_painter.event.DISPATCHER.disconnect(
            substance_painter.event.ExportTexturesEnded,
            self._on_sp_export_finished,
        )
        for event_type in (
            substance_painter.event.ProjectOpened,
            substance_painter.event.ProjectCreated,
        ):
            substance_painter.event.DISPATCHER.disconnect(event_type, self._on_project_opened)
        if self._widget:
            substance_painter.ui.delete_ui_element(self._widget)
            self._widget = None


# ── Layer stack helpers ───────────────────────────────────────────────────────

def _discover_structure(all_ts: list):
    """
    Walk the layer stack for every texture set and collect the group/option
    folder hierarchy.

    Returns:
        structure:   dict[group_name → list[option_name]]  (insertion order)
        ts_node_map: dict[ts_name → {"_top": [...], group_name: {"_node": node,
                                                                   option_name: node}}]
    """
    structure: dict[str, list[str]] = {}
    ts_node_map: dict[str, dict] = {}

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

    return structure, ts_node_map


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
                    if _is_group(child):
                        child.set_visible(_node_name(child) == option)


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


def _write_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

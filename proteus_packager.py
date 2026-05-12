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
        cfg = configparser.ConfigParser()
        cfg.read(_INI_FILE, encoding="utf-8")

        g = cfg["General"] if "General" in cfg else {}
        self._author = g.get("Author", "")
        self._output_dir = g.get("OutputDir", "")
        self._export_preset = g.get("ExportPreset", "")
        self._mutually_exclusive = cfg.getboolean("General", "MutuallyExclusive", fallback=True)
        self._auto_export = cfg.getboolean("General", "AutoExport", fallback=False)

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
        cfg = configparser.ConfigParser()
        cfg["General"] = {
            "Author": self._author,
            "OutputDir": self._output_dir,
            "ExportPreset": self._export_preset,
            "MutuallyExclusive": str(self._mutually_exclusive),
            "AutoExport": str(self._auto_export),
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

    def _browse_output(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self._widget, "Select Output Directory", self._output_edit.text()
        )
        if d:
            self._output_edit.setText(d)

    def _load_mat_preset(self):
        name = self._mat_preset_combo.currentText()
        if name in self._presets:
            self._material_edit.setPlainText(self._presets[name])

    def _clear_log(self):
        self._log_edit.clear()

    def _refresh_export_presets(self, select: str = ""):
        names = _list_export_preset_names()
        current = select or self._export_preset_combo.currentText()
        self._export_preset_combo.blockSignals(True)
        self._export_preset_combo.clear()
        for name in names:
            self._export_preset_combo.addItem(name)
        # Restore selection; fall back to typing the saved value if not in list
        idx = self._export_preset_combo.findText(current)
        if idx >= 0:
            self._export_preset_combo.setCurrentIndex(idx)
        elif current:
            self._export_preset_combo.setEditText(current)
        self._export_preset_combo.blockSignals(False)

    def _log(self, msg: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        # Append safely from any thread; connection type name differs between PySide versions.
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
            # PySide6 dropped Q_ARG in some builds; call directly on main thread
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
        self._refresh_export_presets()

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
        self._export_preset = self._export_preset_combo.currentText().strip()
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
                    exported_files = self._do_sp_export(ts_names, opt_export_dir)

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
                        "ColorTableRows": [{"Row": 16, "SubRowA": {"Diffuse": "#FFFFFF"}}],
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

    def _do_sp_export(self, ts_names: list[str], output_dir: str) -> list[str]:
        config = {
            "exportShaderParams": False,
            "exportPath": output_dir,
            "exportList": [{"rootPath": ts} for ts in ts_names],
            "defaultExportPreset": self._export_preset,
        }
        try:
            result = substance_painter.export.export_project_textures(config)
            files: list[str] = []
            for ts_files in result.textures.values():
                files.extend(ts_files)
            return files
        except Exception as exc:
            self._log(f"  SP export error: {exc}")
            return []

    def _detect_type(self, fpath: str):
        stem = Path(fpath).stem
        for key in ("Diffuse", "Normal", "Index", "Mask"):
            for sfx in sorted(self._suffixes[key], key=len, reverse=True):
                if stem.endswith(sfx):
                    return key
        return None

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
        top_nodes = _ls.get_nodes(ts.get_stack())
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
    try:
        return node.node_type == _ls.NodeType.GroupLayer
    except AttributeError:
        pass
    try:
        return isinstance(node, _ls.GroupLayer)
    except Exception:
        return False


def _node_name(node) -> str:
    try:
        return node.name or ""
    except AttributeError:
        try:
            return node.get_name() or ""
        except Exception:
            return ""


def _node_visible(node) -> bool:
    try:
        return bool(node.visible)
    except AttributeError:
        try:
            return bool(node.get_visible())
        except Exception:
            return True


def _node_children(node) -> list:
    try:
        return list(node.children())
    except Exception:
        return []


# ── Misc helpers ──────────────────────────────────────────────────────────────

def _list_export_preset_names() -> list[str]:
    """Return sorted list of SP export preset names, trying several API forms."""
    import substance_painter.resource as spres
    try:
        # SP 9.x: list_resources accepts a Usage enum
        resources = spres.list_resources(spres.Usage.EXPORT)
        return sorted(r.identifier().name for r in resources)
    except Exception:
        pass
    try:
        # Older API: search with usage keyword
        resources = spres.search("", usage=spres.Usage.EXPORT)
        return sorted(r.identifier().name for r in resources)
    except Exception:
        pass
    try:
        # Fallback: iterate all resources and filter by type string
        all_res = spres.list_resources()
        return sorted(
            r.identifier().name for r in all_res
            if "export" in str(getattr(r, "type", lambda: "")()).lower()
        )
    except Exception:
        return []


def _split_csv(text: str) -> list[str]:
    return [x.strip() for x in text.split(",") if x.strip()]


def _write_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

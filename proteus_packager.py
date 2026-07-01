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
import threading
import time
import zlib
import configparser
from pathlib import Path

_scipy_install_attempted = False  # only try pip install once per session

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
    "chara/human/c1401/obj/body/b0101/material/v0001/mt_c1401b0101_bibo.mtrl",
    "chara/human/c1801/obj/body/b0001/material/v0001/mt_c1801b0001_bibo.mtrl",
    "chara/human/c1601/obj/body/b0001/material/v0001/mt_c1601b0001_bibo.mtrl",
])

_GEN3_PATHS = "\n".join([
    "chara/human/c0201/obj/body/b0001/material/v0001/mt_c0201b0001_b.mtrl",
    "chara/human/c0401/obj/body/b0001/material/v0001/mt_c0401b0001_b.mtrl",
    "chara/human/c1401/obj/body/b0001/material/v0001/mt_c1401b0001_b.mtrl",
    "chara/human/c1401/obj/body/b0101/material/v0001/mt_c1401b0101_b.mtrl",
    "chara/human/c1801/obj/body/b0001/material/v0001/mt_c1801b0001_b.mtrl",
    "chara/human/c1601/obj/body/b0001/material/v0001/mt_c1601b0001_b.mtrl",
])

_TALL_FEMALE_FACES_PATHS = "\n".join([
    "chara/human/c0201/obj/face/f0001/material/mt_c0201f0001_fac_a.mtrl",
    "chara/human/c0201/obj/face/f0002/material/mt_c0201f0002_fac_a.mtrl",
    "chara/human/c0201/obj/face/f0003/material/mt_c0201f0003_fac_a.mtrl",
    "chara/human/c0201/obj/face/f0004/material/mt_c0201f0004_fac_a.mtrl",
    "chara/human/c0201/obj/face/f0005/material/mt_c0201f0005_fac_a.mtrl",
    "chara/human/c0401/obj/face/f0101/material/mt_c0401f0101_fac_a.mtrl",
    "chara/human/c0401/obj/face/f0102/material/mt_c0401f0102_fac_a.mtrl",
    "chara/human/c0401/obj/face/f0103/material/mt_c0401f0103_fac_a.mtrl",
    "chara/human/c0401/obj/face/f0104/material/mt_c0401f0104_fac_a.mtrl",
    "chara/human/c0601/obj/face/f0001/material/mt_c0601f0001_fac_a.mtrl",
    "chara/human/c0601/obj/face/f0002/material/mt_c0601f0002_fac_a.mtrl",
    "chara/human/c0601/obj/face/f0003/material/mt_c0601f0003_fac_a.mtrl",
    "chara/human/c0601/obj/face/f0004/material/mt_c0601f0004_fac_a.mtrl",
    "chara/human/c0801/obj/face/f0001/material/mt_c0801f0001_fac_a.mtrl",
    "chara/human/c0801/obj/face/f0002/material/mt_c0801f0002_fac_a.mtrl",
    "chara/human/c0801/obj/face/f0003/material/mt_c0801f0003_fac_a.mtrl",
    "chara/human/c0801/obj/face/f0004/material/mt_c0801f0004_fac_a.mtrl",
    "chara/human/c0801/obj/face/f0101/material/mt_c0801f0101_fac_a.mtrl",
    "chara/human/c0801/obj/face/f0102/material/mt_c0801f0102_fac_a.mtrl",
    "chara/human/c0801/obj/face/f0103/material/mt_c0801f0103_fac_a.mtrl",
    "chara/human/c0801/obj/face/f0104/material/mt_c0801f0104_fac_a.mtrl",
    "chara/human/c1401/obj/face/f0001/material/mt_c1401f0001_fac_a.mtrl",
    "chara/human/c1401/obj/face/f0002/material/mt_c1401f0002_fac_a.mtrl",
    "chara/human/c1401/obj/face/f0003/material/mt_c1401f0003_fac_a.mtrl",
    "chara/human/c1401/obj/face/f0004/material/mt_c1401f0004_fac_a.mtrl",
    "chara/human/c1401/obj/face/f0101/material/mt_c1401f0101_fac_a.mtrl",
    "chara/human/c1401/obj/face/f0102/material/mt_c1401f0102_fac_a.mtrl",
    "chara/human/c1401/obj/face/f0103/material/mt_c1401f0103_fac_a.mtrl",
    "chara/human/c1401/obj/face/f0104/material/mt_c1401f0104_fac_a.mtrl",
    "chara/human/c1801/obj/face/f0001/material/mt_c1801f0001_fac_a.mtrl",
    "chara/human/c1801/obj/face/f0002/material/mt_c1801f0002_fac_a.mtrl",
    "chara/human/c1801/obj/face/f0003/material/mt_c1801f0003_fac_a.mtrl",
    "chara/human/c1801/obj/face/f0004/material/mt_c1801f0004_fac_a.mtrl",
    "chara/human/c1001/obj/face/f0001/material/mt_c1001f0001_fac_a.mtrl",
    "chara/human/c1001/obj/face/f0002/material/mt_c1001f0002_fac_a.mtrl",
    "chara/human/c1001/obj/face/f0003/material/mt_c1001f0003_fac_a.mtrl",
    "chara/human/c1001/obj/face/f0004/material/mt_c1001f0004_fac_a.mtrl",
    "chara/human/c1001/obj/face/f0101/material/mt_c1001f0101_fac_a.mtrl",
    "chara/human/c1001/obj/face/f0102/material/mt_c1001f0102_fac_a.mtrl",
    "chara/human/c1001/obj/face/f0103/material/mt_c1001f0103_fac_a.mtrl",
    "chara/human/c1001/obj/face/f0104/material/mt_c1001f0104_fac_a.mtrl",
    "chara/human/c1001/obj/face/f0201/material/mt_c1001f0201_fac_a.mtrl",
    "chara/human/c1001/obj/face/f0202/material/mt_c1001f0202_fac_a.mtrl",
    "chara/human/c1001/obj/face/f0249/material/mt_c1001f0249_fac_a.mtrl",
])

_DIFFUSE_DILATION_PX       = 16  # SP diffusion distance for color textures
_MASK_DILATION_PX          =  8  # max paint-search radius for mask dilation
_MASK_DILATION_INNER_PX    =  3  # max seam-proximity radius for mask dilation

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
        self._colorset_meta_manual = False
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
        self._presets: dict[str, str] = {"Bibo+": _BIBO_PLUS_PATHS, "Gen3": _GEN3_PATHS, "Tall Female Faces": _TALL_FEMALE_FACES_PATHS}
        self._gen3_mask_bg = ""

        self._load_settings()
        self._create_ui()
        # Restore material preset combo to saved selection (UI exists now).
        idx = self._mat_preset_combo.findText(self._mat_preset_name)
        if idx >= 0:
            self._mat_preset_combo.setCurrentIndex(idx)
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
        self._export_preset = g.get("ExportPreset", "")
        self._mutually_exclusive = cfg.getboolean("General", "MutuallyExclusive", fallback=True)
        self._install_to_penumbra = cfg.getboolean("General", "InstallToPenumbra", fallback=False)
        _default_gen3_bg = os.path.join(os.path.dirname(__file__), "gen3_mask_background.png")
        self._gen3_mask_bg = g.get("Gen3MaskBackground", _default_gen3_bg)
        self._mat_preset_name = g.get("MaterialPreset", "Bibo+")
        self._material_paths = g.get("MaterialPaths", "").replace("\\n", "\n") or _BIBO_PLUS_PATHS

        s = cfg["Suffixes"] if "Suffixes" in cfg else {}
        self._suffixes = {
            "Diffuse": _split_csv(s.get("Diffuse", "_d")),
            "Normal":  _split_csv(s.get("Normal",  "_n")),
            "Index":   _split_csv(s.get("Index",   "_id")),
            "Mask":    _split_csv(s.get("Mask",    "_m")),
        }

        self._presets = {"Bibo+": _BIBO_PLUS_PATHS, "Gen3": _GEN3_PATHS, "Tall Female Faces": _TALL_FEMALE_FACES_PATHS}
        if "Presets" in cfg:
            for k, v in cfg["Presets"].items():
                if k.lower() not in ("bibo+", "gen3", "tall female faces"):
                    self._presets[k] = v.replace("\\n", "\n")

    def _save_settings(self):
        cfg = configparser.RawConfigParser()
        cfg["General"] = {
            "Author": self._author,
            "OutputDir": self._output_dir,
            "ExportPreset": self._export_preset,
            "MutuallyExclusive": str(self._mutually_exclusive),
            "InstallToPenumbra": str(self._install_to_penumbra),
            "Gen3MaskBackground": self._gen3_mask_bg,
            "MaterialPreset": self._mat_preset_name,
            "MaterialPaths": self._material_paths.replace("\n", "\\n"),
        }
        cfg["Suffixes"] = {k: ",".join(v) for k, v in self._suffixes.items()}
        user_presets = {k: v.replace("\n", "\\n") for k, v in self._presets.items()
                        if k not in ("Bibo+", "Gen3", "Tall Female Faces")}
        if user_presets:
            cfg["Presets"] = user_presets
        with open(_INI_FILE, "w", encoding="utf-8") as f:
            cfg.write(f)

    def _project_settings_path(self) -> str:
        try:
            fp = substance_painter.project.file_path()
        except Exception:
            return ""
        if not fp:
            return ""
        return str(Path(fp).with_suffix(".proteus_packager.json"))

    def _load_project_settings(self):
        """Reset per-project fields to defaults, then load from sidecar if present."""
        self._existing_pmp = ""
        self._colorset_meta = ""
        self._colorset_meta_manual = False
        # Keep INI-loaded material paths as default; project sidecar may override.

        path = self._project_settings_path()
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                self._existing_pmp = d.get("ExistingPmp", "")
                self._colorset_meta = d.get("ColorsetMeta", "")
                self._colorset_meta_manual = d.get("ColorsetMetaManual", False)
                self._material_paths = d.get("MaterialPaths", self._material_paths)
            except Exception:
                pass

        self._existing_pmp_edit.setText(self._existing_pmp)
        self._material_edit.setPlainText(self._material_paths)
        self._refresh_colorset_meta_field()

    def _save_project_settings(self):
        """Save per-project fields to a sidecar JSON next to the .spp file."""
        path = self._project_settings_path()
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "ExistingPmp": self._existing_pmp,
                    "ColorsetMeta": self._colorset_meta,
                    "ColorsetMetaManual": self._colorset_meta_manual,
                    "MaterialPaths": self._material_paths,
                }, f, indent=2)
        except Exception:
            pass

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

        # Colorset metadata.json — auto-detected from the installed Penumbra
        # mod matching the Substance project, but freely overridable by typing
        # a path or browsing; ↻ clears the override and re-detects.
        cset_row = QtWidgets.QHBoxLayout()
        cset_row.addWidget(QtWidgets.QLabel("Colorset metadata"))
        self._colorset_meta_edit = QtWidgets.QLineEdit(self._colorset_meta)
        self._colorset_meta_edit.setToolTip(
            "Auto-detected from the installed Penumbra mod matching the "
            "Substance project name (its Proteus/metadata.json); options reuse "
            "its ColorTableRows by name. Type or browse to override; press ↻ "
            "to clear the override and re-detect."
        )
        cset_row.addWidget(self._colorset_meta_edit)
        cset_browse_btn = QtWidgets.QPushButton("...")
        cset_browse_btn.setFixedWidth(30)
        cset_browse_btn.setToolTip("Browse for a Proteus metadata.json (override)")
        cset_browse_btn.clicked.connect(self._browse_colorset_meta)
        cset_row.addWidget(cset_browse_btn)
        cset_redetect_btn = QtWidgets.QPushButton("↻")
        cset_redetect_btn.setFixedWidth(28)
        cset_redetect_btn.setToolTip(
            "Clear override and re-detect from the matching installed Penumbra mod")
        cset_redetect_btn.clicked.connect(self._redetect_colorset_meta)
        cset_row.addWidget(cset_redetect_btn)
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

        preview_btn = QtWidgets.QPushButton("Generate previews")
        preview_btn.setToolTip(
            "For each option in the layer stack, switch to 3D-only view, "
            "show its Colorset layers, screenshot the viewport, then save "
            "individual PNGs to <OutputDir>/<ModName>/ plus a combined grid."
        )
        preview_btn.clicked.connect(self._on_generate_previews_clicked)
        root.addWidget(preview_btn)

        export_btn = QtWidgets.QPushButton("Export PMP")
        export_btn.clicked.connect(self._on_export_pmp_clicked)
        root.addWidget(export_btn)

        substance_painter.ui.add_dock_widget(self._widget)
        self._connect_ui_autosave()
        # Reflect the auto-detected colorset source on open, unless the user
        # has a saved manual override (which is kept as-is). Often a no-op here
        # because the dock is built before any project is open — _on_project_opened
        # repeats it once the project (and Penumbra match) is available.
        self._refresh_colorset_meta_field()

    def _refresh_colorset_meta_field(self):
        """Show the auto-detected colorset source in the field so it matches what
        export will actually reuse. Skips when the user set a manual override.
        setText emits neither textEdited nor editingFinished, so this won't flip
        the manual flag or trigger an autosave."""
        if self._colorset_meta_manual:
            return
        self._colorset_meta = self._resolve_colorset_meta()
        self._colorset_meta_edit.setText(self._colorset_meta)

    def _connect_ui_autosave(self):
        self._author_edit.editingFinished.connect(self._read_ui_settings)
        self._output_edit.editingFinished.connect(self._read_ui_settings)
        self._existing_pmp_edit.editingFinished.connect(self._read_ui_settings)
        self._colorset_meta_edit.editingFinished.connect(self._read_ui_settings)
        # Typing in the field marks it as a manual override (programmatic
        # setText does not emit textEdited, so auto-detection won't flip this).
        self._colorset_meta_edit.textEdited.connect(self._on_colorset_meta_edited)
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

    def _on_colorset_meta_edited(self, _text):
        # User typed into the field — treat it as a manual override.
        self._colorset_meta_manual = True

    def _browse_colorset_meta(self):
        f, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._widget, "Select Proteus metadata.json",
            self._colorset_meta_edit.text(), "Proteus metadata (*.json)"
        )
        if f:
            self._colorset_meta_manual = True
            self._colorset_meta_edit.setText(f)
            self._read_ui_settings()

    def _redetect_colorset_meta(self):
        # Drop any manual override and re-run auto-detection.
        self._colorset_meta_manual = False
        self._colorset_meta = self._resolve_colorset_meta()
        self._colorset_meta_edit.setText(self._colorset_meta)
        self._read_ui_settings()

    def _resolve_colorset_meta(self) -> str:
        """Locate the Proteus metadata.json to reuse colorsets from.

        Matches an installed Penumbra mod folder against the current Substance
        project name and returns its Proteus/metadata.json. Returns '' if no
        project is open, the Penumbra root can't be read, or no matching mod
        has a Proteus/metadata.json."""
        try:
            if not substance_painter.project.is_open():
                return ""
            mod_name = (substance_painter.project.name() or "").strip()
        except Exception:
            return ""
        if not mod_name:
            return ""
        penumbra_root = _read_penumbra_root_from_xivlauncher()
        if not penumbra_root or not os.path.isdir(penumbra_root):
            return ""
        # Exact folder name first, then a case-insensitive match.
        candidates = [mod_name]
        try:
            candidates += [d for d in os.listdir(penumbra_root)
                           if d.lower() == mod_name.lower() and d != mod_name]
        except OSError:
            pass
        for folder in candidates:
            meta = os.path.join(penumbra_root, folder, "Proteus", "metadata.json")
            if os.path.isfile(meta):
                return meta
        return ""

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
        self._load_project_settings()

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
        all_ts = _visible_texture_sets(
            list(substance_painter.textureset.all_texture_sets()), log=self._log)
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
                # Masks are grayscale transparency images — they don't render as
                # a meaningful 3D tile, so there is nothing to preview.
                if _is_masks_group(group):
                    continue
                prev_group = ""
                for option in structure[group]:
                    _set_visibility_for_option(ts_node_map, structure, group, option, prev_group)
                    prev_group = group
                    _show_colorset_layers_for_option(ts_node_map, group, option)
                    pm = _grab_viewport_pixmap(self._log)
                    if pm is None:
                        self._log(f"  [preview] Skipped {group}/{option} — no pixmap.")
                        continue
                    sg = _safe_path_component(group).lower()
                    so = _safe_path_component(option).lower()
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
        # Persist whatever the field shows (auto-filled or a manual override);
        # the manual flag is maintained by the edit/browse/re-detect handlers.
        self._colorset_meta = self._colorset_meta_edit.text().strip()
        # Prefer the stored URL (item data); fall back to typed text for manual entry
        self._export_preset = (self._export_preset_combo.currentData()
                               or self._export_preset_combo.currentText().strip())
        self._material_paths = self._material_edit.toPlainText().strip()
        self._mat_preset_name = self._mat_preset_combo.currentText()
        self._mutually_exclusive = self._mutex_check.isChecked()
        self._install_to_penumbra = self._install_penumbra_check.isChecked()
        for key, edit in self._suffix_edits.items():
            self._suffixes[key] = _split_csv(edit.text())
        self._save_settings()
        self._save_project_settings()

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
        all_ts = _visible_texture_sets(
            list(substance_painter.textureset.all_texture_sets()), log=self._log)
        ts_names = [ts.name() for ts in all_ts]

        # 1. Discover structure from layer stack
        _t0 = time.time()
        structure, ts_node_map, colorset_layers = _discover_structure(all_ts)
        self._log(f"[timing] discover_structure: {time.time()-_t0:.2f}s")
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
        _t0 = time.time()
        saved_vis = _save_visibility(ts_node_map)
        self._log(f"[timing] save_visibility: {time.time()-_t0:.2f}s")

        try:
            groups_order = list(structure.keys())
            prev_group = ""
            option_groups_meta = []          # fresh-mode Proteus OptionGroups
            option_images: dict = {}         # (group, option) -> rel image path
            mask_threads: list = []

            # Decode the Gen3 mask background once; reused for every mask option.
            mask_bg: tuple | None = None
            if "_b.mtrl" in self._material_paths and self._gen3_mask_bg:
                _bg = _png_decode_rgba(self._gen3_mask_bg, self._log)
                if _bg[0] is not None:
                    mask_bg = _bg
                    _ensure_dilation_backend(self._log)
                else:
                    self._log(f"  Warning: could not load Gen3 mask background: {self._gen3_mask_bg}")

            colorset_map: dict = {}
            # Manual override wins; otherwise re-detect fresh for this project
            # so a stale auto-filled path can never bleed across projects.
            colorset_src = (self._colorset_meta if self._colorset_meta_manual
                            else self._resolve_colorset_meta())
            if colorset_src:
                if os.path.isfile(colorset_src):
                    colorset_map = _load_colorset_map(colorset_src, log=self._log)
                    self._log(f"Loaded colorsets from {colorset_src}")
                else:
                    self._log(f"Colorset metadata not found: {colorset_src}")

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
                # A "Masks" group is a Penumbra-only multi-select group: it is
                # never written into Proteus/metadata.json and has no "None".
                is_masks = _is_masks_group(group)
                gt = "Multi" if is_masks else group_type
                if merge:
                    og = None
                    if not is_masks:
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
                                 "Type": gt, "DefaultSettings": 0,
                                 "Options": []}
                        grp_files[group] = [gpath, gdata]
                    gdata.setdefault("Options", [])
                    touched_group_files[gpath] = gdata

                    proteus_opts = og["Options"] if og is not None else None
                    penumbra_opts = gdata["Options"]
                    if not is_masks:
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
                    QtWidgets.QApplication.processEvents()
                    self._log(f"Exporting {group}/{option}…")

                    _t_vis = time.time()
                    _set_visibility_for_option(ts_node_map, structure, group, option, prev_group)
                    prev_group = group
                    self._log(f"  [timing] set_visibility: {time.time()-_t_vis:.2f}s")

                    # Only export texture sets that still have at least one
                    # visible top-level node. Any TS that lacks the target
                    # group folder has ALL its nodes hidden by the call above
                    # and would export a flat blank that corrupts the overlay.
                    ts_names_export = [
                        ts_n for ts_n, ts_d in ts_node_map.items()
                        if any(_node_visible(n) for n in ts_d.get("_top", []))
                    ]
                    if not ts_names_export:
                        ts_names_export = ts_names
                    elif len(ts_names_export) < len(ts_names):
                        hidden = set(ts_names) - set(ts_names_export)
                        self._log(f"  Skipping hidden texture set(s): "
                                  f"{', '.join(sorted(hidden))}")

                    # Ask SP to export
                    opt_export_dir = os.path.join(export_root, group, option)
                    os.makedirs(opt_export_dir, exist_ok=True)
                    _t_exp = time.time()
                    exported_files = self._do_sp_export(
                        ts_names_export, opt_export_dir, resolved_preset,
                        passthrough=is_masks)
                    self._log(f"  [timing] sp_export: {time.time()-_t_exp:.2f}s")

                    if not exported_files:
                        self._log(f"  No files produced — skipping {group}/{option}")
                        continue

                    final_name = option

                    # Masks: one grayscale PNG at Proteus/Masks/<option>.png,
                    # mapped to the Penumbra option by name. No Proteus metadata
                    # entry, no overlay, no colorset.
                    if is_masks:
                        if not _copy_mask_option(exported_files, proteus_dir,
                                                 final_name, self._log,
                                                 mask_bg, mask_threads):
                            self._log(f"  No usable mask image — skipping {group}/{option}")
                            continue
                        self._log(f"  Mask: Masks/{final_name}.png")
                        image_rel = _maybe_copy_preview_into_pack(
                            self._resolve_output_dir(), mod_name,
                            group, final_name, pmp_root)
                        if image_rel:
                            option_images[(group, final_name)] = image_rel
                        if merge:
                            _upsert_option(penumbra_opts, {
                                "Name": final_name, "Description": "",
                                "Image": image_rel,
                                "Files": {}, "FileSwaps": {}, "Manipulations": []})
                        # Fresh-mode masks group file is built from `structure` below.
                        continue

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

                if not merge and not is_masks:
                    option_groups_meta.append({
                        "PenumbraGroupName": group,
                        "Options": [_none_proteus_option()] + proteus_opts,
                    })

            if mask_threads:
                self._log(f"Finishing {len(mask_threads)} mask background composite(s)…")
                _t0 = time.time()
                for _t in mask_threads:
                    _t.join()
                self._log(f"[timing] mask_join: {time.time()-_t0:.2f}s")

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
                _ensure_default_mod_has_content(
                    os.path.join(pmp_root, "default_mod.json"))

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
                            _default_mod_with_dummy())

                # group_NNN_{name}.json
                for idx, group in enumerate(groups_order, start=1):
                    is_masks = _is_masks_group(group)
                    gt = "Multi" if is_masks else group_type
                    base_opts = [
                        {"Name": o, "Description": "",
                         "Image": option_images.get((group, o), ""),
                         "Files": {}, "FileSwaps": {}, "Manipulations": []}
                        for o in structure[group]]
                    # Masks are always multi-select with no "None" option.
                    opts = base_opts if is_masks else [_none_penumbra_option()] + base_opts
                    safe = re.sub(r"[^\w]", "_", group).lower()
                    _write_json(os.path.join(pmp_root, f"group_{idx:03d}_{safe}.json"), {
                        "Version": 0, "Name": group, "Description": "", "Image": "",
                        "Page": 0, "Priority": 0, "Type": gt,
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
            _t0 = time.time()
            _restore_visibility(ts_node_map, saved_vis)
            self._log(f"[timing] restore_visibility: {time.time()-_t0:.2f}s")
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _do_sp_export(self, ts_names: list[str], output_dir: str,
                      preset_url: str = "", passthrough: bool = False) -> list[str]:
        if passthrough:
            params = {"paddingAlgorithm": "passthrough"}
        else:
            params = {"paddingAlgorithm": "diffusion",
                      "dilationDistance": _DIFFUSE_DILATION_PX}
        config = {
            "exportShaderParams": False,
            "exportPath": output_dir,
            "exportList": [{"rootPath": ts} for ts in ts_names],
            "defaultExportPreset": preset_url or self._export_preset,
            "exportParameters": [{"parameters": params}],
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


def _composite_preview_grid(tiles, out_path, canvas_w=1920, canvas_h=1080,
                            tile_w=256, label_h=24, log=None):
    """tiles: list of (label:str, QPixmap). Save a fixed 16:9 (canvas_w x
    canvas_h) grid PNG to out_path. Columns/rows are chosen to sit as close to
    the canvas aspect as the tile count allows; the grid is scaled to fit and
    centered, with the empty margin letterboxed. Returns True on success."""
    if not tiles:
        return False
    import math
    n = len(tiles)
    # A cell is a square tile plus a label strip beneath it; keep that strip
    # proportional to the tile so it scales cleanly at any output size.
    label_ratio = label_h / tile_w
    cell_h_units = 1.0 + label_ratio          # cell height in tile-width units
    target_aspect = canvas_w / canvas_h
    # cols/rows that best match the canvas aspect: solve cols/(rows*cell_h_units)
    # ≈ target_aspect with rows ≈ n/cols.
    cols = max(1, min(n, round(math.sqrt(n * target_aspect * cell_h_units))))
    rows = math.ceil(n / cols)
    # Largest tile size that fits the whole grid inside the canvas.
    tile = min(canvas_w / cols, canvas_h / (rows * cell_h_units))
    lab = tile * label_ratio
    cell_h = tile + lab
    off_x = (canvas_w - cols * tile) / 2.0
    off_y = (canvas_h - rows * cell_h) / 2.0

    canvas = QtGui.QPixmap(canvas_w, canvas_h)
    canvas.fill(QtGui.QColor(32, 32, 32))
    painter = QtGui.QPainter(canvas)
    try:
        font = painter.font()
        font.setPointSize(max(7, round(tile * 9 / tile_w)))
        painter.setFont(font)
        painter.setPen(QtGui.QColor(220, 220, 220))
        for i, (label, pm) in enumerate(tiles):
            col, row = i % cols, i // cols
            x = int(off_x + col * tile)
            y = int(off_y + row * cell_h)
            t = int(tile)
            scaled = pm.scaled(t, t,
                               QtCore.Qt.KeepAspectRatio,
                               QtCore.Qt.SmoothTransformation)
            ox = x + (t - scaled.width()) // 2
            oy = y + (t - scaled.height()) // 2
            painter.drawPixmap(ox, oy, scaled)
            painter.drawText(QtCore.QRect(x, y + t, t, int(lab)),
                             QtCore.Qt.AlignCenter, label)
    finally:
        painter.end()
    ok = canvas.save(out_path, "PNG")
    if log:
        log(f"Preview saved: {out_path}" if ok else f"Preview save failed: {out_path}")
    return ok


# ── Layer stack helpers ───────────────────────────────────────────────────────

def _visible_texture_sets(ts_list: list, log=None) -> list:
    """Return only texture sets whose eye/enable toggle is ON in SP's Texture
    Set List panel. Tries common API attribute names across SP versions. Falls
    back to returning all sets when none of the attributes are recognised (so
    the behaviour is unchanged on older SP builds).

    Logs which sets are being skipped so the user knows filtering is active."""
    result = []
    for ts in ts_list:
        visible = True  # include by default if API is unavailable
        for attr in ("enabled", "is_enabled", "visible", "is_visible"):
            v = getattr(ts, attr, None)
            if v is None:
                continue
            try:
                visible = bool(v() if callable(v) else v)
            except Exception:
                continue
            break
        if visible:
            result.append(ts)
        elif log:
            log(f"Skipping hidden texture set: {ts.name()}")
    # If no sets passed the filter (all hidden or API unavailable), export all.
    if not result:
        return ts_list
    return result


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


def _set_visibility_for_option(ts_node_map: dict, structure: dict,
                                group: str, option: str, prev_group: str = ""):
    """Show only the target group/option; hide everything else.

    When prev_group == group the top-level visibility state is unchanged so
    those set_visible() calls are skipped — this avoids triggering SP
    recalculations on every option when iterating within the same group.
    Option nodes are looked up directly from ts_node_map instead of walking
    _node_children() on every call."""
    for ts_data in ts_node_map.values():
        if prev_group != group:
            for node in ts_data.get("_top", []):
                is_target = _is_group(node) and _node_name(node) == group
                node.set_visible(is_target)

        group_entry = ts_data.get(group, {})
        for opt_name in structure.get(group, []):
            opt_node = group_entry.get(opt_name)
            if opt_node is None:
                continue
            is_target = opt_name == option
            opt_node.set_visible(is_target)
            if is_target:
                for sub in _node_children(opt_node):
                    if (_is_group(sub)
                            and _COLORSET_FOLDER_RE.match(_node_name(sub) or "")):
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
    """Save visibility only for the nodes _set_visibility_for_option actually
    mutates: top-level nodes, their direct group children (options), and the
    fill layers inside any Colorset sub-folders.  Avoids walking the full layer
    tree, which on a large file can mean thousands of unnecessary API calls."""
    saved: dict[str, bool] = {}
    for ts_name, ts_data in ts_node_map.items():
        for node in ts_data.get("_top", []):
            key = f"{ts_name}/{_node_name(node)}"
            saved[key] = _node_visible(node)
            if not _is_group(node):
                continue
            for child in _node_children(node):
                if not _is_group(child):
                    continue
                ckey = f"{key}/{_node_name(child)}"
                saved[ckey] = _node_visible(child)
                for sub in _node_children(child):
                    if not (_is_group(sub) and _COLORSET_FOLDER_RE.match(_node_name(sub) or "")):
                        continue
                    for layer in _node_children(sub):
                        saved[f"{ckey}/{_node_name(sub)}/{_node_name(layer)}"] = _node_visible(layer)
    return saved


def _restore_visibility(ts_node_map: dict, saved: dict):
    """Restore only the nodes recorded by the targeted _save_visibility, avoiding
    set_visible() calls on fill layers that were never changed."""
    for ts_name, ts_data in ts_node_map.items():
        for node in ts_data.get("_top", []):
            key = f"{ts_name}/{_node_name(node)}"
            if key in saved:
                node.set_visible(saved[key])
            if not _is_group(node):
                continue
            for child in _node_children(node):
                if not _is_group(child):
                    continue
                ckey = f"{key}/{_node_name(child)}"
                if ckey in saved:
                    child.set_visible(saved[ckey])
                for sub in _node_children(child):
                    if not (_is_group(sub) and _COLORSET_FOLDER_RE.match(_node_name(sub) or "")):
                        continue
                    for layer in _node_children(sub):
                        lkey = f"{ckey}/{_node_name(sub)}/{_node_name(layer)}"
                        if lkey in saved:
                            layer.set_visible(saved[lkey])


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

# Reserved top-level group name. A "Masks" group is special: its options are
# grayscale transparency masks applied at runtime over the output of all other
# groups. It is never written into Proteus/metadata.json — each option "Foo"
# maps by name to a flat grayscale file at Proteus/Masks/Foo.png, and the group
# is always a Penumbra multi-select group with no "None" option.
_MASKS_GROUP_RE = re.compile(r"^\s*masks\s*$", re.IGNORECASE)


def _is_masks_group(name: str) -> bool:
    return bool(_MASKS_GROUP_RE.match(name or ""))


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


# Penumbra treats a mod with no Files / Swaps / Manipulations across the
# default option and every group as "empty" and flags it as invalid. Proteus
# does the real work from its own metadata.json, so the Penumbra shell carries
# a single no-op swap (a game path redirected to itself) in the always-applied
# default option. It is never shown in the UI and changes nothing in-game, but
# it makes the package count as a real mod.
_DUMMY_SWAP_PATH = (
    "chara/monster/m8030/obj/body/b0001/material/v0001/mt_m8030b0001_a.mtrl"
)


def _default_mod_with_dummy() -> dict:
    return {"Files": {},
            "Swaps": {_DUMMY_SWAP_PATH: _DUMMY_SWAP_PATH},
            "Manipulations": []}


def _ensure_default_mod_has_content(path: str) -> None:
    """Guarantee default_mod.json registers as a real change so Penumbra does
    not treat the pack as empty. Writes the no-op dummy swap when the file is
    missing or has no Files/Swaps/Manipulations; leaves it untouched when it
    already carries real content (so merges don't clobber real redirects)."""
    data = None
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = None
    if isinstance(data, dict) and (data.get("Files") or data.get("Swaps")
                                   or data.get("Manipulations")):
        return
    _write_json(path, _default_mod_with_dummy())


_FS_UNSAFE = re.compile(r'[<>:"/\\|?*]+')


def _safe_path_component(name: str) -> str:
    """Strip filesystem-illegal chars (Windows superset) and collapse runs."""
    return _FS_UNSAFE.sub("_", name).strip(" .") or "_"


def _option_preview_src(out_dir: str, mod_name: str, group: str, option: str) -> str:
    """Where Generate Previews wrote the per-option preview PNG (may not exist).
    Lowercase to match the casing the in-pack Image field expects."""
    return os.path.join(out_dir, mod_name,
                        _safe_path_component(group).lower(),
                        _safe_path_component(option).lower() + ".png")


def _maybe_copy_preview_into_pack(out_dir: str, mod_name: str,
                                  group: str, option: str,
                                  pmp_root: str) -> str:
    """If a preview PNG exists for (group, option), copy it into the pack at
    images/<group>/<option>.png (lowercased) and return the relative path that
    goes into the Penumbra option's "Image" field. Returns '' if no preview."""
    src = _option_preview_src(out_dir, mod_name, group, option)
    if not os.path.isfile(src):
        return ""
    sg = _safe_path_component(group).lower()
    so = _safe_path_component(option).lower()
    # Mixed separator matches Penumbra's canonical convention for this field.
    rel = f"images\\{sg}/{so}.png"
    dst = os.path.join(pmp_root, "images", sg, so + ".png")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return rel


def _none_proteus_option() -> dict:
    return {"Name": "None", "Overlays": [], "ColorTableRows": []}


def _png_has_alpha(path: str) -> bool:
    """True if the PNG declares an alpha channel (color type 4 = gray+alpha or
    6 = truecolor+alpha), read straight from the IHDR header — no full decode."""
    try:
        with open(path, "rb") as f:
            head = f.read(26)
    except OSError:
        return False
    # 8-byte signature + 4-byte length + b"IHDR" + 4 width + 4 height +
    # 1 bit-depth, then the color-type byte at offset 25.
    if not head.startswith(_PNG_SIG) or len(head) < 26:
        return False
    return head[25] in (4, 6)


def _pick_mask_image(exported_files: list) -> str:
    """Choose the image to use as a Masks option's output. Prefer a PNG that
    carries an alpha channel — that's the Base-Color+Opacity map whose alpha is
    the 'apply region' Proteus reads. Fall back to the first exported file.
    Returns '' if nothing was exported."""
    if not exported_files:
        return ""
    for fpath in exported_files:
        if _png_has_alpha(fpath):
            return fpath
    return exported_files[0]


def _png_decode_rgba(path: str, log=None) -> tuple:
    """Decode a PNG to a flat RGBA bytearray. Returns (pixels, w, h).
    Uses PIL when available (milliseconds vs. minutes for large textures).
    Falls back to a pure-Python decoder so PIL is not a hard requirement."""
    try:
        from PIL import Image as _PILImage
        img = _PILImage.open(path).convert("RGBA")
        w, h = img.size
        return bytearray(img.tobytes()), w, h
    except ImportError:
        pass
    except Exception as exc:
        if log:
            log(f"  Warning: PIL decode failed for {path}: {exc} — using fallback")
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        if log:
            log(f"  Warning: cannot read {path}: {exc}")
        return None, 0, 0
    if not data.startswith(_PNG_SIG):
        return None, 0, 0
    chunks, i = [], 8
    while i + 12 <= len(data):
        n = struct.unpack(">I", data[i:i+4])[0]
        t, d = data[i+4:i+8], data[i+8:i+8+n]
        chunks.append((t, d))
        i += 12 + n
        if t == b"IEND":
            break
    ihdr = next((d for t, d in chunks if t == b"IHDR"), None)
    if not ihdr:
        return None, 0, 0
    w, h = struct.unpack(">II", ihdr[:8])
    bd, ct = ihdr[8], ihdr[9]
    if bd != 8:
        if log:
            log(f"  Warning: {path} is not 8-bit — bg skipped")
        return None, w, h
    ch = {0: 1, 2: 3, 4: 2, 6: 4}.get(ct)
    if ch is None:
        return None, w, h
    raw = zlib.decompress(b"".join(d for t, d in chunks if t == b"IDAT"))
    stride = w * ch
    src_px = bytearray(h * stride)
    prev = bytearray(stride)
    for y in range(h):
        base = y * (stride + 1)
        ft = raw[base]
        row = bytearray(raw[base+1:base+1+stride])
        if ft == 1:
            for x in range(ch, stride): row[x] = (row[x] + row[x-ch]) & 255
        elif ft == 2:
            for x in range(stride): row[x] = (row[x] + prev[x]) & 255
        elif ft == 3:
            for x in range(stride):
                a = row[x-ch] if x >= ch else 0
                row[x] = (row[x] + (a + prev[x]) // 2) & 255
        elif ft == 4:
            for x in range(stride):
                a = row[x-ch] if x >= ch else 0
                b2, c2 = prev[x], (prev[x-ch] if x >= ch else 0)
                pa, pb, pc = abs(b2-c2), abs(a-c2), abs(a+b2-2*c2)
                row[x] = (row[x] + (a if pa<=pb and pa<=pc else b2 if pb<=pc else c2)) & 255
        src_px[y*stride:(y+1)*stride] = row
        prev = row
    rgba = bytearray(w * h * 4)
    if ct == 6:
        rgba[:] = src_px
    elif ct == 2:
        for i in range(w * h):
            rgba[i*4:i*4+3] = src_px[i*3:i*3+3]
            rgba[i*4+3] = 255
    elif ct == 4:
        for i in range(w * h):
            g = src_px[i*2]
            rgba[i*4] = rgba[i*4+1] = rgba[i*4+2] = g
            rgba[i*4+3] = src_px[i*2+1]
    else:  # ct == 0, Gray
        for i in range(w * h):
            g = src_px[i]
            rgba[i*4] = rgba[i*4+1] = rgba[i*4+2] = g
            rgba[i*4+3] = 255
    return rgba, w, h



def _ensure_dilation_backend(log=None) -> None:
    """Detect (and install if missing) the fastest available dilation backend.
    Must be called from the main thread — SP's console drops logs from threads."""
    global _scipy_install_attempted
    try:
        __import__("scipy.ndimage")
        if log:
            log("  [dilation] backend: scipy CPU")
        return
    except ImportError:
        pass

    if not _scipy_install_attempted:
        _scipy_install_attempted = True
        if log:
            log("  [dilation] scipy not found — installing (one-time, may take ~30s)…")
        try:
            import subprocess, sys, os
            # sys.executable is the SP app binary in embedded Python, not python.exe
            _py = os.path.join(sys.prefix, "python.exe")
            if not os.path.isfile(_py):
                _py = os.path.join(sys.prefix, "bin", "python3")
            if not os.path.isfile(_py):
                _py = os.path.join(sys.prefix, "bin", "python")
            subprocess.run(
                [_py, "-m", "pip", "install", "scipy", "--quiet"],
                check=True, timeout=180)
        except Exception as _e:
            if log:
                log(f"  [dilation] scipy install failed: {_e}")

    try:
        __import__("scipy.ndimage")
        if log:
            log("  [dilation] backend: scipy CPU (just installed)")
        return
    except ImportError:
        pass

    try:
        __import__("numpy")
        if log:
            log("  [dilation] backend: numpy BFS (slow — scipy unavailable)")
    except ImportError:
        if log:
            log("  [dilation] backend: pure Python (very slow — install scipy)")


def _dilate_mask(src: str, dst: str, log=None, bg: tuple | None = None) -> bool:
    """Grow the painted (alpha>0) region of a mask outward, but only across UV
    seams. A transparent pixel is filled from its nearest painted pixel only
    when it lies within _MASK_DILATION_INNER_PX of a UV-island border AND within
    _MASK_DILATION_PX of actual paint. This kills the seam line without bleeding
    into intentionally-neutral areas. bg is (pixels, w, h) from _png_decode_rgba;
    a UV-island border is wherever bg alpha-membership flips between neighbours
    (the bg's alpha polarity does not matter). Original opaque pixels are kept.
    A pixel is also only eligible if its own UV island (per bg) already
    contains some real paint — otherwise an island this option never used at
    all (e.g. an unrelated accessory shell sitting nearby in the atlas) could
    get bled in purely because it's geometrically close to paint elsewhere."""
    R = _MASK_DILATION_PX

    # ── Fast path: CuPy/CUDA → scipy → numpy BFS → pure-Python ──────────────
    try:
        import numpy as np
        from PIL import Image as _I

        img = _I.open(src).convert("RGBA")
        arr = np.asarray(img, dtype=np.uint8).copy()
        h, w = arr.shape[:2]
        orig   = arr.copy()
        opaque = orig[:, :, 3] > 0

        _dt_done = False

        # ── Tier 1: scipy (CPU, C-implemented) ───────────────────────────────
        if not _dt_done:
            try:
                from scipy.ndimage import distance_transform_edt as _edt
                _df, (_ry, _rx) = _edt(~opaque, return_indices=True)
                dist = _df.astype(np.int32)
                sry  = _ry.astype(np.int32)
                srx  = _rx.astype(np.int32)
                _dt_done = True
            except ImportError:
                pass

        # ── Tier 2: numpy 4-sweep raster BFS ─────────────────────────────────
        if not _dt_done:
            if log:
                log(f"  [dilation] using numpy BFS (slow — install cupy or scipy)")
            INF = R + 1
            dist = np.full((h, w), INF, dtype=np.int32)
            sry  = np.zeros((h, w), dtype=np.int32)
            srx  = np.zeros((h, w), dtype=np.int32)
            oy, ox = np.where(opaque)
            dist[oy, ox] = 0; sry[oy, ox] = oy; srx[oy, ox] = ox

            for x in range(1, w):           # L→R
                c = dist[:, x-1] + 1; m = (c < dist[:, x]) & (c <= R)
                dist[:, x] = np.where(m, c,          dist[:, x])
                sry[:, x]  = np.where(m, sry[:, x-1], sry[:, x])
                srx[:, x]  = np.where(m, srx[:, x-1], srx[:, x])
            for x in range(w-2, -1, -1):    # R→L
                c = dist[:, x+1] + 1; m = (c < dist[:, x]) & (c <= R)
                dist[:, x] = np.where(m, c,          dist[:, x])
                sry[:, x]  = np.where(m, sry[:, x+1], sry[:, x])
                srx[:, x]  = np.where(m, srx[:, x+1], srx[:, x])
            for y in range(1, h):            # T→B
                c = dist[y-1] + 1; m = (c < dist[y]) & (c <= R)
                dist[y] = np.where(m, c,       dist[y])
                sry[y]  = np.where(m, sry[y-1], sry[y])
                srx[y]  = np.where(m, srx[y-1], srx[y])
            for y in range(h-2, -1, -1):     # B→T
                c = dist[y+1] + 1; m = (c < dist[y]) & (c <= R)
                dist[y] = np.where(m, c,       dist[y])
                sry[y]  = np.where(m, sry[y+1], sry[y])
                srx[y]  = np.where(m, srx[y+1], srx[y])

        to_fill = (~opaque) & (dist <= R)

        if bg is not None:
            bg_pix, bw, bh = bg
            if bw > 0 and bh > 0:
                bg_img = _I.frombytes("RGBA", (bw, bh), bytes(bg_pix))
                if (bw, bh) != (w, h):
                    bg_img = bg_img.resize((w, h), _I.NEAREST)
                bg_a = np.asarray(bg_img)[:, :, 3] > 0

                seam = np.zeros((h, w), dtype=bool)
                seam[:, :-1] |= bg_a[:, :-1] != bg_a[:, 1:]
                seam[:, 1:]  |= bg_a[:, :-1] != bg_a[:, 1:]
                seam[:-1, :] |= bg_a[:-1, :] != bg_a[1:, :]
                seam[1:, :]  |= bg_a[:-1, :] != bg_a[1:, :]

                R_INNER = _MASK_DILATION_INNER_PX
                INF_E = R_INNER + 1
                ed = np.full((h, w), INF_E, dtype=np.int32)
                ed[seam] = 0
                for x in range(1, w):
                    c = ed[:, x-1] + 1
                    ed[:, x] = np.where(c < ed[:, x], c, ed[:, x])
                for x in range(w-2, -1, -1):
                    c = ed[:, x+1] + 1
                    ed[:, x] = np.where(c < ed[:, x], c, ed[:, x])
                for y in range(1, h):
                    c = ed[y-1] + 1
                    ed[y] = np.where(c < ed[y], c, ed[y])
                for y in range(h-2, -1, -1):
                    c = ed[y+1] + 1
                    ed[y] = np.where(c < ed[y], c, ed[y])
                to_fill &= ed <= R_INNER

                # Don't bridge into a UV island that this option never painted
                # at all (e.g. an unrelated accessory shell that merely
                # happens to sit within R/R_INNER of paint elsewhere on the
                # atlas) — only islands that already contain some real paint
                # are eligible for the seam-fill. NOTE: in this bg map the
                # actual UV-island content lives in the *transparent* pixels
                # — bg_a (alpha>0) is the margin, not the island — so island
                # membership is labeled on ~bg_a, not bg_a.
                try:
                    from scipy.ndimage import label as _cc_label
                    bg_content = ~bg_a
                    isl, n_isl = _cc_label(bg_content, structure=np.ones((3, 3)))
                    if n_isl > 0:
                        painted = (opaque & bg_content).ravel()
                        counts = np.bincount(isl.ravel()[painted], minlength=n_isl + 1)
                        painted_island = counts > 0
                        _, (iy, ix) = _edt(isl == 0, return_indices=True)
                        nearest_isl = isl[iy, ix]
                        to_fill &= painted_island[nearest_isl]
                    else:
                        to_fill[:] = False
                except ImportError:
                    if log:
                        log("  [dilation] scipy unavailable — skipping island-paint "
                            "guard (may bleed into unpainted islands)")

        fy, fx = np.where(to_fill)
        arr[fy, fx] = orig[sry[fy, fx], srx[fy, fx]]
        _I.fromarray(arr).save(dst, "PNG")
        return True

    except ImportError:
        pass
    except Exception as exc:
        if log:
            log(f"  Warning: fast dilation failed ({exc}) — using pure-Python fallback")

    # ── Pure-Python fallback ──────────────────────────────────────────────────
    px, w, h = _png_decode_rgba(src, log)
    if px is None:
        if log:
            log(f"  Warning: could not decode mask for dilation — copying raw")
        return False

    stride = w * 4
    orig = bytes(px)

    # Resolve bg alpha lookup; scale nearest-neighbour if bg size != mask size.
    bg_alpha_fn = None  # callable(y, x) → bg alpha byte at mask coords (x, y)
    if bg is not None:
        bg_pix, bw, bh = bg
        if bw == w and bh == h:
            _bg_stride = bw * 4
            def bg_alpha_fn(y, x, _p=bg_pix, _s=_bg_stride):
                return _p[y * _s + x * 4 + 3]
        else:
            _bg_stride = bw * 4
            _sx_scale = bw / w
            _sy_scale = bh / h
            def bg_alpha_fn(y, x, _p=bg_pix, _s=_bg_stride,
                            _sx=_sx_scale, _sy=_sy_scale):
                return _p[int(y * _sy) * _s + int(x * _sx) * 4 + 3]
            if log:
                log(f"  Info: bg {bw}x{bh} scaled to mask {w}x{h} for UV edge_dist")

    # Per-pixel nearest-source tracking.
    INF = R + 1
    best_d = bytearray([INF] * (w * h))
    best_sx = [-1] * (w * h)
    best_sy = [-1] * (w * h)

    # Seed: original opaque pixels, distance 0.
    for y in range(h):
        ro = y * stride
        ri = y * w
        for x in range(w):
            if orig[ro + x * 4 + 3] > 0:
                best_d[ri + x] = 0
                best_sx[ri + x] = x
                best_sy[ri + x] = y

    # 4-sweep distance propagation.
    for y in range(h):          # L→R
        ri = y * w
        for x in range(1, w):
            pi = ri + x - 1
            d = best_d[pi] + 1
            if d < best_d[ri + x] and d <= R:
                best_d[ri + x] = d
                best_sx[ri + x] = best_sx[pi]
                best_sy[ri + x] = best_sy[pi]

    for y in range(h):          # R→L
        ri = y * w
        for x in range(w - 2, -1, -1):
            ni = ri + x + 1
            d = best_d[ni] + 1
            if d < best_d[ri + x] and d <= R:
                best_d[ri + x] = d
                best_sx[ri + x] = best_sx[ni]
                best_sy[ri + x] = best_sy[ni]

    for y in range(1, h):       # T→B
        ri = y * w
        pi = ri - w
        for x in range(w):
            d = best_d[pi + x] + 1
            if d < best_d[ri + x] and d <= R:
                best_d[ri + x] = d
                best_sx[ri + x] = best_sx[pi + x]
                best_sy[ri + x] = best_sy[pi + x]

    for y in range(h - 2, -1, -1):  # B→T
        ri = y * w
        ni = ri + w
        for x in range(w):
            d = best_d[ni + x] + 1
            if d < best_d[ri + x] and d <= R:
                best_d[ri + x] = d
                best_sx[ri + x] = best_sx[ni + x]
                best_sy[ri + x] = best_sy[ni + x]

    # Precompute distance-to-UV-island-border for each pixel: how many pixels
    # it is from the nearest UV seam (where bg "inside island" membership flips
    # between neighbours). Only pixels within R_INNER of a border are eligible
    # for dilation, so large neutral areas — whether deep inside an island or
    # far out in inter-island space — are left untouched. (The bg's alpha
    # polarity is irrelevant here: we key off the boundary, not the fill value.)
    R_INNER = _MASK_DILATION_INNER_PX
    edge_dist = None
    if bg_alpha_fn is not None:
        INF_E = R_INNER + 1
        # Membership map: 1 = inside a UV island per the bg.
        inside = bytearray(w * h)
        for y in range(h):
            ri = y * w
            for x in range(w):
                inside[ri + x] = 1 if bg_alpha_fn(y, x) > 0 else 0
        # Seed dist 0 at border pixels: membership differs from right/below.
        edge_dist = bytearray([INF_E] * (w * h))
        for y in range(h):
            ri = y * w
            for x in range(w):
                c = inside[ri + x]
                if x + 1 < w and inside[ri + x + 1] != c:
                    edge_dist[ri + x] = 0
                    edge_dist[ri + x + 1] = 0
                if y + 1 < h and inside[ri + w + x] != c:
                    edge_dist[ri + x] = 0
                    edge_dist[ri + w + x] = 0
        for y in range(h):            # L→R
            ri = y * w
            for x in range(1, w):
                d = edge_dist[ri + x - 1] + 1
                if d < edge_dist[ri + x]:
                    edge_dist[ri + x] = min(d, INF_E)
        for y in range(h):            # R→L
            ri = y * w
            for x in range(w - 2, -1, -1):
                d = edge_dist[ri + x + 1] + 1
                if d < edge_dist[ri + x]:
                    edge_dist[ri + x] = min(d, INF_E)
        for y in range(1, h):         # T→B
            ri = y * w
            pi = ri - w
            for x in range(w):
                d = edge_dist[pi + x] + 1
                if d < edge_dist[ri + x]:
                    edge_dist[ri + x] = min(d, INF_E)
        for y in range(h - 2, -1, -1):  # B→T
            ri = y * w
            ni = ri + w
            for x in range(w):
                d = edge_dist[ni + x] + 1
                if d < edge_dist[ri + x]:
                    edge_dist[ri + x] = min(d, INF_E)

    # Apply: fill an originally-transparent pixel only when it is near BOTH a
    # UV seam (edge_dist <= R_INNER) and actual paint (best_d <= R). This pins
    # dilation to the seam, leaving intentionally-neutral interior/exterior
    # areas — and paint boundaries that aren't on a UV edge — untouched.
    for y in range(h):
        ri = y * w
        ro = y * stride
        for x in range(w):
            off = ro + x * 4
            if orig[off + 3] != 0:
                continue
            d = best_d[ri + x]
            if d > R:
                continue
            if edge_dist is not None and edge_dist[ri + x] > R_INNER:
                continue  # too far from a UV seam
            sx = best_sx[ri + x]
            sy = best_sy[ri + x]
            s = sy * stride + sx * 4
            px[off:off + 4] = orig[s:s + 4]

    # Encode as RGBA PNG (filter type 0).
    out_raw = bytearray()
    for row_y in range(h):
        out_raw.append(0)
        out_raw += px[row_y * stride:(row_y + 1) * stride]
    compressed = zlib.compress(bytes(out_raw), 6)
    ihdr_data = struct.pack(">II", w, h) + bytes([8, 6, 0, 0, 0])

    def _chunk(ct, cd):
        return (struct.pack(">I", len(cd)) + ct + cd
                + struct.pack(">I", zlib.crc32(ct + cd) & 0xFFFFFFFF))

    out = bytearray(_PNG_SIG)
    out += _chunk(b"IHDR", ihdr_data)
    out += _chunk(b"IDAT", compressed)
    out += _PNG_IEND
    try:
        with open(dst, "wb") as f:
            f.write(out)
        return True
    except Exception as exc:
        if log:
            log(f"  ERROR: cannot write dilated mask: {exc}")
        return False


def _copy_mask_option(exported_files: list, proteus_dir: str, option: str,
                      log=None, mask_bg: tuple | None = None,
                      _threads: list | None = None) -> bool:
    """Write a Masks option's image to Proteus/Masks/<option>.png (stamped).
    If mask_bg is provided (decoded RGBA of the UV-boundary map PNG), dilation
    is applied so seam-gap pixels carry the nearest painted value rather than
    staying neutral (alpha=0). Processing runs on a background thread when
    _threads is given."""
    src = _pick_mask_image(exported_files)
    if not src:
        return False
    masks_dir = os.path.join(proteus_dir, "Masks")
    os.makedirs(masks_dir, exist_ok=True)
    dst = os.path.join(masks_dir, f"{option}.png")
    label = f"Masks/{option}"

    def _work():
        try:
            if mask_bg:
                if not _dilate_mask(src, dst, log, bg=mask_bg):
                    shutil.copy2(src, dst)
                _png_copy_stamped(dst, dst, label)
            else:
                _png_copy_stamped(src, dst, label)
        except Exception as exc:
            if log:
                log(f"  ERROR: mask '{option}' processing failed — {exc}")
            _png_copy_stamped(src, dst, label)
        if log and not _png_has_alpha(dst):
            log(f"  Warning: mask '{option}' has no alpha channel — Proteus uses "
                f"the alpha (the Opacity channel) as the apply region. Add an "
                f"Opacity channel so the mask knows which pixels to affect.")

    if _threads is not None:
        t = threading.Thread(target=_work, daemon=True, name=f"mask-bg-{option}")
        t.start()
        _threads.append(t)
    else:
        _work()
    return True


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

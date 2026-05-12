"""
Proteus Packager — Substance Painter plugin.

On SP texture export, auto-builds a Penumbra-compatible .pmp sidecar
with a Proteus/metadata.json, ready to drop into Penumbra.

Texture set naming convention:
  GroupName/OptionName  →  Penumbra option group + option
  Bare name (no /)      →  placed in a group named after the SP project

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
import substance_painter.event

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
        self._mutually_exclusive = True
        self._auto_export = True
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
        self._mutually_exclusive = cfg.getboolean("General", "MutuallyExclusive", fallback=True)
        self._auto_export = cfg.getboolean("General", "AutoExport", fallback=True)

        s = cfg["Suffixes"] if "Suffixes" in cfg else {}
        self._suffixes = {
            "Diffuse": _split_csv(s.get("Diffuse", "_d")),
            "Normal":  _split_csv(s.get("Normal",  "_n")),
            "Index":   _split_csv(s.get("Index",   "_id")),
            "Mask":    _split_csv(s.get("Mask",    "_m")),
        }

        m = cfg["MaterialPaths"] if "MaterialPaths" in cfg else {}
        self._material_paths = m.get("Default", _BIBO_PLUS_PATHS).replace("\\n", "\n")

        # Built-in preset always present; load any user-saved extras
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
            "MutuallyExclusive": str(self._mutually_exclusive),
            "AutoExport": str(self._auto_export),
        }
        cfg["Suffixes"] = {k: ",".join(v) for k, v in self._suffixes.items()}
        cfg["MaterialPaths"] = {"Default": self._material_paths.replace("\n", "\\n")}
        # Save user presets (skip the built-in Bibo+ — it's always hardcoded)
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

        # Author row
        author_row = QtWidgets.QHBoxLayout()
        author_row.addWidget(QtWidgets.QLabel("Author"))
        self._author_edit = QtWidgets.QLineEdit(self._author)
        author_row.addWidget(self._author_edit)
        root.addLayout(author_row)

        # Output Dir row
        out_row = QtWidgets.QHBoxLayout()
        out_row.addWidget(QtWidgets.QLabel("Output Dir"))
        self._output_edit = QtWidgets.QLineEdit(self._output_dir)
        out_row.addWidget(self._output_edit)
        browse_btn = QtWidgets.QPushButton("...")
        browse_btn.setFixedWidth(30)
        browse_btn.clicked.connect(self._browse_output)
        out_row.addWidget(browse_btn)
        root.addLayout(out_row)

        # Material Game Paths header + preset controls
        mat_label_row = QtWidgets.QHBoxLayout()
        mat_label_row.addWidget(QtWidgets.QLabel("Material Game Paths"))
        mat_label_row.addStretch()
        mat_label_row.addWidget(QtWidgets.QLabel("Preset:"))
        self._preset_combo = QtWidgets.QComboBox()
        for name in self._presets:
            self._preset_combo.addItem(name)
        mat_label_row.addWidget(self._preset_combo)
        load_btn = QtWidgets.QPushButton("Load")
        load_btn.clicked.connect(self._load_preset)
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

        # Options checkboxes
        self._mutex_check = QtWidgets.QCheckBox("Mutually exclusive options (Single select)")
        self._mutex_check.setChecked(self._mutually_exclusive)
        root.addWidget(self._mutex_check)

        self._auto_check = QtWidgets.QCheckBox("Auto-export on SP texture export")
        self._auto_check.setChecked(self._auto_export)
        root.addWidget(self._auto_check)

        # Export button
        export_btn = QtWidgets.QPushButton("Export PMP")
        export_btn.clicked.connect(self._export_pmp)
        root.addWidget(export_btn)

        # Log section
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

    def _load_preset(self):
        name = self._preset_combo.currentText()
        if name in self._presets:
            self._material_edit.setPlainText(self._presets[name])

    def _clear_log(self):
        self._log_edit.clear()

    def _log(self, msg: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        QtCore.QMetaObject.invokeMethod(
            self._log_edit,
            "appendPlainText",
            QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(str, line),
        )

    # ── Events ────────────────────────────────────────────────────────────────

    def _connect_events(self):
        substance_painter.event.DISPATCHER.connect(
            substance_painter.event.ExportTexturesEnded,
            self._on_export_finished,
        )

    def _on_export_finished(self, res):
        self._read_ui_settings()
        if not self._auto_export:
            return
        if not substance_painter.project.is_open():
            return
        self._build_pmp(res.textures)

    # ── Manual export ─────────────────────────────────────────────────────────

    def _read_ui_settings(self):
        self._author = self._author_edit.text().strip()
        self._output_dir = self._output_edit.text().strip()
        self._material_paths = self._material_edit.toPlainText().strip()
        self._mutually_exclusive = self._mutex_check.isChecked()
        self._auto_export = self._auto_check.isChecked()
        for key, edit in self._suffix_edits.items():
            self._suffixes[key] = _split_csv(edit.text())
        self._save_settings()

    def _export_pmp(self):
        self._read_ui_settings()
        if not substance_painter.project.is_open():
            self._log("No project open.")
            return
        textures = self._scan_for_textures()
        if not textures:
            self._log("No exported textures found. Run File > Export Textures first.")
            return
        self._build_pmp(textures)

    def _scan_for_textures(self) -> dict:
        """
        SP doesn't expose the last export result via a direct API call, so for the
        manual button path we scan the output directory for PNGs whose filename
        starts with a known texture set name.
        """
        out_dir = self._resolve_output_dir()
        if not out_dir or not os.path.isdir(out_dir):
            self._log(f"Output directory not found: {out_dir!r}")
            return {}

        ts_names = sorted(
            (ts.name() for ts in substance_painter.textureset.all_texture_sets()),
            key=len, reverse=True,  # longest first so prefixes don't shadow
        )
        result: dict[str, list[str]] = {}
        for fname in os.listdir(out_dir):
            if not fname.lower().endswith(".png"):
                continue
            fpath = os.path.join(out_dir, fname)
            stem = Path(fname).stem
            for ts_name in ts_names:
                if stem == ts_name or stem.startswith(ts_name + "_"):
                    result.setdefault(ts_name, []).append(fpath)
                    break
        return result

    def _resolve_output_dir(self) -> str:
        if self._output_dir:
            return self._output_dir
        fp = substance_painter.project.file_path()
        if fp:
            return str(Path(fp).parent)
        return ""

    # ── Core packaging ────────────────────────────────────────────────────────

    def _build_pmp(self, textures: dict):
        """
        textures: dict[texture_set_name → list[exported_file_paths]]
        """
        if not textures:
            self._log("Nothing to package.")
            return

        mod_name = substance_painter.project.name() or "UnnamedMod"
        author = self._author or "Unknown"
        mat_paths = [p.strip() for p in self._material_paths.splitlines() if p.strip()]
        group_type = "Single" if self._mutually_exclusive else "Multi"
        fallback_group = mod_name

        # Build suffix lookup sorted longest-first so _id beats _d on a stem ending in _id
        suffix_map: list[tuple[str, str]] = []
        for key in ("Diffuse", "Normal", "Index", "Mask"):
            for sfx in sorted(self._suffixes[key], key=len, reverse=True):
                suffix_map.append((sfx, key))

        def detect_type(file_path: str):
            stem = Path(file_path).stem
            for sfx, key in suffix_map:
                if stem.endswith(sfx):
                    return key
            return None

        # Bucket files: {(group, option): {tex_type: file_path}}
        # Preserve insertion order for stable group/option ordering.
        buckets: dict[tuple[str, str], dict[str, str]] = {}
        groups_order: list[str] = []

        for ts_name, files in textures.items():
            if "/" in ts_name:
                group, option = ts_name.split("/", 1)
                group = group.strip()
                option = option.strip()
            else:
                group = fallback_group
                option = ts_name

            if group not in groups_order:
                groups_order.append(group)

            key = (group, option)
            for fp in files:
                tex_type = detect_type(fp)
                if tex_type is None:
                    self._log(f"Skipping unrecognised: {Path(fp).name}")
                    continue
                buckets.setdefault(key, {})[tex_type] = fp

        if not buckets:
            self._log("No recognised texture files — check suffix settings.")
            return

        tmpdir = tempfile.mkdtemp(prefix="proteus_pmp_")
        try:
            proteus_dir = os.path.join(tmpdir, "Proteus")
            os.makedirs(proteus_dir, exist_ok=True)

            # ── Build Proteus/metadata.json ──────────────────────────────────
            option_groups_meta = []
            for group in groups_order:
                options_meta = []
                for (g, option) in buckets:
                    if g != group:
                        continue
                    tex_map = buckets[(g, option)]
                    rel_subdir = f"{group}/{option}"
                    abs_subdir = os.path.join(proteus_dir, group, option)
                    os.makedirs(abs_subdir, exist_ok=True)

                    overlay: dict = {}
                    # MaterialGamePath: single string if one path, array if multiple
                    if len(mat_paths) == 1:
                        overlay["MaterialGamePath"] = mat_paths[0]
                    else:
                        overlay["MaterialGamePath"] = mat_paths

                    for tex_type, src_path in tex_map.items():
                        fname = Path(src_path).name
                        shutil.copy2(src_path, os.path.join(abs_subdir, fname))
                        overlay[tex_type] = f"{rel_subdir}/{fname}"
                        self._log(f"Packed {rel_subdir}/{fname}")

                    options_meta.append({
                        "Name": option,
                        "Overlays": [overlay],
                        "ColorTableRows": [{"Row": 16, "SubRowA": {"Diffuse": "#FFFFFF"}}],
                    })

                option_groups_meta.append({
                    "PenumbraGroupName": group,
                    "Options": options_meta,
                })

            proteus_meta = {
                "FormatVersion": 1,
                "Name": mod_name,
                "Author": author,
                "OptionGroups": option_groups_meta,
            }
            _write_json(os.path.join(proteus_dir, "metadata.json"), proteus_meta)

            # ── meta.json ───────────────────────────────────────────────────
            _write_json(os.path.join(tmpdir, "meta.json"), {
                "FileVersion": 3,
                "Name": mod_name,
                "Author": author,
                "Description": "",
                "Version": "1.0",
                "Website": "",
                "ModTags": [],
            })

            # ── default_mod.json ─────────────────────────────────────────────
            _write_json(os.path.join(tmpdir, "default_mod.json"),
                        {"Files": {}, "Swaps": {}, "Manipulations": []})

            # ── group_NNN_{name}.json ────────────────────────────────────────
            for idx, group in enumerate(groups_order, start=1):
                penumbra_options = []
                for (g, option) in buckets:
                    if g != group:
                        continue
                    penumbra_options.append({
                        "Name": option,
                        "Description": "",
                        "Files": {},
                        "FileSwaps": {},
                        "Manipulations": [],
                    })

                safe = re.sub(r"[^\w]", "_", group).lower()
                _write_json(os.path.join(tmpdir, f"group_{idx:03d}_{safe}.json"), {
                    "Version": 0,
                    "Name": group,
                    "Description": "",
                    "Image": "",
                    "Page": 0,
                    "Priority": 0,
                    "Type": group_type,
                    "DefaultSettings": 0,
                    "Options": penumbra_options,
                })

            # ── ZIP → .pmp ───────────────────────────────────────────────────
            out_dir = self._resolve_output_dir()
            os.makedirs(out_dir, exist_ok=True)
            zip_base = os.path.join(out_dir, mod_name)
            archive = shutil.make_archive(zip_base, "zip", tmpdir)
            pmp_path = zip_base + ".pmp"
            if os.path.exists(pmp_path):
                os.remove(pmp_path)
            os.rename(archive, pmp_path)
            self._log(f"Exported: {pmp_path}")

        except Exception as exc:
            self._log(f"Error: {exc}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def cleanup(self):
        substance_painter.event.DISPATCHER.disconnect(
            substance_painter.event.ExportTexturesEnded,
            self._on_export_finished,
        )
        if self._widget:
            substance_painter.ui.delete_ui_element(self._widget)
            self._widget = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _split_csv(text: str) -> list[str]:
    return [x.strip() for x in text.split(",") if x.strip()]


def _write_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

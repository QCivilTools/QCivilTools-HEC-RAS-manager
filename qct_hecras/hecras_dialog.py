# -*- coding: utf-8 -*-
"""
QCT HEC-RAS Manager — hecras_dialog.py  v2.0.0
Combined: Project+Settings | Plan Editor | Run Manager | Result Viewer | Animate | Log
Author: Dat Vu
"""
import os
import sys
import math
from datetime import datetime

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QPushButton, QLabel, QLineEdit, QFileDialog, QTreeWidget,
    QTreeWidgetItem, QTableWidget, QTableWidgetItem, QHeaderView,
    QTextEdit, QSplitter, QGroupBox, QCheckBox, QSlider,
    QSpinBox, QProgressBar, QMessageBox, QScrollArea,
    QTextBrowser, QAbstractItemView, QComboBox, QGridLayout,
    QFormLayout, QSizePolicy, QInputDialog,
)
from qgis.PyQt.QtCore import Qt, QSettings, QTimer
from qgis.PyQt.QtGui import QTextCursor, QFont

from .hecras_reader import parse_prj, find_project_file
from .hecras_runner import MultiPlanRunManager


_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))


def _log_level(msg):
    mu = msg.upper()
    if "ERROR" in mu or "FAILED" in mu:
        return "ERROR"
    if "WARNING" in mu:
        return "WARNING"
    if "COMPLET" in mu or "DONE" in mu or "FINISH" in mu:
        return "SUCCESS"
    return "INFO"


# ── H5py helper — requires system h5py installed via OSGeo4W Shell ────────────
# Install once: open OSGeo4W Shell and run:  pip install h5py
def _get_h5py():
    try:
        import h5py
        return h5py
    except ImportError:
        return None

# ── HDF reader functions (from ras_hdf_reader) ──────────────────────────────
# These are module-level — HAS_RAS_COMMANDER resolved at import time


def _check_has_rc():
    """Check if ras_commander and its dependencies are available."""
    import importlib.util
    for pkg in ('geopandas', 'xarray', 'shapely', 'ras_commander'):
        if importlib.util.find_spec(pkg) is None:
            return False
    return True


HAS_RAS_COMMANDER = _check_has_rc()
HAS_H5PY = _get_h5py() is not None   # resolve at import time


def get_projection(hdf_path):
    if not HAS_H5PY:
        return None
    if HAS_RAS_COMMANDER:
        try:
            from ras_commander.hdf.HdfBase import HdfBase
            with h5py.File(hdf_path, "r") as hf:  # noqa: F821
                crs = HdfBase.get_projection(hf)
                if crs:
                    return str(crs)
        except BaseException:
            pass
    try:
        with h5py.File(hdf_path, "r") as hf:  # noqa: F821
            # Try 1: root-level file attribute "Projection" (common location)
            proj_attr = hf.attrs.get("Projection")
            if proj_attr is not None:
                if isinstance(proj_attr, bytes):
                    proj_attr = proj_attr.decode("utf-8", "replace")
                if isinstance(proj_attr, np.ndarray):  # noqa: F821
                    proj_attr = proj_attr.tobytes().decode("utf-8", "replace").rstrip("\x00")
                proj_attr = str(proj_attr).strip()
                if proj_attr:
                    return proj_attr
            # Try 2: per-area Projection dataset
            areas = list(hf.get("Geometry/2D Flow Areas", {}).keys())
            if not areas:
                return None
            p = hf.get(f"Geometry/2D Flow Areas/{areas[0]}/Projection")
            if p is None:
                return None
            v = p[()]
            if isinstance(v, bytes):
                return v.decode("utf-8", "replace")
            if isinstance(v, np.ndarray):  # noqa: F821
                return v.tobytes().decode("utf-8", "replace").rstrip("\x00")
            return str(v)
    except BaseException:
        return None


def read_cell_centroids(hdf_path, area_name):
    if not HAS_H5PY:
        return None, None
    if HAS_RAS_COMMANDER:
        try:
            from ras_commander.hdf.HdfMesh import HdfMesh
            from pathlib import Path
            gdf = HdfMesh.get_mesh_cell_points(Path(hdf_path))
            if gdf is not None and not gdf.empty:
                m = gdf[gdf["mesh_name"] == area_name] if "mesh_name" in gdf.columns else gdf
                if not m.empty:
                    return m.geometry.x.to_numpy(), m.geometry.y.to_numpy()
        except BaseException:
            pass
    try:
        with h5py.File(hdf_path, "r") as hf:  # noqa: F821
            c = hf.get(f"Geometry/2D Flow Areas/{area_name}/Cells Center Coordinate")
            if c is None:
                return None, None
            a = np.array(c)  # noqa: F821
            return a[:, 0], a[:, 1]
    except BaseException:
        return None, None


def read_time_stamps(hdf_path):
    """Return a list of timestamp strings, one per output timestep.
    Tries (in order): Time Date Stamp dataset, numeric offsets+start, RAS Commander."""
    if not HAS_H5PY:
        return []
    _BASE = ("Results/Unsteady/Output/Output Blocks/Base Output"
             "/Unsteady Time Series")
    _TS_DATE = f"{_BASE}/Time Date Stamp"
    _TS_TIME = f"{_BASE}/Time"
    _PLAN_HDR = "Plan Data/Plan Information"
    try:
        with h5py.File(hdf_path, "r") as hf:  # noqa: F821
            # Option 1: direct date-stamp strings
            ds = hf.get(_TS_DATE)
            if ds is not None and len(ds) > 0:
                raw = [v.decode("utf-8", "replace").strip()
                       if isinstance(v, bytes) else str(v).strip() for v in ds]
                if raw and raw[0]:
                    return raw
            # Option 2: numeric hour offsets + simulation start
            t_ds = hf.get(_TS_TIME)
            if t_ds is not None:
                import datetime as _dtime
                times_hr = np.array(t_ds, dtype=np.float64)  # noqa: F821
                start_dt = None
                plan_g = hf.get(_PLAN_HDR)
                if plan_g is not None:
                    for attr in ("Simulation Start Time", "Start Date",
                                 "Plan Start Date", "Starting Date"):
                        raw_s = plan_g.attrs.get(attr)
                        if raw_s is None:
                            continue
                        s = (raw_s.decode("utf-8", "replace").strip()
                             if isinstance(raw_s, bytes) else str(raw_s).strip())
                        for fmt in ("%d%b%Y %H:%M:%S", "%d%b%Y %H:%M", "%d%b%Y",
                                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                            try:
                                start_dt = _dtime.datetime.strptime(s, fmt)
                                break
                            except Exception:
                                pass
                        if start_dt:
                            break
                if start_dt:
                    return [(start_dt + _dtime.timedelta(hours=float(h))
                             ).strftime("%d%b%Y %H:%M:%S") for h in times_hr]
                else:
                    return [f"{h:.4f} hr" for h in times_hr]
    except Exception:
        pass
    # Option 3: RAS Commander
    if HAS_RAS_COMMANDER:
        try:
            from ras_commander.hdf.HdfBase import HdfBase
            from ras_commander.hdf.HdfUtils import HdfUtils
            with h5py.File(hdf_path, "r") as hf:  # noqa: F821
                start = HdfBase.get_simulation_start_time(hf)
                tp = f"{_BASE}/Time"
                if tp in hf:
                    times = HdfUtils.convert_timesteps_to_datetimes(
                        np.array(hf[tp][:]), start)  # noqa: F821
                    return [str(t) for t in times]
        except Exception:
            pass
    return []


def read_all_timeseries(hdf_path, area_name, variable):
    """
    Returns array of shape (nTime, nCells). Always guaranteed — callers can use
    data[time_idx] to get a frame.
    """
    import numpy as _np
    result = None

    if HAS_RAS_COMMANDER:
        try:
            from ras_commander.hdf.HdfResultsMesh import HdfResultsMesh
            from pathlib import Path
            da = HdfResultsMesh.get_mesh_timeseries(
                Path(hdf_path), area_name, variable, truncate=False)
            result = da.values.astype(_np.float32) if da is not None else None
        except BaseException:
            pass

    if result is None and HAS_H5PY:
        _T = ("Results/Unsteady/Output/Output Blocks/Base Output/"
              f"Unsteady Time Series/2D Flow Areas/{area_name}/{variable}")
        try:
            with h5py.File(hdf_path, "r") as hf:  # noqa: F821
                ds = hf.get(_T)
                if ds is not None:
                    result = _np.array(ds, dtype=_np.float32)
        except BaseException:
            pass

    if result is None or result.ndim != 2:
        return result

    # Guarantee (nTime, nCells): nTime is typically much smaller than nCells
    # If rows > cols then it's likely (nCells, nTime) — transpose
    if result.shape[0] > result.shape[1]:
        result = result.T
    return result


class _CrossSectionPickTool(object):
    """
    Built on QgsMapToolEmitPoint (QGIS\'s standard click-capture tool) rather
    than a hand-rolled QgsMapTool subclass, for reliable click handling across
    QGIS versions. Captures exactly two canvasClicked signals, draws a rubber
    band line as you click, then calls back with both points in project CRS.
    """

    def __init__(self, canvas, on_complete, on_cancel=None, log_fn=None):
        from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand
        from qgis.core import QgsWkbTypes
        from qgis.PyQt.QtGui import QColor as _QColor

        self.canvas = canvas
        self.on_complete = on_complete
        self.on_cancel = on_cancel
        self.log_fn = log_fn or (lambda m, lvl: None)
        self.points = []
        self._prev_tool = canvas.mapTool()   # restore on completion/cancel

        self.tool = QgsMapToolEmitPoint(canvas)
        self.rb = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.rb.setColor(_QColor(255, 0, 0, 220))
        self.rb.setWidth(3)

        self.tool.canvasClicked.connect(self._on_click)
        canvas.setMapTool(self.tool)
        self.log_fn("Cross-section pick tool armed — click point 1 on the map", "INFO")

    def _on_click(self, point, button):
        from qgis.PyQt.QtCore import Qt as _Qt
        if button == _Qt.RightButton:
            self._cancel()
            return
        self.points.append((point.x(), point.y()))
        self.rb.addPoint(point, True)
        self.log_fn(
            f"Cross-section click {len(self.points)}/2 at "
            f"({point.x():.2f}, {point.y():.2f})", "INFO")
        if len(self.points) >= 2:
            self._finish()

    def _finish(self):
        pts = list(self.points)
        self._restore_tool()
        self.rb.reset()
        self.log_fn(f"Cross-section complete: {pts}", "SUCCESS")
        try:
            self.on_complete(pts)
        except Exception as e:
            import traceback as _tb
            self.log_fn(
                f"on_complete callback FAILED: {e}\n{_tb.format_exc()}", "ERROR")

    def _cancel(self):
        self._restore_tool()
        self.rb.reset()
        self.log_fn("Cross-section drawing cancelled by user", "INFO")
        if self.on_cancel:
            self.on_cancel()

    def _restore_tool(self):
        try:
            if self._prev_tool is not None:
                self.canvas.setMapTool(self._prev_tool)
            else:
                self.canvas.unsetMapTool(self.tool)
        except Exception:
            pass


# Backward-compatible alias
CrossSectionMapTool = _CrossSectionPickTool


class HECRASDialog(QDialog):

    def __init__(self, iface=None, parent=None):
        super().__init__(parent, Qt.Window)
        self.iface = iface
        self._project_data = None
        self._run_manager = None
        self._anim_timer = QTimer()
        self._anim_timer.timeout.connect(self._anim_step)
        self._anim_layer = None
        self._anim_data = None
        self._anim_fid_map = None
        self._anim_gpkg = None
        self._rv_terrain_info = None
        self._pp_last_stamps = None
        self._pp_last_flow_arr = None
        self._pp_last_col_names = None
        self._pp_last_plan_name = None
        self._pp_last_xs_name = None
        # Track everything this plugin creates so it can be cleaned up on close
        self._qct_temp_paths = []   # temp file paths (CSV, GPKG, etc.)
        self._qct_temp_layer_ids = []   # QGIS layer ids added by this plugin
        self._anim_field = ""
        self._anim_stamps = []
        self._fh_drawn_line = None
        self._fh_maptool = None
        self._fh_xs_layer = None
        self._anim_index = 0
        self._project_units = "SI"
        self._project_crs_wkt = ""
        self._project_crs_name = ""
        self._project_crs_auth = ""
        self.setWindowTitle("QCivilTools — HEC-RAS Manager")
        self.setMinimumSize(1050, 700)
        self._build_ui()
        self._restore_settings()

    # ── Main layout ────────────────────────────────────────────────────────────
    def _build_ui(self):
        ml = QVBoxLayout(self)
        ml.setContentsMargins(6, 6, 6, 6)
        ml.setSpacing(4)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)

        self.tabs = QTabWidget()
        ll.addWidget(self.tabs)

        self._build_project_tab()    # Tab 1: Project Browser + Settings
        self._build_editor_tab()     # Tab 2: Plan Editor
        self._build_runner_tab()     # Tab 3: Run Manager
        self._build_results_tab()    # Tab 4: Result Viewer
        self._build_animate_tab()    # Tab 5: Animate
        self._build_postprocess_tab()  # Tab 6: Postprocess (Flow Hydrograph + graph)
        self._build_log_tab()        # Tab 7: Log

        self.status_lbl = QLabel("No project loaded.")
        ll.addWidget(self.status_lbl)

        splitter.addWidget(left)
        self._help_browser, help_panel = self._build_help_panel()
        splitter.addWidget(help_panel)
        splitter.setSizes([740, 300])

        # Update help content whenever the user switches tabs
        self.tabs.currentChanged.connect(self._update_help_panel)
        ml.addWidget(splitter)

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 1 — Project Browser + Settings
    # ══════════════════════════════════════════════════════════════════════════
    def _build_project_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        # ── Project path ──────────────────────────────────────────────────────
        pg = QGroupBox("HEC-RAS Project")
        pl = QHBoxLayout(pg)
        self.txt_project_path = QLineEdit()
        self.txt_project_path.setPlaceholderText("Select .prj file or project folder…")
        self.txt_project_path.setReadOnly(True)
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self._browse_project)
        btn_reload = QPushButton("↺ Reload")
        btn_reload.clicked.connect(self._reload_project)
        pl.addWidget(self.txt_project_path)
        pl.addWidget(btn_browse)
        pl.addWidget(btn_reload)
        lay.addWidget(pg)

        self.lbl_project_title = QLabel("No project loaded.")
        lay.addWidget(self.lbl_project_title)
        self.lbl_project_info = QLabel("")
        self.lbl_project_info.setWordWrap(True)
        self.lbl_project_info.setStyleSheet(
            "background:#eaf4fb;border-radius:3px;padding:4px;")
        lay.addWidget(self.lbl_project_info)

        # ── Plan tree (left) + details (right) ───────────────────────────────
        hsplit = QSplitter(Qt.Horizontal)

        # Left: plan tree
        tree_w = QWidget()
        tl = QVBoxLayout(tree_w)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(QLabel("Plans & Files:"))
        self.tree_plans = QTreeWidget()
        self.tree_plans.setHeaderLabels(["Name", "ID", "Exists"])
        self.tree_plans.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree_plans.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree_plans.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree_plans.setAlternatingRowColors(True)
        self.tree_plans.itemSelectionChanged.connect(self._on_tree_selection)
        tl.addWidget(self.tree_plans)
        hsplit.addWidget(tree_w)

        # Right: plan details table
        det_w = QWidget()
        dl = QVBoxLayout(det_w)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.addWidget(QLabel("Plan Details:"))
        self.tbl_details = QTableWidget()
        self.tbl_details.setColumnCount(2)
        self.tbl_details.setHorizontalHeaderLabels(["Property", "Value"])
        self.tbl_details.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tbl_details.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl_details.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_details.setAlternatingRowColors(True)
        self.tbl_details.verticalHeader().setVisible(False)
        dl.addWidget(self.tbl_details)
        hsplit.addWidget(det_w)
        hsplit.setSizes([340, 360])
        lay.addWidget(hsplit, 1)

        # ── Settings (collapsible) ────────────────────────────────────────────
        sg = QGroupBox("Settings")
        sl = QVBoxLayout(sg)
        sl.setSpacing(5)

        exe_row = QHBoxLayout()
        exe_row.addWidget(QLabel("RAS Executable:"))
        self.txt_ras_exe = QLineEdit()
        self.txt_ras_exe.setPlaceholderText(
            r"C:\Program Files (x86)\HEC\HEC-RAS\7.0\Ras.exe")
        btn_exe = QPushButton("Browse…")
        btn_exe.setFixedWidth(80)
        btn_exe.clicked.connect(self._browse_ras_exe)
        exe_row.addWidget(self.txt_ras_exe)
        exe_row.addWidget(btn_exe)
        sl.addLayout(exe_row)

        progid_row = QHBoxLayout()
        progid_row.addWidget(QLabel("COM ProgID:"))
        self.txt_ras_progid = QLineEdit()
        self.txt_ras_progid.setPlaceholderText("blank = auto-detect (RAS701→RAS700→…)")
        btn_test = QPushButton("Test")
        btn_test.setFixedWidth(60)
        btn_test.clicked.connect(self._test_controller)
        progid_row.addWidget(self.txt_ras_progid)
        progid_row.addWidget(btn_test)
        sl.addLayout(progid_row)

        btn_save_settings = QPushButton("Save Settings")
        btn_save_settings.setFixedWidth(110)
        btn_save_settings.clicked.connect(self._save_settings)
        sl.addWidget(btn_save_settings)
        lay.addWidget(sg)

        self.tabs.addTab(w, "Project + Settings")

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 2 — Plan Editor (raw text + parameter reference)
    # ══════════════════════════════════════════════════════════════════════════
    def _build_editor_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(5)

        # ── Plan selector row ─────────────────────────────────────────────────
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("Plan:"))
        self.pe_combo = QComboBox()
        self.pe_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.pe_combo.currentIndexChanged.connect(self._on_pe_plan_selected)
        sel_row.addWidget(self.pe_combo)
        btn_new = QPushButton("+ New Plan")
        btn_new.setFixedWidth(90)
        btn_new.clicked.connect(self._pe_new_plan)
        btn_del = QPushButton("🗑 Delete")
        btn_del.setFixedWidth(80)
        btn_del.clicked.connect(self._pe_delete_plan)
        sel_row.addWidget(btn_new)
        sel_row.addWidget(btn_del)
        lay.addLayout(sel_row)

        # ── File path label ───────────────────────────────────────────────────
        self.pe_path_lbl = QLabel("No plan loaded.")
        self.pe_path_lbl.setWordWrap(True)
        lay.addWidget(self.pe_path_lbl)

        # ── Sub-tabs: Raw Editor | HDF5 Output Variables ───────────────────────
        pe_subtabs = QTabWidget()

        # --- Sub-tab A: Raw Editor + parameter reference (existing layout) ---
        raw_tab = QWidget()
        raw_tab_lay = QVBoxLayout(raw_tab)
        raw_tab_lay.setContentsMargins(0, 4, 0, 0)
        edit_split = QSplitter(Qt.Horizontal)
        edit_split.setChildrenCollapsible(False)

        # Left: raw text editor
        self.pe_raw = QTextEdit()
        self.pe_raw.setLineWrapMode(QTextEdit.NoWrap)
        self.pe_raw.setFont(QFont("Consolas", 9))
        self.pe_raw.setPlaceholderText(
            "Load a project first, then select a plan to edit its raw content here.\n\n"
            "Edit any line directly — Plan Title, Geom File, Flow File,\n"
            "Simulation Date, intervals, run flags, etc.\n\n"
            "Run flags: -1 = Yes / enabled,  0 = No / disabled")
        edit_split.addWidget(self.pe_raw)

        # Right: parameter reference panel (mirrors HEC-RAS Unsteady Flow Analysis dialog)
        ref_panel = QWidget()
        ref_lay = QVBoxLayout(ref_panel)
        ref_lay.setContentsMargins(4, 0, 0, 0)
        ref_lay.setSpacing(3)
        ref_lay.addWidget(QLabel("<b>Parameter Reference</b>"))

        ref_tb = QTextBrowser()
        ref_tb.setOpenExternalLinks(False)
        ref_tb.setFont(QFont("Segoe UI", 8))
        ref_tb.setHtml("""
<style>
  body{font-family:"Segoe UI",Arial,sans-serif;font-size:9px;color:#1c2833;margin:4px;}
  h3{color:#1a5276;font-size:9px;font-weight:bold;margin:6px 0 2px;
     border-bottom:1px solid #aed6f1;padding-bottom:1px;}
  table{border-collapse:collapse;width:100%;margin:2px 0;}
  th{background:#d6eaf8;color:#1a5276;padding:2px 4px;border:1px solid #aed6f1;
     font-size:9px;text-align:left;}
  td{padding:2px 4px;border:1px solid #d5d8dc;font-size:9px;vertical-align:top;}
  tr:nth-child(even) td{background:#f5faff;}
  code{background:#eaecee;padding:0 3px;border-radius:2px;font-size:8px;
       font-family:Consolas,monospace;}
  .flag{color:#1e8449;font-weight:bold;}
</style>

<h3>Plan Identity</h3>
<table>
 <tr><th>Field</th><th>Example</th><th>Notes</th></tr>
 <tr><td><code>Plan Title=</code></td><td>100yr Base</td><td>Name shown in all tabs</td></tr>
 <tr><td><code>Short Identifier=</code></td><td>p01</td><td>File suffix, must be unique</td></tr>
 <tr><td><code>Plan Description=</code></td><td>Coastal flood scenario</td><td>Free text</td></tr>
</table>

<h3>Geometry &amp; Flow Files</h3>
<table>
 <tr><th>Field</th><th>Example</th><th>Notes</th></tr>
 <tr><td><code>Geom File=</code></td><td>g01</td><td>Links to <code>.g01</code> geometry file</td></tr>
 <tr><td><code>Flow File=</code></td><td>u01</td><td>Links to <code>.u01</code> unsteady flow file</td></tr>
</table>

<h3>Programs to Run (flags: <span class="flag">-1</span>=Yes, <span style="color:#c0392b;">0</span>=No)</h3>
<table>
 <tr><th>Field</th><th>Values</th><th>Description</th></tr>
 <tr><td><code>Run HTab=</code></td><td>-1 / 0</td><td>Geometry Preprocessor</td></tr>
 <tr><td><code>Run UNet=</code></td><td>-1 / 0</td><td>Unsteady Flow Simulation</td></tr>
 <tr><td><code>Run Sediment=</code></td><td>-1 / 0</td><td>Sediment Transport</td></tr>
 <tr><td><code>Run PostProcess=</code></td><td>-1 / 0</td><td>Post Processor (summary output)</td></tr>
 <tr><td><code>Run RASMapper=</code></td><td>-1 / 0</td><td>Floodplain Mapping (stored maps)</td></tr>
</table>

<h3>Simulation Time Window</h3>
<table>
 <tr><th>Field</th><th>Example</th><th>Notes</th></tr>
 <tr><td><code>Simulation Date=</code></td><td>02JAN2025,1530,02JAN2025,2400</td><td>Start/end date-time</td></tr>
</table>

<h3>Computation Settings</h3>
<table>
 <tr><th>Field</th><th>Example</th><th>Notes</th></tr>
 <tr><td><code>Computation Interval=</code></td><td>2SEC</td>
      <td>Timestep: 1SEC 2SEC 1MIN 2MIN 5MIN 10MIN 15MIN 30MIN 1HOUR</td></tr>
 <tr><td><code>Output Interval=</code></td><td>1MIN</td>
     <td>How often results are written to HDF (same tokens as above)</td></tr>
 <tr><td><code>Mapping Output Interval=</code></td><td>1MIN</td>
     <td>Frequency of RAS Mapper stored-map output</td></tr>
 <tr><td><code>Hydrograph Output Interval=</code></td><td>1MIN</td>
     <td>Frequency of hydrograph (DSS) output</td></tr>
 <tr><td><code>Detailed Output Interval=</code></td><td>1MIN</td>
     <td>Frequency of detailed unsteady output</td></tr>
</table>

<h3>DSS Output</h3>
<table>
 <tr><th>Field</th><th>Example</th><th>Notes</th></tr>
 <tr><td><code>DSS File=</code></td><td>C:\\DV\\HEC\\project.dss</td><td>Output DSS path</td></tr>
 <tr><td><code>UNET D1 Links DSS=</code></td><td>-1 / 0</td><td>Write 1D link results to DSS</td></tr>
 <tr><td><code>UNET D2 Cells DSS=</code></td><td>-1 / 0</td><td>Write 2D cell results to DSS</td></tr>
</table>

<h3>Common Computation Options</h3>
<table>
 <tr><th>Field</th><th>Example</th><th>Notes</th></tr>
 <tr><td><code>HTAB Param=</code></td><td>...</td><td>Geometry preprocessor parameters string</td></tr>
 <tr><td><code>UNET Theta=</code></td><td>1</td><td>Implicit weighting factor (1=fully implicit)</td></tr>
 <tr><td><code>UNET Theta Warmup=</code></td><td>1</td><td>Theta during warmup period</td></tr>
 <tr><td><code>UNET Use Existing IB Tables=</code></td><td>-1 / 0</td><td>Reuse preprocessed geometry</td></tr>
 <tr><td><code>UNET Warmup Time=</code></td><td>0</td><td>Warmup duration (minutes)</td></tr>
 <tr><td><code>UNET Write IC File=</code></td><td>-1 / 0</td><td>Write initial conditions file</td></tr>
 <tr><td><code>UNET 2D Equation Set=</code></td><td>Full Momentum</td><td>Diffusion Wave or Full Momentum</td></tr>
 <tr><td><code>UNET D2 Solver=</code></td><td>Pardiso</td><td>Matrix solver: Pardiso / GMRES</td></tr>
 <tr><td><code>UNET D2 Use Courant Number=</code></td><td>-1 / 0</td><td>Adaptive timestep by Courant</td></tr>
 <tr><td><code>UNET D2 Courant Number=</code></td><td>1</td><td>Target Courant number (1 recommended)</td></tr>
</table>
""")
        ref_lay.addWidget(ref_tb, 1)
        edit_split.addWidget(ref_panel)
        edit_split.setSizes([560, 340])
        raw_tab_lay.addWidget(edit_split, 1)
        pe_subtabs.addTab(raw_tab, "Raw Editor")

        # --- Sub-tab B: HDF5 Output Variables checklist ---
        hdf_tab = self._build_pe_hdf_vars_tab()
        pe_subtabs.addTab(hdf_tab, "HDF5 Output Variables")

        lay.addWidget(pe_subtabs, 1)

        # ── Action toolbar ────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.pe_reload_btn = QPushButton("↺ Reload from disk")
        self.pe_reload_btn.clicked.connect(self._pe_reload)
        self.pe_save_btn = QPushButton("💾 Save")
        self.pe_save_btn.clicked.connect(self._pe_save)
        self.pe_saveas_btn = QPushButton("📄 Save As New Plan…")
        self.pe_saveas_btn.clicked.connect(self._pe_save_as)

        for b in [self.pe_reload_btn, self.pe_save_btn, self.pe_saveas_btn]:
            b.setEnabled(False)

        btn_row.addWidget(self.pe_reload_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.pe_save_btn)
        btn_row.addWidget(self.pe_saveas_btn)
        lay.addLayout(btn_row)

        self._pe_current_path = None
        self.tabs.addTab(w, "Plan Editor")

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 3 — Run Manager
    # ══════════════════════════════════════════════════════════════════════════
    def _build_runner_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        # ── Plan selection table ──────────────────────────────────────────────
        lay.addWidget(QLabel("Select plans to run:"))
        self.tbl_run_plans = QTableWidget()
        self.tbl_run_plans.setColumnCount(5)
        self.tbl_run_plans.setHorizontalHeaderLabels(
            ["Run", "Plan Title", "Short ID", "Flow Type", "Status"])
        hh = self.tbl_run_plans.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.tbl_run_plans.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_run_plans.setAlternatingRowColors(True)
        self.tbl_run_plans.setMaximumHeight(180)
        lay.addWidget(self.tbl_run_plans)

        sel_row = QHBoxLayout()
        btn_all = QPushButton("Select All")
        btn_all.clicked.connect(self._select_all_plans)
        btn_none = QPushButton("Select None")
        btn_none.clicked.connect(self._select_no_plans)
        sel_row.addWidget(btn_all)
        sel_row.addWidget(btn_none)
        sel_row.addStretch()
        lay.addLayout(sel_row)

        # ── Run options ───────────────────────────────────────────────────────
        og = QGroupBox("Run Options")
        ol = QGridLayout(og)
        ol.setSpacing(6)

        ol.addWidget(QLabel("Mode:"), 0, 0)
        self.rb_sequential = QPushButton("Sequential")
        self.rb_sequential.setCheckable(True)
        self.rb_sequential.setChecked(True)
        self.rb_parallel = QPushButton("Parallel")
        self.rb_parallel.setCheckable(True)
        self.rb_sequential.clicked.connect(lambda: (self.rb_sequential.setChecked(True),
                                                    self.rb_parallel.setChecked(False)))
        self.rb_parallel.clicked.connect(lambda: (self.rb_parallel.setChecked(True),
                                                  self.rb_sequential.setChecked(False)))
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.rb_sequential)
        mode_row.addWidget(self.rb_parallel)
        mode_row.addStretch()
        ol.addLayout(mode_row, 0, 1)

        ol.addWidget(QLabel("Max parallel workers:"), 1, 0)
        self.spn_workers = QSpinBox()
        self.spn_workers.setRange(1, 8)
        self.spn_workers.setValue(2)
        self.spn_workers.setFixedWidth(60)
        ol.addWidget(self.spn_workers, 1, 1)

        ol.addWidget(QLabel("Engine:"), 2, 0)
        self.rb_rc = QPushButton("RAS Commander")
        self.rb_rc.setCheckable(True)
        self.rb_rc.setChecked(True)   # DEFAULT — parallel support, required package
        self.rb_rc.setToolTip(
            "Requires RAS Commander package. Supports parallel runs and core-count control.")
        self.rb_exe = QPushButton("RAS Executable")
        self.rb_exe.setCheckable(True)
        self.rb_exe.setToolTip("Launches Ras.exe directly. No extra packages needed.")
        self.rb_ctrl = QPushButton("RAS Controller (COM)")
        self.rb_ctrl.setCheckable(True)
        self.rb_ctrl.setToolTip(
            "Not recommended — COM is incompatible with 64-bit QGIS Python (bitness mismatch).")

        def _set_engine(active):
            for b in [self.rb_rc, self.rb_exe, self.rb_ctrl]:
                b.setChecked(False)
            active.setChecked(True)
        self.rb_rc.clicked.connect(lambda: _set_engine(self.rb_rc))
        self.rb_exe.clicked.connect(lambda: _set_engine(self.rb_exe))
        self.rb_ctrl.clicked.connect(lambda: _set_engine(self.rb_ctrl))
        eng_row = QHBoxLayout()
        eng_row.addWidget(self.rb_rc)
        eng_row.addWidget(self.rb_exe)
        eng_row.addWidget(self.rb_ctrl)
        eng_row.addStretch()
        ol.addLayout(eng_row, 2, 1)

        ol.addWidget(QLabel("Cores per plan:"), 3, 0)
        self.spn_cores = QSpinBox()
        self.spn_cores.setRange(1, 16)
        self.spn_cores.setValue(2)
        self.spn_cores.setFixedWidth(60)
        self.spn_cores.setToolTip("CPU cores per HEC-RAS instance (RAS Commander only)")
        ol.addWidget(self.spn_cores, 3, 1)
        lay.addWidget(og)

        # ── Run log ───────────────────────────────────────────────────────────
        lay.addWidget(QLabel("Run log:"))
        self.run_log = QTextEdit()
        self.run_log.setReadOnly(True)
        self.run_log.setFont(QFont("Consolas", 9))
        lay.addWidget(self.run_log, 1)

        # ── Progress + control ────────────────────────────────────────────────
        self.prog_bar = QProgressBar()
        self.prog_bar.setRange(0, 0)
        self.prog_bar.setVisible(False)
        lay.addWidget(self.prog_bar)

        ctrl_row = QHBoxLayout()
        self.btn_run = QPushButton("▶  Run Selected Plans")
        self.btn_run.setEnabled(False)
        self.btn_run.clicked.connect(self._start_run)
        self.btn_cancel = QPushButton("■  Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_run)
        ctrl_row.addStretch()
        ctrl_row.addWidget(self.btn_run)
        ctrl_row.addWidget(self.btn_cancel)
        lay.addLayout(ctrl_row)

        self.tabs.addTab(w, "Run Manager")

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 4 — Result Viewer (Flood Raster)
    # ══════════════════════════════════════════════════════════════════════════
    def _build_results_tab(self):
        w = QScrollArea()
        w.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(8)

        # ── Plan HDF selector ─────────────────────────────────────────────────
        pg = QGroupBox("Plan Result HDF")
        pl = QFormLayout(pg)
        pl.setSpacing(6)
        self.rv_plan_combo = QComboBox()
        self.rv_plan_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.rv_plan_combo.currentIndexChanged.connect(self._rv_on_plan_changed)
        pl.addRow("Plan:", self.rv_plan_combo)
        self.rv_area_combo = QComboBox()
        self.rv_area_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        pl.addRow("2D Flow Area:", self.rv_area_combo)
        self.rv_info_lbl = QLabel("—")
        self.rv_info_lbl.setWordWrap(True)
        pl.addRow("Info:", self.rv_info_lbl)
        scan_row = QHBoxLayout()
        btn_scan = QPushButton("↺ Scan for HDFs")
        btn_scan.clicked.connect(self._scan_results)
        scan_row.addWidget(btn_scan)
        scan_row.addStretch()
        pl.addRow("", scan_row)
        lay.addWidget(pg)

        # ── Variable ─────────────────────────────────────────────────────────
        vg = QGroupBox("Variable")
        vgl = QFormLayout(vg)
        vgl.setSpacing(6)
        self.rv_var_combo = QComboBox()
        self.rv_var_combo.addItems([
            "Depth (WSE − terrain)  [m or ft]",
            "Water Surface Elevation (WSE)  [m or ft]",
            "Velocity (face-averaged)  [m/s or ft/s]",
            "Bed Level (terrain elevation)  [m or ft]"])
        self.rv_var_combo.currentIndexChanged.connect(self._rv_on_var_changed)
        vgl.addRow("Variable:", self.rv_var_combo)
        self.rv_time_combo = QComboBox()
        self.rv_time_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        vgl.addRow("Time step:", self.rv_time_combo)
        self.rv_thresh = QLineEdit("0.003")
        self.rv_thresh.setFixedWidth(80)
        self.rv_thresh.setToolTip("HEC-RAS default = 0.003m (Cell Volume Tolerance)")
        vgl.addRow("Dry threshold (m):", self.rv_thresh)
        lay.addWidget(vg)

        # ── Output ───────────────────────────────────────────────────────────
        og = QGroupBox("Output")
        ogl = QFormLayout(og)
        ogl.setSpacing(6)

        self.rv_chk_points = QCheckBox(
            "Point layer — 3D cell centroids (Z = value), from plan HDF")
        self.rv_chk_points.setChecked(True)
        ogl.addRow("", self.rv_chk_points)

        # Cell mesh (plan HDF)
        self.rv_chk_tin = QCheckBox(
            "Cell mesh — exact HDF cell boundary polygons (Z = value), from plan HDF")
        ogl.addRow("", self.rv_chk_tin)
        tin_fmt_row = QHBoxLayout()
        tin_fmt_row.addWidget(QLabel("Mesh format:"))
        self.rv_tin_fmt = QComboBox()
        self.rv_tin_fmt.addItems([
            "GeoPackage (.gpkg)", "Shapefile (.shp)", "GeoJSON (.geojson)"])
        tin_fmt_row.addWidget(self.rv_tin_fmt)
        tin_fmt_row.addStretch()
        self.rv_tin_fmt_row = QWidget()
        self.rv_tin_fmt_row.setLayout(tin_fmt_row)
        self.rv_tin_fmt_row.setVisible(False)
        self.rv_chk_tin.toggled.connect(self.rv_tin_fmt_row.setVisible)
        ogl.addRow("", self.rv_tin_fmt_row)

        self.rv_chk_filter = QCheckBox("Remove dry cells (depth < threshold)")
        self.rv_chk_filter.setChecked(True)
        ogl.addRow("", self.rv_chk_filter)

        lay.addWidget(og)

        self.rv_prog = QProgressBar()
        self.rv_prog.setRange(0, 0)
        self.rv_prog.setVisible(False)
        lay.addWidget(self.rv_prog)

        btn_load = QPushButton("▶  Load to QGIS")
        self.rv_load_btn = btn_load
        btn_load.setEnabled(False)
        btn_load.clicked.connect(self._rv_load)
        lay.addWidget(btn_load)

        # ── RAS Mapper launcher ───────────────────────────────────────────
        mg = QGroupBox("RAS Mapper")
        ml2 = QVBoxLayout(mg)
        ml2.setSpacing(5)
        note = QLabel(
            "Open RASMapper.exe to view plan results, flood extents and stored maps. "
            "Loads the project .rasmap file directly.")
        note.setWordWrap(True)
        ml2.addWidget(note)

        exe_row = QHBoxLayout()
        exe_row.addWidget(QLabel("RASMapper.exe:"))
        self.rv_mapper_exe = QLineEdit()
        self.rv_mapper_exe.setPlaceholderText(
            r"C:\Program Files (x86)\HEC\HEC-RAS\7.0.1\RasMapper.exe  (blank = auto-find)")
        btn_mapper_browse = QPushButton("…")
        btn_mapper_browse.setFixedWidth(28)
        btn_mapper_browse.clicked.connect(self._rv_browse_mapper_exe)
        exe_row.addWidget(self.rv_mapper_exe)
        exe_row.addWidget(btn_mapper_browse)
        ml2.addLayout(exe_row)

        rasmap_row = QHBoxLayout()
        rasmap_row.addWidget(QLabel(".rasmap file:"))
        self.rv_rasmap_path = QLineEdit()
        self.rv_rasmap_path.setPlaceholderText("Auto-detected from project folder…")
        self.rv_rasmap_path.setReadOnly(True)
        btn_rasmap_browse = QPushButton("…")
        btn_rasmap_browse.setFixedWidth(28)
        btn_rasmap_browse.clicked.connect(self._rv_browse_rasmap)
        rasmap_row.addWidget(self.rv_rasmap_path)
        rasmap_row.addWidget(btn_rasmap_browse)
        ml2.addLayout(rasmap_row)

        mapper_btn_row = QHBoxLayout()
        btn_open_mapper = QPushButton("🗺  Open in RAS Mapper")
        btn_open_mapper.clicked.connect(self._rv_open_rasmapper)
        btn_find_mapper = QPushButton("🔍 Auto-find RASMapper.exe")
        btn_find_mapper.setFixedWidth(190)
        btn_find_mapper.clicked.connect(self._rv_find_mapper_exe)
        mapper_btn_row.addWidget(btn_open_mapper)
        mapper_btn_row.addWidget(btn_find_mapper)
        ml2.addLayout(mapper_btn_row)
        lay.addWidget(mg)

        # ── Terrain ──────────────────────────────────────────────────────────
        tg = QGroupBox("🌍 Terrain")
        tgl = QVBoxLayout(tg)
        tgl.setSpacing(5)
        tgl.addWidget(QLabel(
            "Load the terrain surface associated with the project (read from "
            "the .rasmap file's Terrains entry) as a raster layer."))
        self.rv_terrain_lbl = QLabel("—")
        self.rv_terrain_lbl.setWordWrap(True)
        self.rv_terrain_lbl.setStyleSheet("color:#7f8c8d;")
        tgl.addWidget(self.rv_terrain_lbl)
        terrain_btn_row = QHBoxLayout()
        btn_scan_terrain = QPushButton("🔍 Scan for terrain")
        btn_scan_terrain.clicked.connect(self._rv_scan_terrain)
        self.btn_load_terrain = QPushButton("🌍 Load Terrain")
        self.btn_load_terrain.setEnabled(False)
        self.btn_load_terrain.clicked.connect(self._rv_load_terrain)
        terrain_btn_row.addWidget(btn_scan_terrain)
        terrain_btn_row.addWidget(self.btn_load_terrain)
        tgl.addLayout(terrain_btn_row)
        lay.addWidget(tg)

        lay.addStretch()

        w.setWidget(inner)
        self.tabs.addTab(w, "Result Viewer")

    def _apply_raster_style(self, layer, variable="depth"):
        """
        Pseudocolor with explicit class breaks:
          depth    → Blues,    0–5 m  in 0.1 m steps  + ">5 m" overflow
          velocity → RdYlBu,  0–10 m/s in 1 m/s steps + ">10" overflow
          wse      → Spectral, data range (interpolated)
          bed_level→ BrBG,    data range (interpolated)
        """
        try:
            from qgis.core import (QgsRasterShader, QgsColorRampShader,
                                   QgsSingleBandPseudoColorRenderer,
                                   QgsRasterBandStats, QgsStyle)
            from qgis.PyQt.QtGui import QColor

            provider = layer.dataProvider()
            stats = provider.bandStatistics(1, QgsRasterBandStats.All, layer.extent(), 0)
            data_min, data_max = stats.minimumValue, stats.maximumValue
            if data_min == data_max:
                return

            vl = variable.lower()

            ramp = None
            items = []

            def _get_ramp(name):
                r = QgsStyle.defaultStyle().colorRamp(name)
                if not r:
                    for fb in ("Blues", "Reds", "Spectral", "Viridis"):
                        r = QgsStyle.defaultStyle().colorRamp(fb)
                        if r:
                            break
                return r

            if "depth" in vl or variable == "depth":
                # Blues: 0-1 m (0.2 step) | 1-3 m (0.5 step) | >3 m overflow
                ramp = _get_ramp("Blues")
                _fine = [(round(i * 0.2, 1), round((i + 1) * 0.2, 1)) for i in range(5)]
                _coarse = [(1.0 + i * 0.5, 1.0 + (i + 1) * 0.5) for i in range(4)]
                depth_breaks = _fine + _coarse
                N_d = len(depth_breaks)
                for idx_d, (lo, hi) in enumerate(depth_breaks):
                    t = idx_d / (N_d - 1)
                    col = ramp.color(t) if ramp else QColor(int(255 * (1 - t)), int(255 * (1 - t)), 255)
                    items.append(QgsColorRampShader.ColorRampItem(
                        lo, col, f"{lo:.1f}-{hi:.1f} m"))
                items.append(QgsColorRampShader.ColorRampItem(
                    3.0, QColor(8, 48, 107), "> 3.0 m"))

            elif "veloc" in vl or variable == "velocity":
                # RdYlBu reversed: 0-5 m/s (0.5 step) | 5-10 m/s (1.0 step) | >10 overflow
                ramp = _get_ramp("RdYlBu")
                vel_breaks = (
                    [(i * 0.5, (i + 1) * 0.5) for i in range(10)] + [(5.0 + i, 6.0 + i) for i in range(5)])
                N_v = len(vel_breaks)
                for idx_v, (lo, hi) in enumerate(vel_breaks):
                    t = 1.0 - idx_v / (N_v - 1)
                    col = ramp.color(t) if ramp else QColor(
                        int(255 * idx_v / N_v), 0, int(255 * (1 - idx_v / N_v)))
                    items.append(QgsColorRampShader.ColorRampItem(
                        lo, col, f"{lo:.1f}-{hi:.1f} m/s"))
                items.append(QgsColorRampShader.ColorRampItem(
                    10.0, QColor(103, 0, 31), "> 10.0 m/s"))

            elif "wse" in vl or "water" in vl or variable == "wse":
                ramp = _get_ramp("Spectral")
                n_cls = 20
                for i in range(n_cls + 1):
                    t = i / n_cls
                    v = data_min + t * (data_max - data_min)
                    col = ramp.color(1.0 - t) if ramp else QColor(int(255 * t), 0, int(255 * (1 - t)))
                    items.append(QgsColorRampShader.ColorRampItem(v, col, f"{v:.3f} m"))

            elif "bed" in vl or variable == "bed_level":
                ramp = _get_ramp("BrBG")
                n_cls = 20
                for i in range(n_cls + 1):
                    t = i / n_cls
                    v = data_min + t * (data_max - data_min)
                    col = ramp.color(t) if ramp else QColor(int(200 * t), int(150 * t), 0)
                    items.append(QgsColorRampShader.ColorRampItem(v, col, f"{v:.2f} m"))

            else:
                ramp = _get_ramp("Blues")
                n_cls = 20
                for i in range(n_cls + 1):
                    t = i / n_cls
                    v = data_min + t * (data_max - data_min)
                    col = ramp.color(t) if ramp else QColor(int(255 * (1 - t)), int(255 * (1 - t)), 255)
                    items.append(QgsColorRampShader.ColorRampItem(v, col, f"{v:.3f}"))

            shader = QgsRasterShader()
            color_ramp = QgsColorRampShader()
            color_ramp.setColorRampType(QgsColorRampShader.Discrete
                                        if items[-1].label.startswith(">")
                                        else QgsColorRampShader.Interpolated)
            color_ramp.setClip(True)
            color_ramp.setColorRampItemList(items)
            shader.setRasterShaderFunction(color_ramp)

            renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
            renderer.setClassificationMin(items[0].value)
            renderer.setClassificationMax(items[-1].value)
            layer.setRenderer(renderer)
            layer.triggerRepaint()
        except Exception as e:
            self.append_log(f"Raster style: {e}", "WARNING")

    def _rv_find_rasmap(self):
        """Auto-find .rasmap file in the project folder."""
        if not self._project_data:
            return
        d = self._project_data
        folder = d["folder"]
        base = os.path.splitext(os.path.basename(d["prj_path"]))[0]
        # Try basename.rasmap first
        rasmap = os.path.join(folder, base + ".rasmap")
        if not os.path.isfile(rasmap):
            # Scan for any .rasmap in project folder
            for f in os.listdir(folder):
                if f.lower().endswith(".rasmap"):
                    rasmap = os.path.join(folder, f)
                    break
            else:
                rasmap = ""
        self.rv_rasmap_path.setText(rasmap)
        if rasmap:
            self.append_log(f"Found .rasmap: {os.path.basename(rasmap)}", "INFO")
            self._rv_scan_terrain()

    def _rv_browse_rasmap(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Select .rasmap file",
            self._project_data["folder"] if self._project_data else "",
            "RASMapper files (*.rasmap);;All Files (*)")
        if p:
            self.rv_rasmap_path.setText(p)

    def _rv_scan_terrain(self):
        """Scan the .rasmap file for terrain layer entries.

        HEC-RAS's Terrain.hdf is an index/metadata file only — it lists which
        GeoTIFF tiles make up the terrain and their priority, but is NOT
        itself a raster GDAL can open. HEC-RAS always creates a sibling
        Terrain.vrt file specifically for use in external GIS software
        (this is documented in the HEC-RAS Reference Manual's Terrain Layer
        section), so when a .hdf entry is found we look for and use its
        .vrt counterpart instead.
        """
        rasmap_path = self.rv_rasmap_path.text().strip()
        if not rasmap_path or not os.path.isfile(rasmap_path):
            self.rv_terrain_lbl.setText(
                "⚠ No .rasmap file found — load a project first (or browse to one above).")
            self.btn_load_terrain.setEnabled(False)
            return
        try:
            from .rasmap_reader import find_terrains
        except ImportError:
            from rasmap_reader import find_terrains
        terrains = find_terrains(rasmap_path)
        if not terrains:
            self.rv_terrain_lbl.setText(
                "⚠ No <Terrains> entry found in this .rasmap file.")
            self.btn_load_terrain.setEnabled(False)
            self._rv_terrain_info = None
            return

        # Substitute .hdf references with their .vrt sibling (GDAL-readable)
        for t in terrains:
            if t["path"].lower().endswith(".hdf"):
                vrt_path = os.path.splitext(t["path"])[0] + ".vrt"
                if os.path.isfile(vrt_path):
                    t["path"] = vrt_path
                    t["exists"] = True
                else:
                    t["exists"] = False
                    t["_no_vrt"] = True

        valid = [t for t in terrains if t["exists"]]
        chosen = valid[0] if valid else terrains[0]
        self._rv_terrain_info = chosen

        if chosen["exists"]:
            status = "✅"
            self.rv_terrain_lbl.setText(f"{status} {chosen['name']}: {chosen['path']}")
        elif chosen.get("_no_vrt"):
            status = "⚠"
            self.rv_terrain_lbl.setText(
                f"⚠ Found terrain reference '{chosen['name']}' but no matching "
                f".vrt file next to it — QGIS/GDAL cannot open HEC-RAS's "
                f".hdf terrain index directly. Open RAS Mapper once to "
                f"regenerate the .vrt, or load the terrain's source GeoTIFF(s) "
                f"manually in QGIS.")
        else:
            status = "⚠ file not found at"
            self.rv_terrain_lbl.setText(f"{status} {chosen['name']}: {chosen['path']}")

        self.btn_load_terrain.setEnabled(chosen["exists"])
        self.append_log(
            f"Terrain found: {chosen['name']} → {chosen['path']} "
            f"({'exists' if chosen['exists'] else 'MISSING/unsupported'})",
            "INFO" if chosen["exists"] else "WARNING")

    def _rv_load_terrain(self):
        """Load the scanned terrain .vrt/.tif as a QGIS raster layer.

        Only GDAL-readable raster formats are supported (.tif/.tiff/.vrt/.img).
        HEC-RAS's Terrain.hdf is never loaded directly — see _rv_scan_terrain.
        """
        info = getattr(self, "_rv_terrain_info", None)
        if not info or not info.get("exists"):
            self.rv_terrain_lbl.setText("⚠ Scan for terrain first.")
            return
        path = info["path"]
        ext = os.path.splitext(path)[1].lower()
        from qgis.core import QgsRasterLayer, QgsProject

        if ext not in (".tif", ".tiff", ".vrt", ".img"):
            self.rv_terrain_lbl.setText(
                f"⚠ Unsupported terrain file type for QGIS: {ext}. "
                f"Only .tif/.tiff/.vrt/.img are supported.")
            return

        try:
            lyr = QgsRasterLayer(path, info["name"])

            if not lyr.isValid():
                self.rv_terrain_lbl.setText(
                    f"❌ Could not load terrain as raster — QGIS/GDAL couldn't "
                    f"open: {path}")
                self.append_log(
                    f"Terrain load failed (invalid raster): {path}", "ERROR")
                return

            QgsProject.instance().addMapLayer(lyr)
            self._qct_track_layer(lyr)
            self.rv_terrain_lbl.setText(f"✅ Loaded: {info['name']}")
            self.append_log(f"Terrain layer loaded: {path}", "SUCCESS")
            if self.iface:
                self.iface.mapCanvas().refresh()
        except Exception as e:
            self.append_log(f"Terrain load error: {e}", "ERROR")
            self.rv_terrain_lbl.setText(f"❌ Error: {e}")

    def _rv_browse_mapper_exe(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Select RASMapper.exe", "",
            "Executable (*.exe);;All Files (*)")
        if p:
            self.rv_mapper_exe.setText(p)

    def _rv_find_mapper_exe(self):
        """Search common HEC-RAS install paths for RASMapper.exe."""
        import glob
        candidates = []
        for base in [
            r"C:\Program Files (x86)\HEC\HEC-RAS",
            r"C:\Program Files\HEC\HEC-RAS",
            r"D:\Program Files\HEC\HEC-RAS",
            r"D:\Program Files (x86)\HEC\HEC-RAS",
        ]:
            candidates += glob.glob(os.path.join(base, "*", "RasMapper.exe"))
            candidates += glob.glob(os.path.join(base, "*", "RASMapper.exe"))
        # Also check same folder as Ras.exe
        ras_exe = self.txt_ras_exe.text().strip()
        if ras_exe:
            mapper = os.path.join(os.path.dirname(ras_exe), "RasMapper.exe")
            if not os.path.isfile(mapper):
                mapper = os.path.join(os.path.dirname(ras_exe), "RASMapper.exe")
            if os.path.isfile(mapper):
                candidates.insert(0, mapper)
        if candidates:
            # Pick newest version
            found = sorted(candidates, reverse=True)[0]
            self.rv_mapper_exe.setText(found)
            self.append_log(f"Found RASMapper: {found}", "SUCCESS")
        else:
            QMessageBox.warning(self, "Not Found",
                                "RASMapper.exe not found in common HEC-RAS paths.\n"
                                "Use Browse to locate it manually.")

    def _rv_open_rasmapper(self):
        """Launch RASMapper.exe with the project .rasmap file."""
        rasmap = self.rv_rasmap_path.text().strip()
        if not rasmap or not os.path.isfile(rasmap):
            # Try auto-find
            self._rv_find_rasmap()
            rasmap = self.rv_rasmap_path.text().strip()
        if not rasmap or not os.path.isfile(rasmap):
            QMessageBox.warning(self, "No .rasmap File",
                                "No .rasmap file found or selected.\n"
                                "Browse to select the project .rasmap file.")
            return

        mapper_exe = self.rv_mapper_exe.text().strip()
        if not mapper_exe:
            self._rv_find_mapper_exe()
            mapper_exe = self.rv_mapper_exe.text().strip()
        if not mapper_exe or not os.path.isfile(mapper_exe):
            QMessageBox.warning(self, "RASMapper Not Found",
                                "RASMapper.exe not found.\n"
                                "Use Auto-find or Browse to locate it.")
            return

        try:
            import subprocess
            # Use list form (no shell=True) — avoids shell injection risk and
            # correctly handles paths with spaces on Windows without quoting.
            self.append_log(
                f"Launching RASMapper: {mapper_exe} {rasmap}", "INFO")
            subprocess.Popen(
                [mapper_exe, rasmap],
                cwd=os.path.dirname(rasmap))
            self.append_log(
                f"RASMapper opened: {os.path.basename(rasmap)}", "SUCCESS")
        except Exception as e:
            QMessageBox.critical(self, "Launch Error", str(e))
            self.append_log(f"RASMapper launch error: {e}", "ERROR")

    def _rv_read_hdf_info(self, hdf_path):
        """Read 2D areas, cell count, timesteps, threshold from plan HDF."""
        h5py = _get_h5py()
        if not h5py:
            return None, None, None, None, 0.003
        try:
            areas, stamps, n_cells, threshold = [], [], 0, 0.003
            with h5py.File(hdf_path, "r") as hf:
                g = hf.get("Geometry/2D Flow Areas")
                if g:
                    areas = [k for k in g.keys() if isinstance(g[k], h5py.Group) and "Cells Center Coordinate" in g[k]]
                    if areas:
                        area0 = areas[0]
                        n_cells = g[area0]["Cells Center Coordinate"].shape[0]
                        threshold = float(g[area0].attrs.get("Cell Volume Tolerance", 0.003))
                _TS = "Results/Unsteady/Output/Output Blocks/Base Output/Unsteady Time Series"
                ts_ds = hf.get(f"{_TS}/Time Date Stamp")
                if ts_ds is not None:
                    stamps = [v.decode() if isinstance(v, bytes) else str(v)
                              for v in ts_ds[:]]
            return areas, stamps, n_cells, threshold, threshold
        except Exception:
            return None, None, None, None, 0.003

    def _refresh_anim_plans(self):
        """Populate animate tab plan combo from current project."""
        self.anim_plan_combo.blockSignals(True)
        self.anim_plan_combo.clear()
        if not self._project_data:
            self.anim_plan_combo.blockSignals(False)
            return
        d = self._project_data
        folder = d["folder"]
        base = os.path.splitext(os.path.basename(d["prj_path"]))[0]
        for p in d["plans"]:
            hdf = os.path.join(folder, f"{base}.{p['short_id']}.hdf")
            if os.path.isfile(hdf):
                lbl = f"{p.get('title', '') or p['short_id']}  [{p['short_id']}]"
                self.anim_plan_combo.addItem(lbl, {
                    "plan_title": p.get("title", "") or p["short_id"],
                    "hdf_path": hdf})
        self.anim_plan_combo.blockSignals(False)
        if self.anim_plan_combo.count():
            self._anim_on_plan_changed(0)
            self.anim_load_btn.setEnabled(True)

    def _rv_on_plan_changed(self, idx):
        p = self.rv_plan_combo.itemData(idx)
        if not p or not os.path.isfile(p.get("hdf_path", "")):
            return
        hdf_path = p["hdf_path"]

        # First try qct_ras_results get_plan_hdf_info (richer info)
        used_rc = False
        try:
            from qgis.utils import plugins
            if "qct_ras_results" in plugins:
                import importlib.util
                import sys as _sys
                spec = importlib.util.find_spec("qct_ras_results")
                if spec:
                    plugin_dir = os.path.dirname(spec.origin)
                    if plugin_dir not in _sys.path:
                        _sys.path.insert(0, plugin_dir)
                    from qct_ras_results.plan_hdf_raster import (
                        get_plan_hdf_info, get_plan_hdf_areas)
                    info = get_plan_hdf_info(hdf_path)
                    areas = get_plan_hdf_areas(hdf_path)
                    if areas:
                        self.rv_area_combo.clear()
                        for a in areas:
                            self.rv_area_combo.addItem(a, a)
                        stamps = info.get("stamps", [])
                        self.rv_time_combo.clear()
                        self.rv_time_combo.addItem("Maximum (peak across all timesteps)", -1)
                        for i, s in enumerate(stamps):
                            self.rv_time_combo.addItem(s, i)
                        ai = info["areas"].get(areas[0], {})
                        thresh = ai.get("threshold", 0.003)
                        self.rv_thresh.setText(str(thresh))
                        n_cells = ai.get("n_cells", 0)
                        n_ts = info.get("n_timesteps", 0)
                        self.rv_info_lbl.setText(
                            f"{n_cells:,} cells · {n_ts} timesteps · "
                            f"{info.get('time_start', '')} → {info.get('time_end', '')}")
                        self.rv_load_btn.setEnabled(True)
                        used_rc = True
        except Exception:
            pass

        if used_rc:
            return

        # Fallback: direct h5py read
        areas, stamps, n_cells, threshold, _ = self._rv_read_hdf_info(hdf_path)
        if areas is None:
            h5py = _get_h5py()
            if not h5py:
                self.rv_info_lbl.setText(
                    "h5py not available — install qct_ras_results plugin "
                    "which includes bundled h5py.")
                self.rv_load_btn.setEnabled(False)
                return
            self.rv_info_lbl.setText("Could not read HDF structure.")
            return

        self.rv_area_combo.clear()
        for a in (areas or []):
            self.rv_area_combo.addItem(a, a)
        self.rv_time_combo.clear()
        self.rv_time_combo.addItem("Maximum (peak across all timesteps)", -1)
        for i, s in enumerate(stamps or []):
            self.rv_time_combo.addItem(s, i)
        if threshold:
            self.rv_thresh.setText(str(threshold))
        n_ts = len(stamps) if stamps else 0
        self.rv_info_lbl.setText(
            f"{n_cells:,} cells · {n_ts} timesteps" if n_cells else
            "HDF read — no 2D areas found")
        self.rv_load_btn.setEnabled(bool(areas))

    # ── Flow Hydrograph extractor ──────────────────────────────────────────────
    def _pp_on_plan_changed(self, idx):
        """Populate the Postprocess area combo when the plan selection changes."""
        p = self.pp_plan_combo.itemData(idx)
        if not p or not os.path.isfile(p.get("hdf_path", "")):
            self.pp_area_combo.clear()
            return
        hdf_path = p["hdf_path"]
        areas, _stamps, _n_cells, _thresh, _ = self._rv_read_hdf_info(hdf_path)
        self.pp_area_combo.clear()
        for a in (areas or []):
            self.pp_area_combo.addItem(a, a)

    def _fh_on_type_changed(self, idx):
        """idx 0 = Draw on map, 1 = Reference Line, 2 = SA/2D Conn, 3 = Face Flow."""
        is_draw = (idx == 0)
        self.btn_fh_draw.setVisible(is_draw)
        self.btn_fh_use_layer.setVisible(is_draw)
        self.btn_fh_scan.setVisible(not is_draw)
        # NOTE: do NOT disable fh_name_combo — on some Qt/PyQt builds a disabled
        # QComboBox's currentData() unreliably returns None even after addItem()
        # with valid userData. Leave it enabled; it's read-only in practice since
        # items are always set programmatically, never typed by the user.
        if is_draw:
            self.fh_name_combo.clear()
            self.fh_name_combo.addItem("(draw a line on the map, or pick an existing layer)", None)

    def _fh_use_existing_layer(self):
        """Let the user pick an existing line layer in the project as the cross-section."""
        from qgis.core import QgsProject, QgsWkbTypes

        line_layers = []
        for lyr_id, lyr in QgsProject.instance().mapLayers().items():
            try:
                if lyr.type() == lyr.VectorLayer and \
                   lyr.geometryType() == QgsWkbTypes.LineGeometry:
                    line_layers.append(lyr)
            except Exception:
                continue

        if not line_layers:
            self.fh_status.setText(
                "⚠ No line layers found in the project. Add or draw one first.")
            return

        names = [f"{ln.name()}  ({ln.featureCount()} feature(s))" for ln in line_layers]
        idx_sel, ok = QInputDialog.getItem(
            self, "Select Line Layer", "Choose a line layer to use as cross-section:",
            names, 0, False)
        if not ok:
            return
        sel_lyr = line_layers[names.index(idx_sel)]

        feats = list(sel_lyr.getFeatures())
        if not feats:
            self.fh_status.setText(f"⚠ Layer '{sel_lyr.name()}' has no features.")
            return

        # If multiple features, let user pick which one (e.g. by id)
        feat = feats[0]
        if len(feats) > 1:
            feat_labels = [f"Feature id={f.id()}" for f in feats]
            idx_f, ok2 = QInputDialog.getItem(
                self, "Select Feature",
                f"Layer has {len(feats)} features — choose one:",
                feat_labels, 0, False)
            if not ok2:
                return
            feat = feats[feat_labels.index(idx_f)]

        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            self.fh_status.setText("⚠ Selected feature has no geometry.")
            return

        # Extract first and last vertex of the line (works for simple 2-point or
        # multi-vertex lines — uses endpoints as the cross-section line)
        if geom.isMultipart():
            parts = geom.asMultiPolyline()
            verts = parts[0] if parts else []
        else:
            verts = geom.asPolyline()
        if len(verts) < 2:
            self.fh_status.setText("⚠ Line geometry has fewer than 2 vertices.")
            return

        p1, p2 = verts[0], verts[-1]
        pts_layer_crs = [(p1.x(), p1.y()), (p2.x(), p2.y())]

        # Transform from the layer's own CRS into the project CRS (same CRS
        # _fh_extract expects, matching what _fh_start_draw produces from the canvas)
        from qgis.core import QgsCoordinateTransform, QgsPointXY
        proj_crs = QgsProject.instance().crs()
        layer_crs = sel_lyr.crs()
        pts_proj_crs = pts_layer_crs
        if layer_crs.isValid() and proj_crs.isValid() and layer_crs != proj_crs:
            try:
                xform = QgsCoordinateTransform(layer_crs, proj_crs, QgsProject.instance())
                pts_proj_crs = [
                    (xform.transform(QgsPointXY(x, y)).x(),
                     xform.transform(QgsPointXY(x, y)).y())
                    for (x, y) in pts_layer_crs]
                self.append_log(
                    f"Cross-section layer CRS ({layer_crs.authid()}) transformed to "
                    f"project CRS ({proj_crs.authid()})", "INFO")
            except Exception as e_xf:
                self.append_log(f"Layer CRS transform failed: {e_xf}", "WARNING")

        self._fh_drawn_line = pts_proj_crs
        self.fh_name_combo.clear()
        self.fh_name_combo.addItem(
            f"From layer '{sel_lyr.name()}': "
            f"({pts_proj_crs[0][0]:.1f},{pts_proj_crs[0][1]:.1f}) → "
            f"({pts_proj_crs[1][0]:.1f},{pts_proj_crs[1][1]:.1f})",
            ("drawn", pts_proj_crs))
        self.fh_status.setText(
            f"✅ Using line from '{sel_lyr.name()}'. "
            f"Click '📊 Plot' to extract and plot the flow hydrograph.")

    def _fh_start_draw(self):
        """Activate map tool: user clicks two points on canvas to define cross-section."""
        if not self.iface:
            self.fh_status.setText("⚠ Map canvas not available.")
            return
        p = self.pp_plan_combo.currentData()
        if not p:
            self.fh_status.setText("⚠ Select a plan first.")
            return

        self.fh_status.setText(
            "✏ Click point 1 on the map, then click point 2 to finish "
            "(right-click to cancel)…")
        canvas = self.iface.mapCanvas()

        def _on_complete(pts):
            try:
                self._fh_drawn_line = pts   # [(x1,y1), (x2,y2)] in project CRS
                self.fh_name_combo.clear()
                self.fh_name_combo.addItem(
                    f"Drawn line: ({pts[0][0]:.1f},{pts[0][1]:.1f}) → "
                    f"({pts[1][0]:.1f},{pts[1][1]:.1f})", ("drawn", pts))
                self.append_log(
                    f"Combo populated: count={self.fh_name_combo.count()}, "
                    f"currentData={self.fh_name_combo.currentData()}", "INFO")
                self.fh_status.setText(
                    "✅ Cross-section drawn. Click '📊 Plot' to extract and plot the flow hydrograph.")
            except Exception as e_combo:
                import traceback as _tb
                self.append_log(
                    f"_on_complete FAILED while populating combo: {e_combo}\n"
                    f"{_tb.format_exc()}", "ERROR")
                self.fh_status.setText(f"❌ Error storing cross-section: {e_combo}")
                return

            # Add a visible line layer so the cross-section stays on the map (cosmetic only)
            try:
                from qgis.core import (QgsVectorLayer, QgsFeature, QgsGeometry,
                                       QgsPointXY, QgsProject)
                from qgis.PyQt.QtGui import QColor as _QC
                crs_id = QgsProject.instance().crs().authid()
                uri = "LineString?crs=" + crs_id if crs_id else "LineString"
                xs_lyr = QgsVectorLayer(uri, "Cross-section (drawn)", "memory")
                if not crs_id:
                    xs_lyr.setCrs(QgsProject.instance().crs())
                feat = QgsFeature()
                feat.setGeometry(QgsGeometry.fromPolylineXY(
                    [QgsPointXY(pts[0][0], pts[0][1]), QgsPointXY(pts[1][0], pts[1][1])]))
                xs_lyr.dataProvider().addFeature(feat)
                xs_lyr.updateExtents()
                sym = xs_lyr.renderer().symbol()
                sym.setColor(_QC(255, 0, 0))
                sym.setWidth(1.0)
                QgsProject.instance().addMapLayer(xs_lyr)
                self._fh_xs_layer = xs_lyr
                self._qct_track_layer(xs_lyr)
            except Exception as _e_lyr:
                self.append_log(f"Cross-section layer draw skipped: {_e_lyr}", "WARNING")
            self.activateWindow()
            self.raise_()

        def _on_cancel():
            self.fh_status.setText("Cross-section drawing cancelled (right-click).")

        self._fh_maptool = _CrossSectionPickTool(
            canvas, _on_complete, _on_cancel, log_fn=self.append_log)

    def _fh_scan(self):
        """Scan the selected plan HDF for reference lines, SA/2D connections, face data."""
        p = self.pp_plan_combo.currentData()
        if not p:
            self.fh_status.setText("⚠ Load a project and select a plan first.")
            return
        hdf_path = p["hdf_path"]
        h5py = _get_h5py()
        if not h5py:
            self.fh_status.setText("⚠ h5py not available — pip install h5py in OSGeo4W Shell")
            return

        _TS = ("Results/Unsteady/Output/Output Blocks/Base Output"
               "/Unsteady Time Series")
        _SUMM = ("Results/Unsteady/Output/Output Blocks/Base Output"
                 "/Summary Output")
        area = self.pp_area_combo.currentText()
        src_type = self.fh_type_combo.currentIndex()
        self.fh_name_combo.clear()

        try:
            with h5py.File(hdf_path, "r") as hf:
                if src_type == 1:   # Reference Lines
                    # HEC-RAS 6.x: Results/Unsteady/.../2D Flow Areas/{area}/Reference Lines/...
                    # HEC-RAS 7.x: same path with possible nesting
                    paths = [
                        f"{_TS}/2D Flow Areas/{area}/Reference Lines",
                        f"{_TS}/Reference Lines",
                        f"{_SUMM}/2D Flow Areas/{area}/Reference Lines",
                    ]
                    found = []
                    for path in paths:
                        g = hf.get(path)
                        if g is None:
                            continue
                        for name in g.keys():
                            item = g[name]
                            # Each reference line is a group containing "Flow"
                            if isinstance(item, h5py.Group) and "Flow" in item:
                                found.append((name, path))
                            elif isinstance(item, h5py.Dataset):
                                found.append((name, path))
                    if found:
                        for name, path in found:
                            self.fh_name_combo.addItem(name, (path, name))
                        self.fh_status.setText(
                            f"✅ Found {len(found)} reference line(s)")
                    else:
                        self.fh_name_combo.addItem("(none found — check plan has reference lines)", None)
                        self.fh_status.setText(
                            "⚠ No reference lines found. Define them in RAS Mapper and re-run.")

                elif src_type == 2:  # SA/2D Connections
                    paths = [
                        f"{_TS}/SA 2D Area Connections",
                        f"{_TS}/Storage Area Connections",
                        f"{_TS}/2D Flow Areas/{area}/SA 2D Area Connections",
                    ]
                    found = []
                    for path in paths:
                        g = hf.get(path)
                        if g is None:
                            continue
                        for name in g.keys():
                            item = g[name]
                            if isinstance(item, (h5py.Group, h5py.Dataset)):
                                found.append((name, path))
                    if found:
                        for name, path in found:
                            self.fh_name_combo.addItem(name, (path, name))
                        self.fh_status.setText(f"✅ Found {len(found)} SA/2D connection(s)")
                    else:
                        self.fh_name_combo.addItem("(none found)", None)
                        self.fh_status.setText("⚠ No SA/2D connections found in this plan.")

                elif src_type == 3:   # Face Flow — all faces summed
                    face_path = f"{_TS}/2D Flow Areas/{area}/Face Flow"
                    ds = hf.get(face_path)
                    if ds is not None:
                        shape = ds.shape
                        self.fh_name_combo.addItem(
                            f"All faces — {area}  (shape {shape})", (face_path, "all"))
                        self.fh_status.setText(
                            f"✅ Face Flow found: shape {shape}  "
                            f"({'nTime×nFaces' if shape[0] < shape[1] else 'nFaces×nTime'})")
                    else:
                        self.fh_name_combo.addItem("(Face Flow dataset not found)", None)
                        self.fh_status.setText("⚠ No Face Flow in this plan HDF.")
        except Exception as e:
            self.fh_status.setText(f"❌ Scan error: {e}")
            self.append_log(f"Flow scan error: {e}", "ERROR")

    def _fh_extract(self):
        """Extract flow hydrograph and load as a QGIS non-spatial table + export CSV."""
        p = self.pp_plan_combo.currentData()
        if not p:
            self.fh_status.setText("⚠ Select a plan first.")
            return
        h5py = _get_h5py()
        if not h5py:
            self.fh_status.setText("⚠ h5py not available.")
            return

        item_data = self.fh_name_combo.currentData()
        if item_data is None:
            # Fallback read path — currentData() can be unreliable on some
            # Qt builds; try itemData(currentIndex()) directly as well.
            idx_cur = self.fh_name_combo.currentIndex()
            if idx_cur >= 0:
                item_data = self.fh_name_combo.itemData(idx_cur)
            self.append_log(
                f"Combo fallback read: index={idx_cur}, "
                f"itemData={item_data}", "INFO")
        # Ultimate fallback for Draw mode: use self._fh_drawn_line directly,
        # bypassing the combo entirely, since that attribute is set reliably
        # by _on_complete regardless of any QComboBox quirks.
        if item_data is None and self.fh_type_combo.currentIndex() == 0 \
           and getattr(self, "_fh_drawn_line", None):
            item_data = ("drawn", self._fh_drawn_line)
            self.append_log(
                "Using self._fh_drawn_line directly (combo data was unavailable)",
                "INFO")
        if item_data is None:
            src_type_check = self.fh_type_combo.currentIndex()
            if src_type_check == 0:
                self.fh_status.setText(
                    "⚠ No cross-section defined yet. Click '✏ Draw cross-section on map' "
                    "or '📍 Use existing layer' first.")
            else:
                self.fh_status.setText("⚠ No valid source selected — run Scan first.")
            return

        hdf_path = p["hdf_path"]
        plan_name = p.get("plan_title", "Plan")
        area = self.pp_area_combo.currentText()
        src_type = self.fh_type_combo.currentIndex()
        ds_path, name = item_data
        _TS = ("Results/Unsteady/Output/Output Blocks/Base Output"
               "/Unsteady Time Series")

        try:
            import numpy as _np
            with h5py.File(hdf_path, "r") as hf:
                # ── Read timestamps ───────────────────────────────────────────
                stamps = []
                ds_ts = hf.get(f"{_TS}/Time Date Stamp")
                if ds_ts is not None:
                    stamps = [v.decode("utf-8", "replace").strip()
                              if isinstance(v, bytes) else str(v).strip()
                              for v in ds_ts]
                if not stamps:
                    ds_t = hf.get(f"{_TS}/Time")
                    if ds_t is not None:
                        stamps = [f"{float(h):.4f} hr" for h in ds_t]

                # ── Max-timestep subsampling ────────────────────────────────────
                # Limits how many timesteps get read/calculated/plotted, evenly
                # spaced across the full duration (not truncated to the start).
                # This is the main speed control for long simulations.
                max_steps = self.pp_max_steps.value()
                n_ts_full = len(stamps) if stamps else None
                ts_idx = None   # None = use all timesteps, no subsampling
                if n_ts_full and n_ts_full > max_steps:
                    ts_idx = _np.linspace(0, n_ts_full - 1, max_steps).round().astype(int)
                    ts_idx = _np.unique(ts_idx)
                    self.append_log(
                        f"Limiting {n_ts_full} timesteps to {len(ts_idx)} "
                        f"(evenly subsampled) for faster extraction/plotting", "INFO")

                def _subsample_time(arr):
                    """Subsample a (nTime, nCols) array along axis 0 using ts_idx,
                    if ts_idx is set. No-op otherwise."""
                    if ts_idx is None or arr is None:
                        return arr
                    return arr[ts_idx, :] if arr.shape[0] >= len(ts_idx) else arr

                # ── Read flow data ────────────────────────────────────────────
                flow_arr = None
                col_names = []

                if src_type == 0:   # ── Drawn cross-section (user line on map) ──
                    if ds_path != "drawn":
                        self.fh_status.setText("⚠ Draw a cross-section first.")
                        return
                    line_pts = name   # (ds_path, line_pts) was stored as ("drawn", pts)
                    g = hf.get(f"Geometry/2D Flow Areas/{area}")
                    if g is None:
                        self.fh_status.setText(f"⚠ 2D area '{area}' not found in geometry.")
                        return

                    # Transform drawn points from project CRS into the HDF's native CRS,
                    # since the user draws/picks a layer in whatever CRS the QGIS project uses.
                    from qgis.core import (QgsCoordinateReferenceSystem,
                                           QgsCoordinateTransform, QgsProject,
                                           QgsPointXY)
                    hdf_crs = QgsCoordinateReferenceSystem()
                    proj_wkt = get_projection(hdf_path)
                    if proj_wkt:
                        hdf_crs.createFromWkt(proj_wkt)
                        if hdf_crs.isValid():
                            self.append_log(
                                f"HDF projection read from file: {hdf_crs.authid()} "
                                f"({hdf_crs.description()})", "INFO")
                        else:
                            self.append_log(
                                f"HDF projection WKT found but failed to parse: "
                                f"{proj_wkt[:120]}...", "WARNING")
                    else:
                        self.append_log(
                            "No projection WKT found anywhere in this HDF "
                            "(checked root attribute and per-area dataset)", "WARNING")

                    canvas_crs = QgsProject.instance().crs()
                    line_pts_hdf = line_pts

                    if not hdf_crs.isValid():
                        # IMPORTANT: do NOT silently assume project CRS == HDF CRS.
                        # That assumption previously masked real CRS mismatches.
                        # Instead, proceed without transforming and let the
                        # bounding-box sanity check below catch any mismatch
                        # explicitly, with a clear diagnostic message.
                        self.append_log(
                            "Could not determine HDF's native CRS — proceeding "
                            "without coordinate transform. If the bbox check below "
                            "fails, the project CRS and model CRS likely differ; "
                            "set the project CRS to match your model "
                            "(Project ▸ Properties ▸ CRS) and try again.", "WARNING")
                        self.fh_status.setText(
                            "⚠ Could not read the model's CRS from this HDF. "
                            "If extraction fails below, set your QGIS project CRS "
                            "to match the HEC-RAS model's CRS and retry.")

                    self.append_log(
                        f"Cross-section CRS check: project={canvas_crs.authid()} "
                        f"({canvas_crs.description()}), HDF="
                        f"{hdf_crs.authid() if hdf_crs.isValid() else 'UNKNOWN'} "
                        f"({hdf_crs.description() if hdf_crs.isValid() else 'n/a'})", "INFO")

                    if canvas_crs.isValid() and hdf_crs.isValid() and canvas_crs != hdf_crs:
                        try:
                            xform = QgsCoordinateTransform(
                                canvas_crs, hdf_crs, QgsProject.instance())
                            line_pts_hdf = [
                                (xform.transform(QgsPointXY(px, py)).x(),
                                 xform.transform(QgsPointXY(px, py)).y())
                                for (px, py) in line_pts]
                            self.append_log(
                                f"Transformed line: {line_pts} → {line_pts_hdf}", "INFO")
                        except Exception as _e_xf:
                            self.fh_status.setText(
                                f"❌ CRS transform failed: {_e_xf}. "
                                "Check that the project CRS is set correctly.")
                            self.append_log(f"CRS transform FAILED: {_e_xf}", "ERROR")
                            return
                    else:
                        self.append_log(
                            "No CRS transform needed — project and HDF CRS match", "INFO")

                    # ── Sanity check: does the transformed line fall near the mesh? ──
                    fp_coord_check = _np.array(g["FacePoints Coordinate"], dtype=_np.float64)
                    mesh_x0, mesh_x1 = fp_coord_check[:, 0].min(), fp_coord_check[:, 0].max()
                    mesh_y0, mesh_y1 = fp_coord_check[:, 1].min(), fp_coord_check[:, 1].max()
                    (lx1, ly1), (lx2, ly2) = line_pts_hdf
                    line_x0, line_x1 = min(lx1, lx2), max(lx1, lx2)
                    line_y0, line_y1 = min(ly1, ly2), max(ly1, ly2)
                    # Check for any bbox overlap (with small buffer)
                    buf = max(mesh_x1 - mesh_x0, mesh_y1 - mesh_y0) * 0.01
                    _x_ok = mesh_x0 - buf <= line_x1 and line_x0 <= mesh_x1 + buf
                    _y_ok = mesh_y0 - buf <= line_y1 and line_y0 <= mesh_y1 + buf
                    overlaps = _x_ok and _y_ok
                    if not overlaps:
                        hdf_crs_label = hdf_crs.authid() if hdf_crs.isValid() else "UNKNOWN (not found in HDF)"
                        # Rough NZ CRS hint based on coordinate magnitude, since
                        # NZTM2000 (EPSG:2193) easting/northing run ~1.0-2.1M / 4.7-6.2M,
                        # while older Mount Eden / Circuit-based systems (e.g. 2105)
                        # run ~ tens of thousands to low hundreds of thousands.

                        def _crs_hint(x, y):
                            if 1_000_000 < x < 2_200_000 and 4_700_000 < y < 6_300_000:
                                return "looks like NZTM2000 (EPSG:2193)"
                            if 1_000 < x < 800_000 and 1_000 < y < 1_200_000:
                                return "looks like a local NZ Circuit/Mount system (e.g. EPSG:21xx)"
                            return "CRS unclear from magnitude alone"
                        line_hint = _crs_hint(line_x0, line_y0)
                        mesh_hint = _crs_hint(mesh_x0, mesh_y0)
                        self.fh_status.setText(
                            f"❌ Drawn/selected line is far outside the 2D mesh extent.\n"
                            f"Line: X[{line_x0:.1f},{line_x1:.1f}] Y[{line_y0:.1f},{line_y1:.1f}] — {line_hint}\n"
                            f"Mesh: X[{mesh_x0:.1f},{mesh_x1:.1f}] Y[{mesh_y0:.1f},{mesh_y1:.1f}] — {mesh_hint}\n"
                            f"This is almost certainly a CRS mismatch. The model's HDF CRS "
                            f"is {hdf_crs_label}. Set your QGIS project CRS to match "
                            f"(Project ▸ Properties ▸ CRS) and try again.")
                        self.append_log(
                            f"Line/mesh bbox mismatch — line X[{line_x0:.1f},{line_x1:.1f}] "
                            f"Y[{line_y0:.1f},{line_y1:.1f}] ({line_hint}) vs mesh "
                            f"X[{mesh_x0:.1f},{mesh_x1:.1f}] Y[{mesh_y0:.1f},{mesh_y1:.1f}] "
                            f"({mesh_hint}). HDF CRS={hdf_crs_label}", "ERROR")
                        return

                    fp_coord = fp_coord_check
                    face_fp_idx = _np.array(g["Faces FacePoint Indexes"], dtype=_np.int32)
                    normals_len = _np.array(g["Faces NormalUnitVector and Length"], dtype=_np.float64)
                    face_normal = normals_len[:, :2]   # unit normal (nx, ny)

                    (x1, y1), (x2, y2) = line_pts_hdf
                    # Drawn-line direction vector and crossing test via segment intersection

                    def _seg_intersect(p1, p2, p3, p4):
                        """Return True if segment p1-p2 intersects segment p3-p4."""
                        def cross(o, a, b):
                            return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
                        d1 = cross(p3, p4, p1)
                        d2 = cross(p3, p4, p2)
                        d3 = cross(p1, p2, p3)
                        d4 = cross(p1, p2, p4)
                        return ((d1 * d2 < 0) and (d3 * d4 < 0))

                    drawn_p1, drawn_p2 = (x1, y1), (x2, y2)
                    crossed_faces = []
                    crossed_sign = []   # +1 = flow in official HEC-RAS positive direction
                    crossed_width = []   # intersected width per face (full face length;
                    # a straight drawn line crosses a straight face
                    # segment at exactly one point, so the entire
                    # face contributes its full length to flow —
                    # "partial length" only applies to angled/curved
                    # breaklines, which this simple two-point line
                    # tool doesn't produce)
                    line_dx, line_dy = (x2 - x1), (y2 - y1)
                    # Official HEC-RAS convention: positive flow passes through the
                    # profile line from LEFT to RIGHT when looking in the direction
                    # of travel from point 1 to point 2 (i.e. looking "downstream"
                    # along the drawn line). In standard map coordinates (X=east,
                    # Y=north), "right of direction of travel" is a CLOCKWISE
                    # rotation of the travel vector by 90°: (dx,dy) -> (dy,-dx).
                    right_x, right_y = line_dy, -line_dx

                    n_faces = face_fp_idx.shape[0]
                    for fi in range(n_faces):
                        fp1_idx, fp2_idx = face_fp_idx[fi, 0], face_fp_idx[fi, 1]
                        if fp1_idx < 0 or fp2_idx < 0:
                            continue
                        fpx1, fpy1 = fp_coord[fp1_idx]
                        fpx2, fpy2 = fp_coord[fp2_idx]
                        if _seg_intersect(drawn_p1, drawn_p2, (fpx1, fpy1), (fpx2, fpy2)):
                            crossed_faces.append(fi)
                            # Sign: +1 if this face's normal points to the RIGHT of
                            # the drawn line's direction (point1->point2), matching
                            # HEC-RAS's "left to right looking downstream" convention.
                            dot = face_normal[fi, 0] * right_x + face_normal[fi, 1] * right_y
                            crossed_sign.append(1.0 if dot >= 0 else -1.0)
                            crossed_width.append(normals_len[fi, 2])   # full face length

                    if not crossed_faces:
                        self.fh_status.setText(
                            "⚠ Drawn line does not cross any 2D mesh faces. "
                            "Make sure the line passes through the flow area and try again.")
                        return

                    self.append_log(
                        f"Cross-section: {len(crossed_faces)} mesh face(s) crossed", "INFO")

                    signs = _np.array(crossed_sign, dtype=_np.float64)
                    sel = _np.array(crossed_faces, dtype=_np.int64)
                    face_len_all = normals_len[:, 2]   # full face length (m)  # noqa: F841

                    flow_arr = None

                    # Priority 1: Face Flow (m3/s) - direct sum, most accurate.
                    # Q = sum(Q_f), no calculation needed.
                    ds_ff = hf.get(f"{_TS}/2D Flow Areas/{area}/Face Flow")
                    if ds_ff is not None:
                        ff = _np.array(ds_ff, dtype=_np.float64)
                        if ff.shape[0] > ff.shape[1]:
                            ff = ff.T   # ensure (nTime, nFaces)
                        ff = _subsample_time(ff)
                        flow_arr = (ff[:, sel] * signs[_np.newaxis, :]).sum(axis=1, keepdims=True)
                        col_names = [f"FlowAcrossSection (m3/s)  [{len(crossed_faces)} faces, Face Flow]"]
                        self.append_log("Using Face Flow dataset (direct sum)", "INFO")

                    if flow_arr is None:
                        # Priority 2: OFFICIAL HEC-RAS method (matches RAS Mapper
                        # "Profile Line -> Plot Flow" exactly):
                        #   Q = sum(V_face x A_face)
                        # where A_face is read from the precomputed Face Area
                        # hydraulic property table (Faces Area Elevation Info/
                        # Values), evaluated at that face's OWN water surface
                        # elevation (Face Water Surface dataset) -- NOT an
                        # adjacent-cell average. This is the rating-curve table
                        # HEC-RAS itself uses internally:
                        #   A_f(zs) = A_f,i + W_f,i*(zs - zs,i)   for zs,i <= zs < zs,i+1
                        #           = a + b*zs                    for zs >= zs,N
                        # IMPORTANT: this reads "Face Velocity" (normal component
                        # perpendicular to each face) — the dataset RAS Mapper
                        # itself uses for all flux/flow calculations and display
                        # maps. This is NOT the same as the optional "Face Point
                        # (Node) Velocities" output (Node X Vel/Node Y Vel),
                        # which: (a) must be manually enabled in Unsteady Flow
                        # Analysis -> Options -> Output Options -> HDF5 Write
                        # Parameters, (b) is never used by RAS Mapper for
                        # mapping, and (c) is unreliable at wet-dry boundaries
                        # (e.g. a dry levee crest node reports the higher of its
                        # two adjacent cells' velocities rather than a true
                        # average). Face Velocity has none of these caveats and
                        # is always present in standard 2D output.
                        ds_fv = hf.get(f"{_TS}/2D Flow Areas/{area}/Face Velocity")
                        ds_wsf = hf.get(f"{_TS}/2D Flow Areas/{area}/Face Water Surface")
                        fa_info = g.get("Faces Area Elevation Info")     # (nFaces,2): [start,count]
                        fa_vals = g.get("Faces Area Elevation Values")   # (N,2): [elevation,area]
                        if fa_info is not None and fa_vals is not None:
                            self.append_log(
                                "Face Area Elevation table found in plan HDF's embedded "
                                "geometry (Geometry/2D Flow Areas/.../Faces Area Elevation "
                                "Info+Values) — this is the same geometry snapshot used by "
                                "the solver at run time, matching RAS Mapper exactly.", "INFO")

                        if ds_fv is not None and ds_wsf is not None and fa_info is not None and fa_vals is not None:
                            fv = _np.array(ds_fv, dtype=_np.float64)
                            if fv.shape[0] > fv.shape[1]:
                                fv = fv.T   # (nTime, nFaces)
                            fv = _subsample_time(fv)
                            wsf = _np.array(ds_wsf, dtype=_np.float64)
                            if wsf.shape[0] > wsf.shape[1]:
                                wsf = wsf.T   # (nTime, nFaces)
                            wsf = _subsample_time(wsf)
                            fa_info = _np.array(fa_info, dtype=_np.int64)
                            fa_vals = _np.array(fa_vals, dtype=_np.float64)

                            n_ts3 = fv.shape[0]
                            area_per_face_t = _np.zeros((n_ts3, len(sel)), dtype=_np.float64)
                            for j, fi in enumerate(sel):
                                start, count = fa_info[fi]
                                if count <= 0:
                                    continue
                                elevs = fa_vals[start:start + count, 0]
                                areas = fa_vals[start:start + count, 1]
                                # Linear interpolation along the rating curve;
                                # clamp below lowest entry to 0 (dry), extrapolate
                                # above the highest entry using the last segment's
                                # slope (matches "a + b*zs" extrapolation case).
                                area_per_face_t[:, j] = _np.interp(
                                    wsf[:, fi], elevs, areas,
                                    left=0.0, right=areas[-1] if len(areas) else 0.0)

                            fv_signed = fv[:, sel] * signs[_np.newaxis, :]
                            flow_arr = (fv_signed * area_per_face_t).sum(axis=1, keepdims=True)
                            col_names = [f"FlowAcrossSection (m3/s)  "
                                         f"[{len(crossed_faces)} faces, official Q=V×A table]"]
                            self.append_log(
                                "Using official method: Q = Σ(V_face × A_face) with "
                                "A_face from Face Area hydraulic property table "
                                "evaluated at Face Water Surface", "INFO")
                        else:
                            missing = []
                            if ds_fv is None:
                                missing.append("Face Velocity")
                            if ds_wsf is None:
                                missing.append("Face Water Surface")
                            if fa_info is None or fa_vals is None:
                                missing.append("Faces Area Elevation table")
                            self.append_log(
                                f"Official method unavailable — missing: {', '.join(missing)}. "
                                f"Falling back to approximation.", "WARNING")

                    if flow_arr is None:
                        # Priority 3: last-resort approximation only if the official
                        # Face Area table or Face Water Surface isn't stored in this
                        # HDF. Q = sum(V_f x h_f x W_f) using adjacent-cell-averaged
                        # WSE for face depth. Less accurate than Priority 2 — flagged
                        # clearly to the user.
                        ds_fv2 = hf.get(f"{_TS}/2D Flow Areas/{area}/Face Velocity")
                        ds_wsc = hf.get(f"{_TS}/2D Flow Areas/{area}/Water Surface")
                        z_face_min_ds = g.get("Faces Minimum Elevation")

                        if ds_fv2 is not None and ds_wsc is not None:
                            fv2 = _np.array(ds_fv2, dtype=_np.float64)
                            if fv2.shape[0] > fv2.shape[1]:
                                fv2 = fv2.T
                            fv2 = _subsample_time(fv2)
                            wsc = _np.array(ds_wsc, dtype=_np.float64)
                            if wsc.shape[0] > wsc.shape[1]:
                                wsc = wsc.T
                            wsc = _subsample_time(wsc)
                            n_ts4 = fv2.shape[0]

                            cell_fi_all = _np.array(g["Cells Face and Orientation Info"], dtype=_np.int64)
                            cell_fv_all = _np.array(g["Cells Face and Orientation Values"], dtype=_np.int64)
                            n_cells_g = cell_fi_all.shape[0]
                            face_to_cells = {int(fi): [] for fi in sel}
                            for ci in range(n_cells_g):
                                s, c = cell_fi_all[ci]
                                refs = cell_fv_all[s:s + c, 0]
                                for r in refs:
                                    r = int(r)
                                    if r in face_to_cells:
                                        face_to_cells[r].append(ci)

                            wse_face_t = _np.zeros((n_ts4, len(sel)), dtype=_np.float64)
                            for j, fi in enumerate(sel):
                                cells = face_to_cells.get(int(fi), [])
                                if cells:
                                    wse_face_t[:, j] = wsc[:, cells].mean(axis=1)

                            if z_face_min_ds is not None:
                                z_face_min = _np.array(z_face_min_ds, dtype=_np.float64)[sel]
                            else:
                                terrain_ds = g.get("Cells Minimum Elevation")
                                terrain_arr = (_np.array(terrain_ds, dtype=_np.float64)
                                               if terrain_ds is not None else None)
                                z_face_min = _np.zeros(len(sel), dtype=_np.float64)
                                if terrain_arr is not None:
                                    for j, fi in enumerate(sel):
                                        cells = face_to_cells.get(int(fi), [])
                                        if cells:
                                            z_face_min[j] = terrain_arr[cells].mean()

                            depth_face_t = _np.maximum(0.0, wse_face_t - z_face_min[_np.newaxis, :])
                            dry_mask = depth_face_t < 0.003
                            depth_face_t = _np.where(dry_mask, 0.0, depth_face_t)
                            face_width = _np.array(crossed_width, dtype=_np.float64)

                            fv2_signed = fv2[:, sel] * signs[_np.newaxis, :]
                            flow_arr = (
                                fv2_signed * depth_face_t * face_width[_np.newaxis, :]
                            ).sum(axis=1, keepdims=True)
                            col_names = [f"FlowAcrossSection (m3/s)  "
                                         f"[{len(crossed_faces)} faces, V×h×W APPROX]"]
                            self.append_log(
                                "⚠ Used approximation Q=Σ(V×h×W) — official Face Area "
                                "table and/or Face Water Surface not in this HDF. "
                                "Results may differ from RAS Mapper.", "WARNING")

                    if flow_arr is None:
                        ts_group = hf.get(f"{_TS}/2D Flow Areas/{area}")
                        ts_keys = list(ts_group.keys()) if ts_group is not None else []
                        geom_keys = [k for k in g.keys()
                                     if "Face" in k or "Area" in k or "Cell" in k]
                        self.fh_status.setText(
                            "❌ Could not compute cross-section flow — see Log tab "
                            "for available datasets.")
                        self.append_log(
                            f"No usable flow path found.\n"
                            f"  Unsteady Time Series datasets for '{area}': {ts_keys}\n"
                            f"  Geometry keys (Face/Area/Cell) for '{area}': {geom_keys}",
                            "ERROR")
                        return

                elif src_type == 1:   # Reference line
                    grp = hf.get(f"{ds_path}/{name}")
                    if isinstance(grp, h5py.Group):
                        ds_flow = grp.get("Flow") or grp.get("Flow (m3/s)") or grp.get("Total Flow")
                        if ds_flow is not None:
                            flow_arr = _np.array(ds_flow, dtype=_np.float64)
                    if flow_arr is None:
                        ds_flow = hf.get(f"{ds_path}/{name}")
                        if isinstance(ds_flow, h5py.Dataset):
                            flow_arr = _np.array(ds_flow, dtype=_np.float64)
                    if flow_arr is not None:
                        if flow_arr.ndim == 1:
                            col_names = [f"Flow_{name} (m3/s)"]
                            flow_arr = flow_arr.reshape(-1, 1)
                        else:
                            if flow_arr.shape[0] > flow_arr.shape[1]:
                                flow_arr = flow_arr.T
                            col_names = [f"Flow_{name}_s{i + 1} (m3/s)" for i in range(flow_arr.shape[1])]

                elif src_type == 2:  # SA/2D connection
                    grp = hf.get(f"{ds_path}/{name}")
                    if isinstance(grp, h5py.Group):
                        ds_flow = grp.get("Flow") or grp.get("Flow (m3/s)")
                        if ds_flow is not None:
                            flow_arr = _np.array(ds_flow, dtype=_np.float64)
                    if flow_arr is None:
                        ds_flow = hf.get(f"{ds_path}/{name}")
                        if isinstance(ds_flow, h5py.Dataset):
                            flow_arr = _np.array(ds_flow, dtype=_np.float64)
                    if flow_arr is not None:
                        if flow_arr.ndim == 1:
                            col_names = [f"Flow_{name} (m3/s)"]
                            flow_arr = flow_arr.reshape(-1, 1)

                else:   # src_type == 3: Face Flow — sum abs across all faces
                    ds_ff = hf.get(ds_path)
                    if ds_ff is not None:
                        ff = _np.array(ds_ff, dtype=_np.float32)
                        if ff.shape[0] > ff.shape[1]:
                            ff = ff.T
                        ff = _subsample_time(ff)
                        flow_arr = _np.nansum(_np.abs(ff), axis=1).reshape(-1, 1)
                        col_names = [f"TotalFaceFlow_{area} (m3/s)"]
                        self.append_log(
                            f"Face Flow: summed {ff.shape[1]} faces × {ff.shape[0]} timesteps",
                            "INFO")

                if flow_arr is None:
                    self.fh_status.setText("❌ Could not read flow dataset from HDF.")
                    return

                # Catch-all subsampling for source types that read flow_arr
                # directly (Reference Line, SA/2D Connection) without going
                # through the per-priority subsampling above (drawn cross-section
                # and Face-Flow-all paths already subsampled their raw arrays).
                if ts_idx is not None and flow_arr.shape[0] == n_ts_full:
                    flow_arr = flow_arr[ts_idx, :]

                n_ts = flow_arr.shape[0]
                if ts_idx is not None and len(stamps) == n_ts_full:
                    stamps = [stamps[i] for i in ts_idx]
                if len(stamps) != n_ts:
                    stamps = [f"Step {i}" for i in range(n_ts)]

            # ── Store extracted data for plotting + optional CSV save ─────────
            # (No QGIS table layer is created — results go straight to the graph.
            # Use the "Save flow data as CSV" button to export if needed.)
            self._pp_last_stamps = stamps
            self._pp_last_flow_arr = flow_arr
            self._pp_last_col_names = col_names
            self._pp_last_plan_name = plan_name
            self._pp_last_xs_name = name if isinstance(name, str) else "Cross-section"

            self.fh_status.setText(
                f"✅ {n_ts} timestep(s) plotted below. "
                f"Use '💾 Save flow data as CSV' to export.")
            self.append_log(
                f"Flow hydrograph extracted: {n_ts} steps, {flow_arr.shape[1]} column(s)",
                "SUCCESS")

            # Plot into the embedded graph on the Postprocess tab
            self._pp_plot_hydrograph(
                stamps, flow_arr, col_names,
                title=f"{plan_name} — {name if isinstance(name, str) else 'Cross-section'}")

            if self.iface:
                self.iface.mapCanvas().refresh()

        except Exception as e:
            import traceback
            self.append_log(f"Flow extract error: {e}\n{traceback.format_exc()[-300:]}", "ERROR")
            self.fh_status.setText(f"❌ Error: {e}")

    def _rv_on_var_changed(self, idx):
        """Disable time step selector for Bed Level (static terrain — no timestep needed)."""
        is_bed = (idx == 3)
        self.rv_time_combo.setEnabled(not is_bed)
        self.rv_chk_filter.setEnabled(not is_bed)
        if is_bed:
            self.rv_time_combo.setToolTip("Not applicable — Bed Level is static terrain elevation")
        else:
            self.rv_time_combo.setToolTip("")

    def _rv_load(self):
        p = self.rv_plan_combo.itemData(self.rv_plan_combo.currentIndex())
        if not p:
            return
        hdf_path = p.get("hdf_path", "")
        if not os.path.isfile(hdf_path):
            QMessageBox.warning(self, "File Missing", f"HDF not found:\n{hdf_path}")
            return

        area = self.rv_area_combo.currentData() or self.rv_area_combo.currentText()
        t_idx = self.rv_time_combo.currentData() if self.rv_time_combo.count() else -1
        var_idx = self.rv_var_combo.currentIndex()
        variable = ["depth", "wse", "velocity", "bed_level"][var_idx]
        try:
            thresh = float(self.rv_thresh.text().strip() or "0.003")
        except BaseException:
            thresh = 0.003
        do_pts = self.rv_chk_points.isChecked()
        do_tin = self.rv_chk_tin.isChecked()
        filter_dry = self.rv_chk_filter.isChecked() and variable != "bed_level"

        # For Bed Level, time step is irrelevant — always use terrain
        if variable == "bed_level":
            t_idx = None

        # Ensure qct_ras_results is on sys.path
        try:
            import importlib.util as _ilu
            spec = _ilu.find_spec("qct_ras_results")
            if spec:
                pd = os.path.dirname(spec.origin)
                if pd and pd not in sys.path:
                    sys.path.insert(0, pd)
        except Exception:
            pass

        self.rv_load_btn.setEnabled(False)
        self.rv_prog.setVisible(True)
        self.append_log(f"Loading {variable} from {os.path.basename(hdf_path)}…", "STEP")

        try:
            import numpy as np
            import math
            import tempfile
            from qgis.core import (QgsVectorLayer, QgsField, QgsFeature,
                                   QgsGeometry, QgsPoint, QgsProject,
                                   QgsCoordinateReferenceSystem)
            from qgis.PyQt.QtCore import QVariant

            h5py = _get_h5py()
            if not h5py:
                raise ImportError(
                    "h5py not available. Install qct_ras_results plugin "
                    "which includes bundled h5py.")

            # ── Read geometry and CRS from HDF ────────────────────────────────
            with h5py.File(hdf_path, "r") as hf:
                proj = hf.attrs.get("Projection", "")
                if isinstance(proj, bytes):
                    proj = proj.decode("utf-8")

                g = hf[f"Geometry/2D Flow Areas/{area}"]
                centers = np.array(g["Cells Center Coordinate"], dtype=np.float64)
                terrain_raw = np.array(g["Cells Minimum Elevation"], dtype=np.float32)
                terrain_valid = ~np.isnan(terrain_raw)
                terrain = np.where(terrain_valid, terrain_raw, 0.0)
                n_cells = len(terrain)
                threshold = float(g.attrs.get("Cell Volume Tolerance", thresh))

                _S = "Results/Unsteady/Output/Output Blocks/Base Output/Summary Output"
                _T = "Results/Unsteady/Output/Output Blocks/Base Output/Unsteady Time Series"

                # WSE values
                if t_idx is None or t_idx == -1:
                    wse_ds = hf.get(f"{_S}/2D Flow Areas/{area}/Maximum Water Surface")
                    wse_vals = np.array(wse_ds[0], dtype=np.float32) if wse_ds is not None else None
                else:
                    wse_ds = hf.get(f"{_T}/2D Flow Areas/{area}/Water Surface")
                    wse_vals = np.array(wse_ds[t_idx], dtype=np.float32) if wse_ds is not None else None

                if wse_vals is None:
                    raise ValueError(f"No Water Surface data found for area '{area}'")

                # Align sizes
                n = min(n_cells, len(wse_vals))
                centers = centers[:n]
                terrain = terrain[:n]
                wse_vals = wse_vals[:n]

                depth_vals = np.maximum(0.0, wse_vals - terrain)
                wet_mask = depth_vals > threshold

                # Compute output values
                if variable == "depth":
                    values = depth_vals
                    z_vals = depth_vals      # Z = depth
                elif variable == "wse":
                    values = wse_vals
                    z_vals = wse_vals        # Z = WSE elevation
                elif variable == "bed_level":
                    values = terrain[:n]
                    z_vals = terrain[:n]     # Z = bed elevation (static)
                else:  # velocity
                    if t_idx is None or t_idx == -1:
                        fv_ds = hf.get(f"{_S}/2D Flow Areas/{area}/Maximum Face Velocity")
                        fv_row = np.array(fv_ds[0], dtype=np.float32) if fv_ds is not None else None
                    else:
                        fv_ds = hf.get(f"{_T}/2D Flow Areas/{area}/Face Velocity")
                        fv_row = np.array(fv_ds[t_idx], dtype=np.float32) if fv_ds is not None else None
                    if fv_row is None:
                        raise ValueError("No Face Velocity data found")
                    fv_abs = np.abs(fv_row)
                    normals = np.array(g["Faces NormalUnitVector and Length"], dtype=np.float32)
                    face_len = normals[:, 2]
                    cell_fi = g["Cells Face and Orientation Info"][:]
                    cell_fv_d = g["Cells Face and Orientation Values"][:]
                    cell_vel = np.zeros(n, dtype=np.float32)
                    for ci in range(n):
                        s, c = cell_fi[ci]
                        refs = cell_fv_d[s:s + c, 0]
                        lens = face_len[refs]
                        tot = lens.sum()
                        cell_vel[ci] = (fv_abs[refs] * lens).sum() / tot if tot > 0 else 0.0
                    values = cell_vel
                    z_vals = cell_vel        # Z = velocity magnitude

                # Apply dry filter (not applicable to bed level)
                if filter_dry and variable != "bed_level":
                    mask = wet_mask
                else:
                    mask = np.ones(n, dtype=bool)

                n_wet = int(mask.sum())
                self.append_log(f"  {n_wet:,} cells selected of {n:,} total", "INFO")

                # ── Read facepoint data for cell mesh export ──
                if do_tin:
                    # Use direct FacePoint index list per cell (no face walking)
                    cell_fp_idx = g["Cells FacePoint Indexes"][:]  # noqa: F841
                    fp_coord = g["FacePoints Coordinate"][:]  # noqa: F841

            xs = centers[:, 0]
            ys = centers[:, 1]

            # ── CRS from HDF ──────────────────────────────────────────────────
            qgs_crs = QgsCoordinateReferenceSystem()
            if proj:
                qgs_crs.createFromWkt(proj)
            if not qgs_crs.isValid():
                qgs_crs = QgsProject.instance().crs()
                self.append_log("CRS from HDF not valid — using project CRS", "WARNING")
            else:
                self.append_log(f"CRS: {qgs_crs.authid() or 'WKT'}", "INFO")

            plan_name = p.get("plan_title", os.path.basename(hdf_path))

            # ── POINT LAYER (3D — Z = value) ──────────────────────────────────
            if do_pts and n_wet > 0:
                self.append_log("Building 3D point layer…", "STEP")
                lname = f"{plan_name}_{area}_{variable}"
                field = variable[:10]
                uri = "PointZ"
                lyr = QgsVectorLayer(uri, lname, "memory")
                lyr.setCrs(qgs_crs)
                pr = lyr.dataProvider()
                pr.addAttributes([QgsField(field, QVariant.Double)])
                lyr.updateFields()

                xs_w = xs[mask]
                ys_w = ys[mask]
                z_w = z_vals[mask]
                v_w = values[mask]
                feats = []
                fidx = 0  # noqa: F841
                for i in range(len(xs_w)):
                    v = float(v_w[i])
                    z = float(z_w[i])
                    if math.isnan(v) or v < -9000:
                        continue
                    feat = QgsFeature()
                    feat.setGeometry(QgsGeometry(QgsPoint(float(xs_w[i]), float(ys_w[i]), z)))
                    feat.setAttributes([v])
                    feats.append(feat)
                    if len(feats) >= 5000:
                        pr.addFeatures(feats)
                        feats = []
                if feats:
                    pr.addFeatures(feats)
                lyr.updateExtents()
                QgsProject.instance().addMapLayer(lyr)
                self._qct_track_layer(lyr)
                self._apply_point_style(lyr, variable)  # after addMapLayer
                self.append_log(f"3D point layer: {lname} ({lyr.featureCount():,} pts)", "SUCCESS")

            # ── Cell mesh export ──────────────────────────────────────────────
            if do_tin and n_wet > 0:
                self.append_log("Exporting cell mesh (FacePoints)…", "STEP")
                try:
                    # Use local plan_hdf_raster (bundled in this plugin)
                    from .plan_hdf_raster import (
                        export_cell_polygons as _exp_poly,
                        get_plan_hdf_areas as _get_areas)
                    # Verify area exists in HDF
                    _areas = _get_areas(hdf_path)
                    self.append_log(f"Cell mesh: areas={_areas}, requested={area}", "INFO")
                    if area not in _areas:
                        area = _areas[0] if _areas else area
                        self.append_log(f"Using area: {area}", "INFO")
                    export_face_tin = _exp_poly
                    fmt_map = {"GeoPackage (.gpkg)": "gpkg", "Shapefile (.shp)": "shp",
                               "GeoJSON (.geojson)": "geojson"}
                    fmt = fmt_map.get(self.rv_tin_fmt.currentText(), "gpkg")
                    ext = {"gpkg": ".gpkg", "shp": ".shp", "geojson": ".geojson"}[fmt]
                    # mkstemp is secure (no TOCTOU race); close the fd immediately
                    # since export_face_tin opens the path itself.
                    _tin_fd, tin_path = tempfile.mkstemp(
                        prefix=f"QCT_{plan_name[:12]}_{variable}_mesh", suffix=ext)
                    os.close(_tin_fd)
                    export_face_tin(
                        hdf_path, tin_path,
                        area_name=area,
                        variable=variable,
                        time_index=None if (t_idx == -1 or t_idx is None) else t_idx,
                        dry_threshold=thresh,
                        fmt=fmt,
                        log_fn=lambda m, lvl: self.append_log(m, lvl))
                    tin_lyr = QgsVectorLayer(tin_path,
                                             f"{plan_name}_{area}_{variable}_cells", "ogr")
                    if tin_lyr.isValid():
                        QgsProject.instance().addMapLayer(tin_lyr)
                        self._qct_track_layer(tin_lyr)
                        self._qct_track_temp_path(tin_path)
                        self._apply_point_style(tin_lyr, variable)
                        if self.iface:
                            self.iface.layerTreeView().refreshLayerSymbology(tin_lyr.id())
                        self.append_log(
                            f"Cell mesh loaded: {tin_lyr.featureCount():,} cells", "SUCCESS")
                    else:
                        self.append_log(f"Cell mesh file saved: {tin_path}", "WARNING")
                except ImportError:
                    self.append_log(
                        "Cell mesh export needs plan_hdf_raster module (bundled).", "WARNING")
                except Exception as e_tin:
                    self.append_log(f"Cell mesh error: {e_tin}", "ERROR")

        except Exception as e:
            import traceback
            self.append_log(f"Load error: {e}\n{traceback.format_exc()[-400:]}", "ERROR")
        finally:
            self.rv_load_btn.setEnabled(True)
            self.rv_prog.setVisible(False)
            if self.iface:
                self.iface.mapCanvas().refresh()

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 5 — Animate
    # ══════════════════════════════════════════════════════════════════════════

    def _build_animate_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(8)
        note = QLabel(
            "Loads all time steps for a variable into memory, then steps through "
            "them live on the QGIS canvas.")
        note.setWordWrap(True)
        lay.addWidget(note)

        ag = QGroupBox("Animation Setup")
        agl = QFormLayout(ag)
        agl.setSpacing(6)
        self.anim_plan_combo = QComboBox()
        self.anim_plan_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.anim_plan_combo.currentIndexChanged.connect(self._anim_on_plan_changed)
        agl.addRow("Plan:", self.anim_plan_combo)
        self.anim_area_combo = QComboBox()
        self.anim_area_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        agl.addRow("2D Flow Area:", self.anim_area_combo)
        self.anim_var_combo = QComboBox()
        # HEC-RAS timeseries: only "Water Surface" and "Face Velocity" stored
        # "Depth" is computed from WSE - terrain per frame
        self.anim_var_combo.addItems([
            "Depth (WSE − terrain)",
            "Water Surface (WSE)",
            "Velocity"])
        agl.addRow("Variable:", self.anim_var_combo)
        speed_row = QHBoxLayout()
        self.anim_speed = QSlider(Qt.Horizontal)
        self.anim_speed.setRange(100, 3000)
        self.anim_speed.setValue(500)
        self.anim_speed_lbl = QLabel("500 ms")
        self.anim_speed.valueChanged.connect(
            lambda v: self.anim_speed_lbl.setText(f"{v} ms"))
        speed_row.addWidget(self.anim_speed)
        speed_row.addWidget(self.anim_speed_lbl)
        agl.addRow("Frame speed:", speed_row)
        lay.addWidget(ag)

        self.anim_time_lbl = QLabel("—")
        self.anim_time_lbl.setAlignment(Qt.AlignCenter)
        self.anim_time_lbl.setStyleSheet(
            "font-size:13px; font-weight:bold; color:#1a5276; "
            "background:#eaf4fb; border-radius:4px; padding:4px 8px;")
        lay.addWidget(self.anim_time_lbl)
        self.anim_slider = QSlider(Qt.Horizontal)
        self.anim_slider.setRange(0, 0)
        self.anim_slider.sliderMoved.connect(self._anim_seek)
        lay.addWidget(self.anim_slider)
        self.anim_prog = QProgressBar()
        self.anim_prog.setRange(0, 0)
        self.anim_prog.setVisible(False)
        lay.addWidget(self.anim_prog)

        btn_row = QHBoxLayout()
        self.anim_load_btn = QPushButton("📥  Load Animation Data")
        self.anim_load_btn.setEnabled(False)
        self.anim_load_btn.clicked.connect(self._load_animation)
        self.anim_play_btn = QPushButton("▶  Play")
        self.anim_play_btn.setEnabled(False)
        self.anim_play_btn.clicked.connect(self._toggle_anim)
        self.anim_stop_btn = QPushButton("■  Stop")
        self.anim_stop_btn.setEnabled(False)
        self.anim_stop_btn.clicked.connect(self._stop_anim)
        btn_row.addWidget(self.anim_load_btn)
        btn_row.addWidget(self.anim_play_btn)
        btn_row.addWidget(self.anim_stop_btn)
        lay.addLayout(btn_row)
        lay.addStretch()
        self.tabs.addTab(w, "Animate")

    def _anim_on_plan_changed(self, idx):
        p = self.anim_plan_combo.itemData(idx)
        if not p or not os.path.isfile(p.get("hdf_path", "")):
            return
        self.anim_area_combo.clear()
        try:
            h5py = _get_h5py()
            if not h5py:
                self.append_log("h5py not available for animation", "WARNING")
                return
            with h5py.File(p["hdf_path"], "r") as hf:
                g = hf.get("Geometry/2D Flow Areas")
                if g:
                    for k in g.keys():
                        # Skip datasets (Attributes, Cell Info, etc.) — only Groups
                        if not isinstance(g[k], h5py.Group):
                            continue
                        if "Cells Center Coordinate" in g[k]:
                            self.anim_area_combo.addItem(k, k)
                # Fallback: check results path if geometry areas not found
                if self.anim_area_combo.count() == 0:
                    _TS = "Results/Unsteady/Output/Output Blocks/Base Output/Unsteady Time Series/2D Flow Areas"
                    ts_g = hf.get(_TS)
                    if ts_g:
                        for k in ts_g.keys():
                            self.anim_area_combo.addItem(k, k)
        except Exception as e:
            self.append_log(f"Animate area scan: {e}", "WARNING")
        self.anim_load_btn.setEnabled(self.anim_area_combo.count() > 0)

    def _load_animation(self):
        from qgis.PyQt.QtCore import QThread, pyqtSignal
        p = self.anim_plan_combo.currentData()
        area = self.anim_area_combo.currentText()
        var = self.anim_var_combo.currentText()
        if not p or not area:
            QMessageBox.warning(self, "No Data", "Select a plan and area.")
            return

        self.anim_prog.setVisible(True)
        self.anim_load_btn.setEnabled(False)
        self.append_log(f"Loading animation: {var} / {area}…", "STEP")

        class AnimWorker(QThread):
            done = pyqtSignal(object, object, object, object)

            def __init__(self, hdf_path, area, var):
                super().__init__()
                self.hdf_path = hdf_path
                self.area = area
                self.var = var

            def run(self):
                try:
                    import numpy as _np
                    h5py = _get_h5py()
                    _TS = ("Results/Unsteady/Output/Output Blocks/Base Output"
                           "/Unsteady Time Series/2D Flow Areas")
                    xs, ys = read_cell_centroids(self.hdf_path, self.area)
                    crs = get_projection(self.hdf_path)

                    # Read timestamps directly — log raw structure for diagnostics
                    stamps = []
                    _BASE = ("Results/Unsteady/Output/Output Blocks/Base Output"
                             "/Unsteady Time Series")
                    try:
                        with h5py.File(self.hdf_path, "r") as _hf:
                            # Try 1: Time Date Stamp strings
                            ds_ts = _hf.get(f"{_BASE}/Time Date Stamp")
                            if ds_ts is not None and len(ds_ts) > 0:
                                stamps = [
                                    v.decode("utf-8", "replace").strip()
                                    if isinstance(v, bytes) else str(v).strip()
                                    for v in ds_ts]
                            # Try 2: numeric Time (hours) + start from plan info
                            if not stamps or not stamps[0]:
                                import datetime as _dtime
                                ds_t = _hf.get(f"{_BASE}/Time")
                                if ds_t is not None:
                                    times_hr = _np.array(ds_t, dtype=_np.float64)
                                    start_dt = None
                                    for grp_path in ("Plan Data/Plan Information",
                                                     "Plan Data/Plan Parameters"):
                                        pg = _hf.get(grp_path)
                                        if pg is None:
                                            continue
                                        for attr in ("Simulation Start Time",
                                                     "Start Date", "Starting Date",
                                                     "Plan Start Date"):
                                            raw_s = pg.attrs.get(attr)
                                            if raw_s is None:
                                                continue
                                            s = (raw_s.decode("utf-8", "replace").strip()
                                                 if isinstance(raw_s, bytes)
                                                 else str(raw_s).strip())
                                            for fmt in ("%d%b%Y %H:%M:%S",
                                                        "%d%b%Y %H:%M",
                                                        "%d%b%Y",
                                                        "%Y-%m-%d %H:%M:%S",
                                                        "%Y-%m-%dT%H:%M:%S"):
                                                try:
                                                    start_dt = _dtime.datetime.strptime(
                                                        s, fmt)
                                                    break
                                                except Exception:
                                                    pass
                                            if start_dt:
                                                break
                                        if start_dt:
                                            break
                                    if start_dt:
                                        stamps = [
                                            (start_dt + _dtime.timedelta(
                                                hours=float(h))
                                             ).strftime("%d%b%Y %H:%M:%S")
                                            for h in times_hr]
                                    else:
                                        # No start date — use hour offsets directly
                                        stamps = [f"{h:.2f} hr" for h in times_hr]
                    except Exception as _e_ts:  # noqa: F841
                        stamps = []   # non-fatal
                    n_cells = len(xs)

                    if self.var == "Velocity":
                        # Reads "Face Velocity" (normal-to-face component) and
                        # does our own length-weighted face->cell averaging
                        # below. This deliberately avoids the optional "Face
                        # Point (Node) Velocities" HDF output, which RAS Mapper
                        # itself never uses for mapping and which is unreliable
                        # at wet-dry boundaries (reports the higher of two
                        # adjacent cells' velocities rather than a true average
                        # at dry nodes like a levee crest). Face Velocity has
                        # no such caveat and is always present in standard
                        # 2D output, regardless of optional HDF5 write settings.
                        with h5py.File(self.hdf_path, "r") as hf:
                            fv_ds = hf.get(f"{_TS}/{self.area}/Face Velocity")
                            if fv_ds is None:
                                raise ValueError("No 'Face Velocity' dataset in results HDF")
                            fv_raw = _np.array(fv_ds, dtype=_np.float32)
                            # Ensure shape (n_timesteps, n_faces)
                            if fv_raw.ndim == 2 and fv_raw.shape[0] > fv_raw.shape[1]:
                                fv_raw = fv_raw.T
                            n_ts, n_faces = fv_raw.shape
                            # Geometry for face→cell averaging
                            g = hf[f"Geometry/2D Flow Areas/{self.area}"]
                            normals = _np.array(g["Faces NormalUnitVector and Length"], dtype=_np.float32)
                            face_len = normals[:, 2]
                            cell_fi = _np.array(g["Cells Face and Orientation Info"], dtype=_np.int32)
                            cell_fv = _np.array(g["Cells Face and Orientation Values"], dtype=_np.int32)

                        # Vectorised face→cell averaging across ALL timesteps at once
                        cell_vel = _np.zeros((n_ts, n_cells), dtype=_np.float32)
                        fv_abs = _np.abs(fv_raw)   # (n_ts, n_faces)
                        for ci in range(n_cells):
                            s, c = cell_fi[ci]
                            refs = cell_fv[s:s + c, 0]
                            refs = refs[refs < n_faces]
                            if len(refs) == 0:
                                continue
                            lens = face_len[refs]
                            tot = lens.sum()
                            if tot > 0:
                                cell_vel[:, ci] = (fv_abs[:, refs] * lens).sum(axis=1) / tot
                        data = _np.where(cell_vel > 1e-4, cell_vel, _np.nan)
                    else:
                        hdf_var = {
                            "Depth (WSE − terrain)": "Water Surface",
                            "Water Surface (WSE)": "Water Surface",
                        }.get(self.var, "Water Surface")
                        data = read_all_timeseries(self.hdf_path, self.area, hdf_var)
                        # Ensure (n_timesteps, n_cells)
                        if data.ndim == 2 and data.shape[1] != n_cells:
                            data = data.T
                        if data.shape[1] != n_cells:
                            raise ValueError(f"Data shape {data.shape} doesn't match {n_cells} cells")

                    self.done.emit(data, xs, ys, (crs, stamps))
                except Exception:
                    import traceback
                    self.done.emit(None, None, None, (None, [traceback.format_exc()]))

        self._anim_worker = AnimWorker(p["hdf_path"], area, var)

        def _on_done(data, xs, ys, extra):
            crs, stamps = extra
            self.anim_prog.setVisible(False)
            self.anim_load_btn.setEnabled(True)
            if data is None or xs is None:
                msg = stamps[0] if stamps else "Unknown error"
                QMessageBox.warning(self, "Load Error", str(msg))
                return
            import numpy as _np
            import tempfile
            import math as _math
            n_cells = len(xs)

            # ── Compute display values ────────────────────────────────────────
            terrain = None
            dry_thresh = 0.003
            try:
                h5py = _get_h5py()
                if h5py:
                    with h5py.File(p["hdf_path"], "r") as _hf:
                        _g = _hf.get(f"Geometry/2D Flow Areas/{area}")
                        if _g and "Cells Minimum Elevation" in _g:
                            terrain = _np.array(_g["Cells Minimum Elevation"],
                                                dtype=_np.float32)[:n_cells]
                            terrain = _np.where(_np.isnan(terrain), 0.0, terrain)
                        _t = _g.attrs.get("Cell Volume Tolerance", 0.003) if _g else 0.003
                        dry_thresh = float(_t)
                        if dry_thresh > 1.0 or dry_thresh < 0.0001:
                            dry_thresh = 0.003
            except Exception:
                pass

            if var == "Depth (WSE − terrain)" and terrain is not None:
                data_display = _np.maximum(0.0, data - terrain[_np.newaxis, :])
                data_display = _np.where(data_display > dry_thresh, data_display, _np.nan)
            elif var == "Water Surface (WSE)":
                if terrain is not None:
                    depth_chk = _np.maximum(0.0, data - terrain[_np.newaxis, :])
                    data_display = _np.where(depth_chk > dry_thresh, data, _np.nan)
                else:
                    data_display = _np.where(data > 0, data, _np.nan)
            else:
                data_display = data   # velocity pre-computed in worker

            if "Depth" in var:
                field = "depth"
            elif "Water" in var:
                field = "wse"
            elif "Veloc" in var:
                field = "velocity"
            else:
                field = "value"

            from qgis.core import QgsCoordinateReferenceSystem, QgsProject, QgsVectorLayer
            qgs_crs = QgsCoordinateReferenceSystem()
            if crs:
                qgs_crs.createFromWkt(crs)
            if not qgs_crs.isValid() and self._project_crs_wkt:
                qgs_crs.createFromWkt(self._project_crs_wkt)
            if not qgs_crs.isValid():
                qgs_crs = QgsProject.instance().crs()

            lname = f"ANIM_{p.get('plan_title', '')[:10]}_{area}_{field}"

            # ── Build cell mesh GeoPackage (polygons, one per cell) ───────────
            # This gives true filled polygons — rule-based renderer colours them
            # correctly every frame without classification drift.
            lyr = None
            # mkstemp is secure; close fd immediately since OGR opens the path itself.
            _gpkg_fd, gpkg_path = tempfile.mkstemp(
                prefix=f"QCT_ANIM_{p.get('plan_title', '')[:10]}_", suffix=".gpkg")
            os.close(_gpkg_fd)
            try:
                from .plan_hdf_raster import export_cell_polygons
                frame0_vals = data_display[0]  # noqa: F841
                # Write frame-0 values into the GeoPackage
                export_cell_polygons(
                    p["hdf_path"], gpkg_path,
                    area_name=area,
                    variable=field,            # "depth","wse","velocity","value"
                    time_index=0,
                    dry_threshold=0.0,         # export ALL cells, dry=nan handled by renderer
                    fmt="gpkg",
                    log_fn=lambda m, lvl: None)  # silent
                lyr = QgsVectorLayer(gpkg_path, lname, "ogr")
                if not lyr.isValid():
                    raise RuntimeError(f"GeoPackage layer invalid: {gpkg_path}")
                lyr.setCrs(qgs_crs)
                self.append_log(
                    f"Animation mesh: {lyr.featureCount():,} cell polygons built", "INFO")
            except Exception as e_mesh:
                self.append_log(
                    f"Cell mesh build failed ({e_mesh}) — falling back to point layer", "WARNING")
                # ── Fallback: memory point layer ──────────────────────────────
                from qgis.core import (QgsVectorLayer, QgsField, QgsFeature,
                                       QgsGeometry, QgsPointXY)
                from qgis.PyQt.QtCore import QVariant
                lyr = QgsVectorLayer("Point", lname, "memory")
                lyr.setCrs(qgs_crs)
                pr = lyr.dataProvider()
                pr.addAttributes([QgsField(field, QVariant.Double)])
                lyr.updateFields()
                frame0 = data_display[0]
                feats = []
                for i in range(n_cells):
                    feat = QgsFeature()
                    feat.setGeometry(QgsGeometry.fromPointXY(
                        QgsPointXY(float(xs[i]), float(ys[i]))))
                    v = float(frame0[i])
                    feat.setAttributes(
                        [None if (_math.isnan(v) or v < -9000) else v])
                    feats.append(feat)
                    if len(feats) >= 5000:
                        pr.addFeatures(feats)
                        feats = []
                if feats:
                    pr.addFeatures(feats)
                lyr.updateExtents()
                gpkg_path = None   # signal _anim_seek to use changeAttributeValues path

            # ── Add to QGIS and style ─────────────────────────────────────────
            QgsProject.instance().addMapLayer(lyr)
            self._qct_track_layer(lyr)
            if gpkg_path:
                self._qct_track_temp_path(gpkg_path)
            if field in ("depth", "velocity"):
                self._apply_point_style(lyr, var)
            else:
                import numpy as _np2
                _valid = data_display[~_np2.isnan(data_display)]
                _vmin = float(_valid.min()) if len(_valid) else 0.0
                _vmax = float(_valid.max()) if len(_valid) else 15.0
                self._apply_point_style(lyr, var, v_min=_vmin, v_max=_vmax)
            if self.iface:
                self.iface.layerTreeView().refreshLayerSymbology(lyr.id())
                self.iface.mapCanvas().refresh()

            # Build fid→cell-index map from GeoPackage feature order
            # GeoPackage fids start at 1 and match cell insertion order
            self._anim_layer = lyr
            self._anim_gpkg = gpkg_path   # None = point layer fallback
            self._anim_fid_map = {feat.id(): feat.id() - 1
                                  for feat in lyr.getFeatures()}
            self._anim_data = data_display
            self._anim_field = field
            self._anim_stamps = stamps or []
            self._anim_index = 0
            if stamps:
                self.append_log(
                    f"Timestamp sample[0]: {repr(stamps[0])}", "INFO")
            self.anim_slider.setRange(0, data_display.shape[0] - 1)
            self.anim_play_btn.setEnabled(True)
            self.anim_stop_btn.setEnabled(True)
            self.append_log(
                f"Animation ready: {data_display.shape[0]} frames × "
                f"{n_cells:,} cells", "SUCCESS")
            self._anim_seek(0)

        self._anim_worker.done.connect(_on_done)
        self._anim_worker.start()

    def _toggle_anim(self):
        if self._anim_timer.isActive():
            self._anim_timer.stop()
            self.anim_play_btn.setText("▶  Play")
        else:
            self._anim_timer.setInterval(self.anim_speed.value())
            self._anim_timer.start()
            self.anim_play_btn.setText("⏸  Pause")
            # Keep timer interval in sync with speed slider while playing
            self.anim_speed.valueChanged.connect(self._anim_timer.setInterval)

    def _stop_anim(self):
        self._anim_timer.stop()
        self.anim_play_btn.setText("▶  Play")
        self._anim_index = 0
        self._anim_seek(0)

    def _anim_step(self):
        if self._anim_data is None:
            return
        self._anim_index = (self._anim_index + 1) % self._anim_data.shape[0]
        self._anim_seek(self._anim_index)

    def _anim_seek(self, idx):
        if self._anim_layer is None or self._anim_data is None:
            return
        self._anim_index = idx
        # Block slider signal to avoid re-entrancy
        self.anim_slider.blockSignals(True)
        self.anim_slider.setValue(idx)
        self.anim_slider.blockSignals(False)

        ntime = self._anim_data.shape[0]
        idx = max(0, min(idx, ntime - 1))
        frame = self._anim_data[idx]

        # Timestamp display — try several HEC-RAS stamp formats
        if idx < len(self._anim_stamps):
            ts = self._anim_stamps[idx]
            ts_disp = ts.strip()
            # Try parsing as date string
            for fmt in ("%d%b%Y %H:%M:%S", "%d%b%Y %H:%M", "%d%b%Y",
                        "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
                        "%m/%d/%Y %H:%M:%S"):
                try:
                    from datetime import datetime as _dt
                    dt = _dt.strptime(ts.strip(), fmt)
                    ts_disp = dt.strftime("%d %b %Y  |  %H:%M:%S")
                    break
                except Exception:
                    pass
            # If still looks like a raw stamp (not reformatted), show as-is
        else:
            ts_disp = "—"
        self.anim_time_lbl.setText(
            f"🕒  {ts_disp}    ·    Frame {idx + 1} / {ntime}")

        # Update cell values for this frame
        pr = self._anim_layer.dataProvider()
        fidx = self._anim_layer.fields().indexOf(self._anim_field)
        if fidx < 0:
            self.append_log(
                f"Animation: field '{self._anim_field}' not found in layer", "WARNING")
            return

        fid_map = getattr(self, "_anim_fid_map", None)
        attrs = {}
        if fid_map:
            for fid, ci in fid_map.items():
                if 0 <= ci < len(frame):
                    v = float(frame[ci])
                    attrs[fid] = {fidx: None if (math.isnan(v) or v < -9000) else v}
        else:
            for feat in self._anim_layer.getFeatures():
                ci = feat.id() - 1
                if 0 <= ci < len(frame):
                    v = float(frame[ci])
                    attrs[feat.id()] = {fidx: None if (math.isnan(v) or v < -9000) else v}

        # Use editing session so GeoPackage polygon layer redraws correctly
        gpkg = getattr(self, "_anim_gpkg", None)
        if gpkg:
            # GeoPackage path: startEditing → changeAttributeValues → commit
            self._anim_layer.startEditing()
            pr.changeAttributeValues(attrs)
            self._anim_layer.commitChanges(stopEditing=True)
        else:
            # Memory point layer path
            pr.changeAttributeValues(attrs)

        self._anim_layer.triggerRepaint()
        if self.iface:
            self.iface.mapCanvas().refresh()

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 6 — Log
    # ══════════════════════════════════════════════════════════════════════════
    # ══════════════════════════════════════════════════════════════════════════
    # TAB 6 — Postprocess (Flow Hydrograph extractor + graph)
    # ══════════════════════════════════════════════════════════════════════════
    def _build_postprocess_tab(self):
        w = QScrollArea()
        w.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(8)

        # ── Plan selector (independent of Result Viewer) ───────────────────────
        pg = QGroupBox("Plan Selection")
        pgl = QFormLayout(pg)
        pgl.setSpacing(5)
        self.pp_plan_combo = QComboBox()
        self.pp_plan_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.pp_plan_combo.currentIndexChanged.connect(self._pp_on_plan_changed)
        pgl.addRow("Plan:", self.pp_plan_combo)
        self.pp_area_combo = QComboBox()
        self.pp_area_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        pgl.addRow("2D Flow Area:", self.pp_area_combo)
        lay.addWidget(pg)

        # ── Flow Hydrograph Extractor ─────────────────────────────────────────
        fg = QGroupBox("📈 Flow Hydrograph")
        fl = QVBoxLayout(fg)
        fl.setSpacing(5)
        fl.addWidget(QLabel(
            "Extract flow rate vs time from HEC-RAS reference lines, "
            "SA/2D connections, aggregate face flow, or a cross-section you draw on the map."))

        fl2 = QFormLayout()
        fl2.setSpacing(5)
        self.fh_type_combo = QComboBox()
        self.fh_type_combo.addItems([
            "Draw cross-section on map (anywhere, no pre-defined reference line needed)",
            "Reference Line (flow hydrograph at named cross-section)",
            "SA/2D Connection (flow through internal connection)",
            "Face Flow — sum across 2D area faces (all faces)"])
        self.fh_type_combo.currentIndexChanged.connect(self._fh_on_type_changed)
        fl2.addRow("Source type:", self.fh_type_combo)

        self.fh_name_combo = QComboBox()
        self.fh_name_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.fh_name_combo.setPlaceholderText("Click Scan to populate…")
        fl2.addRow("Name / location:", self.fh_name_combo)

        self.pp_max_steps = QSpinBox()
        self.pp_max_steps.setRange(10, 100000)
        self.pp_max_steps.setValue(1440)   # ~1 min steps over 24 hr by default
        self.pp_max_steps.setToolTip(
            "Limits how many timesteps are read/calculated and plotted. "
            "Lower this to speed up extraction on long simulations; data is "
            "evenly subsampled across the full duration, not truncated.")
        fl2.addRow("Max timesteps to plot:", self.pp_max_steps)
        fl.addLayout(fl2)

        fh_btn_row = QHBoxLayout()
        self.btn_fh_scan = QPushButton("🔍 Scan available")
        self.btn_fh_scan.clicked.connect(self._fh_scan)
        self.btn_fh_draw = QPushButton("✏ Draw cross-section on map")
        self.btn_fh_draw.clicked.connect(self._fh_start_draw)
        self.btn_fh_draw.setVisible(False)
        self.btn_fh_use_layer = QPushButton("📍 Use existing layer")
        self.btn_fh_use_layer.setToolTip(
            "Pick a line feature from any line/polygon-boundary layer already in the project")
        self.btn_fh_use_layer.clicked.connect(self._fh_use_existing_layer)
        self.btn_fh_use_layer.setVisible(False)
        btn_fh_export = QPushButton("📊 Plot")
        btn_fh_export.clicked.connect(self._fh_extract)
        fh_btn_row.addWidget(self.btn_fh_scan)
        fh_btn_row.addWidget(self.btn_fh_draw)
        fh_btn_row.addWidget(self.btn_fh_use_layer)
        fh_btn_row.addWidget(btn_fh_export)
        fl.addLayout(fh_btn_row)

        self.fh_status = QLabel("")
        self.fh_status.setWordWrap(True)
        fl.addWidget(self.fh_status)
        lay.addWidget(fg)
        self._fh_on_type_changed(0)   # set initial button visibility

        # ── Graph window ────────────────────────────────────────────────────
        gg = QGroupBox("📉 Hydrograph Plot")
        gl = QVBoxLayout(gg)
        gl.setSpacing(5)

        self._pp_canvas = None
        self._pp_figure = None
        self._pp_chart_container = QWidget()
        self._pp_chart_layout = QVBoxLayout(self._pp_chart_container)
        self._pp_chart_layout.setContentsMargins(0, 0, 0, 0)
        self._pp_chart_placeholder = QLabel(
            "No hydrograph plotted yet. Extract a flow hydrograph above — "
            "the graph will appear here automatically.")
        self._pp_chart_placeholder.setAlignment(Qt.AlignCenter)
        self._pp_chart_placeholder.setMinimumHeight(320)
        self._pp_chart_placeholder.setStyleSheet("color:#7f8c8d;")
        self._pp_chart_layout.addWidget(self._pp_chart_placeholder)
        gl.addWidget(self._pp_chart_container)

        graph_btn_row = QHBoxLayout()
        btn_save_png = QPushButton("💾 Save plot as PNG")
        btn_save_png.clicked.connect(self._pp_save_plot)
        btn_save_csv = QPushButton("📄 Save flow data as CSV")
        btn_save_csv.clicked.connect(self._pp_save_csv)
        btn_clear_plot = QPushButton("🗑 Clear plot")
        btn_clear_plot.clicked.connect(self._pp_clear_plot)
        graph_btn_row.addWidget(btn_save_png)
        graph_btn_row.addWidget(btn_save_csv)
        graph_btn_row.addWidget(btn_clear_plot)
        graph_btn_row.addStretch()
        gl.addLayout(graph_btn_row)

        lay.addWidget(gg)
        lay.addStretch()

        w.setWidget(inner)
        self.tabs.addTab(w, "Postprocess")

    def _pp_plot_hydrograph(self, stamps, flow_arr, col_names, title=""):
        """Render the extracted flow hydrograph into the embedded matplotlib canvas."""
        try:
            import matplotlib
            matplotlib.use("Qt5Agg")
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

            # Remove placeholder / previous canvas
            for i in reversed(range(self._pp_chart_layout.count())):
                item = self._pp_chart_layout.itemAt(i)
                wdg = item.widget()
                if wdg is not None:
                    wdg.setParent(None)

            fig = Figure(figsize=(7, 3.2), tight_layout=True)
            ax = fig.add_subplot(111)

            n_ts = flow_arr.shape[0]
            x = list(range(n_ts))
            # Try to use real timestamps for x tick labels (thin them out)
            use_time_labels = (len(stamps) == n_ts and n_ts > 0)

            for j, cn in enumerate(col_names):
                y = flow_arr[:, j]
                ax.plot(x, y, marker="", linewidth=1.6, label=cn)

            ax.set_xlabel("Time step" if not use_time_labels else "Time")
            ax.set_ylabel("Flow (m³/s)")
            ax.set_title(title or "Flow Hydrograph")
            ax.grid(True, linestyle="--", alpha=0.4)
            ax.axhline(0, color="#888888", linewidth=0.8)
            if len(col_names) > 1:
                ax.legend(fontsize=8)

            if use_time_labels and n_ts > 1:
                step = max(1, n_ts // 8)
                tick_idx = list(range(0, n_ts, step))
                tick_labels = [stamps[i] for i in tick_idx]
                ax.set_xticks(tick_idx)
                ax.set_xticklabels(tick_labels, rotation=30, ha="right", fontsize=7)

            canvas = FigureCanvas(fig)
            canvas.setMinimumHeight(340)
            self._pp_chart_layout.addWidget(canvas)
            self._pp_canvas = canvas
            self._pp_figure = fig
            canvas.draw()
        except ImportError as e_imp:
            self.append_log(
                f"matplotlib not available — install via OSGeo4W Shell: "
                f"pip install matplotlib  ({e_imp})", "WARNING")
            self._pp_chart_placeholder.setText(
                "⚠ matplotlib not installed. Run 'pip install matplotlib' "
                "in OSGeo4W Shell to enable graphing.")
        except Exception as e:
            import traceback
            self.append_log(
                f"Plot error: {e}\n{traceback.format_exc()[-300:]}", "ERROR")

    def _pp_save_plot(self):
        if self._pp_figure is None:
            self.fh_status.setText("⚠ No plot to save yet.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Plot", "", "PNG Image (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            self._pp_figure.savefig(path, dpi=150)
            self.append_log(f"Plot saved: {path}", "SUCCESS")
        except Exception as e:
            self.append_log(f"Save plot error: {e}", "ERROR")

    def _pp_save_csv(self):
        """Export the last extracted flow hydrograph to a user-chosen CSV path."""
        if self._pp_last_flow_arr is None:
            self.fh_status.setText("⚠ No flow data to save yet — extract a hydrograph first.")
            return
        default_name = f"Flow_{(self._pp_last_plan_name or 'Plan')[:12]}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Flow Data as CSV", default_name, "CSV Files (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            import csv
            import math as _math2
            stamps = self._pp_last_stamps
            flow_arr = self._pp_last_flow_arr
            col_names = self._pp_last_col_names
            n_ts = flow_arr.shape[0]
            with open(path, "w", newline="", encoding="utf-8") as f_csv:
                writer = csv.writer(f_csv)
                writer.writerow(["Timestamp", "Step"] + col_names)
                for i in range(n_ts):
                    row = [stamps[i], i]
                    for j in range(flow_arr.shape[1]):
                        v = float(flow_arr[i, j])
                        row.append("" if _math2.isnan(v) else f"{v:.4f}")
                    writer.writerow(row)
            self.fh_status.setText(f"✅ Saved: {path}")
            self.append_log(f"Flow data CSV saved: {path}", "SUCCESS")
        except Exception as e:
            self.append_log(f"Save CSV error: {e}", "ERROR")
            self.fh_status.setText(f"❌ Save failed: {e}")

    def _pp_clear_plot(self):
        for i in reversed(range(self._pp_chart_layout.count())):
            item = self._pp_chart_layout.itemAt(i)
            wdg = item.widget()
            if wdg is not None:
                wdg.setParent(None)
        self._pp_chart_placeholder = QLabel(
            "No hydrograph plotted yet. Extract a flow hydrograph above — "
            "the graph will appear here automatically.")
        self._pp_chart_placeholder.setAlignment(Qt.AlignCenter)
        self._pp_chart_placeholder.setMinimumHeight(320)
        self._pp_chart_placeholder.setStyleSheet("color:#7f8c8d;")
        self._pp_chart_layout.addWidget(self._pp_chart_placeholder)
        self._pp_canvas = None
        self._pp_figure = None

    def _build_log_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        lay.addWidget(self.log_text)
        btn_row = QHBoxLayout()
        btn_clr = QPushButton("Clear")
        btn_clr.setFixedWidth(80)
        btn_clr.clicked.connect(self.log_text.clear)
        btn_save = QPushButton("Save…")
        btn_save.setFixedWidth(80)
        btn_save.clicked.connect(self._save_log)
        btn_row.addStretch()
        btn_row.addWidget(btn_clr)
        btn_row.addWidget(btn_save)
        lay.addLayout(btn_row)
        self.tabs.addTab(w, "Log")

    # ══════════════════════════════════════════════════════════════════════════
    # Help panel — context-sensitive per active tab
    # ══════════════════════════════════════════════════════════════════════════
    _HELP_CSS = """
        <style>
          body{font-family:"Segoe UI",Arial,sans-serif;font-size:10px;color:#1c2833;margin:8px;}
          h2{color:#1a5276;font-size:12px;margin:6px 0 4px;}
          h3{color:#1a5276;font-size:10px;font-weight:bold;margin:8px 0 3px;
             border-bottom:1px solid #aed6f1;padding-bottom:2px;}
          h4{color:#154360;font-size:10px;margin:5px 0 2px;}
          .step{background:#eaf4fb;border-left:3px solid #1a5276;
                margin:3px 0;padding:4px 7px;border-radius:0 3px 3px 0;}
          .ok  {background:#eafaf1;border-left:3px solid #1e8449;
                margin:3px 0;padding:4px 7px;border-radius:0 3px 3px 0;}
          .warn{background:#fef9e7;border-left:3px solid #d68910;
                margin:3px 0;padding:4px 7px;border-radius:0 3px 3px 0;}
          .err {background:#fdedec;border-left:3px solid #c0392b;
                margin:3px 0;padding:4px 7px;border-radius:0 3px 3px 0;}
          code{background:#eaecee;padding:1px 3px;border-radius:2px;
               font-family:Consolas,monospace;font-size:9px;}
          ul{margin:2px 0;padding-left:16px;}
          li{margin:1px 0;}
          table{border-collapse:collapse;width:100%;margin:3px 0;}
          th{background:#d6eaf8;color:#1a5276;padding:2px 5px;
             border:1px solid #aed6f1;font-size:9px;text-align:left;}
          td{padding:2px 5px;border:1px solid #d5d8dc;font-size:9px;vertical-align:top;}
          tr:nth-child(even) td{background:#f5faff;}
        </style>
    """

    _HELP_TAB0 = """
        <h2>🌊 QCT HEC-RAS Manager <span style="font-size:9px;color:#7f8c8d;">v1.0.0</span></h2>
        <p style="margin:2px 0 6px;">Complete HEC-RAS 2D workflow manager for QGIS — browse
        projects, edit plans, run simulations, load flood results, extract flow
        hydrographs, and animate results without opening HEC-RAS.</p>

        <h3>⚙ Requirements</h3>
        <table>
          <tr><th>Component</th><th>Version</th><th>Notes</th></tr>
          <tr><td>QGIS</td><td>3.16+</td><td>Tested on 3.36–3.44 (Windows)</td></tr>
          <tr><td>Python</td><td>3.12</td><td>Bundled with QGIS</td></tr>
          <tr><td>h5py</td><td>3.x</td><td>Must install — see below</td></tr>
           <tr><td>HEC-RAS</td><td>6.x / 7.x</td><td>Run simulations only; result viewing works without it</td></tr>
        </table>
        <div class="warn">
          ⚠ <b>h5py</b> must be installed before first use. Open <b>OSGeo4W Shell</b> and run:<br>
          <code>pip install h5py</code><br>
          NumPy and SciPy ship with QGIS and need no separate install.
        </div>
        <div class="warn">
          ⚠ <b>RAS Commander</b> is required for parallel runs. Install via
          OSGeo4W Shell: <code>pip install ras-commander</code>. On first use it may show a
          "Verifying SciChart" folder dialog — select any writable folder and click OK.
          The RAS Executable engine works without RAS Commander.
        </div>

        <h3>📦 Installation</h3>
        <div class="step">
          <b>From ZIP</b> (recommended)<br>
          1. <b>Plugins ▸ Manage and Install Plugins… ▸ Install from ZIP</b><br>
          2. Browse to <code>qct_hecras_manager_v1.0.0.zip</code> → <b>Install Plugin</b><br>
          3. Plugin appears under <b>QCivilTools ▸ HEC-RAS Manager</b> and as toolbar 🌊
        </div>
        <div class="step">
          <b>Manual install path</b><br>
          <code>%APPDATA%\\QGIS\\QGIS3\\profiles\\default\\python\\plugins\\qct_hecras\\</code>
        </div>
        <div class="warn">
          ⚠ Do <b>not</b> install into <code>Program Files\\QGIS\\</code> — use the per-user path above.
        </div>

        <h3>🚀 Quick Start</h3>
        <div class="step">
          1. <b>Tab 1</b>: Browse to a <code>.prj</code> file or folder → project loads<br>
          2. <b>Tab 2</b>: Edit plan files and configure HDF5 output variables<br>
          3. <b>Tab 3</b>: Tick plans → Run Selected Plans<br>
          4. <b>Tab 4</b>: Select plan → variable → time step → Load to QGIS<br>
          5. <b>Tab 5</b>: Animate results over time on the QGIS canvas<br>
          6. <b>Tab 6</b>: Extract and plot flow hydrographs at any cross-section
        </div>

        <h3>🔧 Troubleshooting</h3>
        <table>
          <tr><th>Symptom</th><th>Fix</th></tr>
          <tr><td><i>"h5py not available"</i></td>
              <td>Run <code>pip install h5py</code> in OSGeo4W Shell, then restart QGIS.</td></tr>
          <tr><td>Run fails exit code 1</td>
              <td>RAS Executable path wrong — use Auto-find or paste full path to 64-bit <code>Ras.exe</code>.</td></tr>
          <tr><td>Blank / empty layer</td>
              <td>Lower dry threshold or untick <i>Remove dry cells</i>.</td></tr>
          <tr><td>COM bitness error</td>
              <td>Expected — use <b>RAS Executable</b> engine instead.</td></tr>
          <tr><td>Cross-section bbox mismatch</td>
              <td>CRS mismatch — plugin auto-syncs project CRS on load; check Log tab (Tab 7) for details.</td></tr>
          <tr><td>Stale results after reinstall</td>
              <td>Disable → re-enable plugin in Plugin Manager to flush <code>__pycache__</code>.</td></tr>
        </table>
        <p style="color:#7f8c8d;font-size:9px;margin-top:8px;">
          <a href="https://github.com/QCivilTools">github.com/QCivilTools</a>
        </p>
    """
    _HELP_TAB1 = """
        <h2>Tab 2 — Plan Editor</h2>
        <div class="step">
          Select a plan → the raw <code>.pXX</code> file is loaded into the text editor.
          Edit any line directly.
        </div>
        <h3>Buttons</h3>
        <ul>
          <li><b>↺ Reload</b> — discard edits, reload file from disk</li>
          <li><b>💾 Save</b> — write changes to the existing <code>.pXX</code> file</li>
          <li><b>📄 Save As New Plan…</b> — write to a new <code>.pXX</code>, register in <code>.prj</code></li>
          <li><b>🗑 Delete</b> — remove plan file and its <code>.prj</code> entry (with confirmation)</li>
        </ul>
        <h3>Key plan-file fields</h3>
        <table>
          <tr><th>Field</th><th>Example</th><th>Description</th></tr>
          <tr><td><code>Plan Title=</code></td><td>100yr Base</td><td>Name shown in all tabs</td></tr>
          <tr><td><code>Geom File=</code></td><td>g01</td><td>Geometry file suffix</td></tr>
          <tr><td><code>Flow File=</code></td><td>u01</td><td>Unsteady flow file suffix</td></tr>
           <tr><td><code>Simulation Date=</code></td><td>01JAN2000,0000</td><td>Start / end date-time</td></tr>
          <tr><td><code>Computation Interval=</code></td><td>1MIN</td><td>Hydraulic time step</td></tr>
          <tr><td><code>Output Interval=</code></td><td>5MIN</td><td>How often results written to HDF</td></tr>
          <tr><td><code>Run HTab=</code></td><td>-1</td><td>Run geometry pre-processor (-1=Yes, 0=No)</td></tr>
          <tr><td><code>Run UNet=</code></td><td>-1</td><td>Run unsteady simulation (-1=Yes, 0=No)</td></tr>
          <tr><td><code>Run RASMapper=</code></td><td>-1 / 0</td><td>Export RAS Mapper layers after run</td></tr>
        </table>
        <div class="warn">
          ⚠ Run flags: <code>-1</code> = enabled, <code>0</code> = disabled.
        </div>

        <h3>HDF5 Output Variables sub-tab</h3>
        <div class="step">
          Mirrors HEC-RAS's own <i>Output Control Options → HDF5 Write
          Parameters</i> dialog as a checklist. Click <b>🔍 Read current
          selection from plan</b> to tick boxes matching whatever
          <code>HDF Additional Output Variable=</code> lines already exist in
          the loaded plan (this also runs automatically whenever a plan is
          selected or reloaded). Tick/untick boxes, then click
          <b>✅ Apply to plan text</b> to update the Raw Editor text —
          this only edits the in-memory text; click <b>💾 Save</b> afterward
          to write it to disk.
        </div>
        <div class="step">
          Variables marked <b>★</b> (bold, with a blue tip below them) are
          ones this plugin's Flow Hydrograph or Result Viewer features can
          actually use: <code>Face Flow</code> (best — direct flow sum),
          <code>Face Area</code> + <code>Face Water Surface</code> (together,
          enable the official Q=V×A flow method), and <code>Cell Velocity</code>
          (a more direct alternative to this plugin's own face→cell velocity
          averaging, not yet wired in). <code>Face Point (Node) Velocities*</code>
          is flagged with a warning — it's not used by RAS Mapper or this
          plugin for any mapping, and is unreliable at wet-dry boundaries.
        </div>
        <div class="warn">
          ⚠ Each checked variable becomes its own
          <code>HDF Additional Output Variable=&lt;name&gt;</code> line —
          there is no single combined value. Unchecking a variable simply
          removes its line; HEC-RAS treats an absent line as "off". These
          settings only affect <i>future</i> runs of this plan — re-run it
          after changing them for the new variables to appear in its HDF.
        </div>
    """

    _HELP_TAB2 = """
        <h2>Tab 3 — Run Manager</h2>
        <div class="step">
          1. Tick plans to run<br>
          2. Choose <b>Sequential</b> or <b>Parallel</b> mode<br>
          3. Select run engine<br>
          4. Click <b>▶ Run Selected Plans</b>
        </div>
        <div class="step">
          Each plan is copied to an isolated temp folder → simulated →
          results moved back → temp folder deleted. This prevents
          file-locking conflicts in parallel runs.
        </div>
        <h3>Engines</h3>
        <div class="ok">
          ✅ <b>RAS Commander</b> (default) — Python orchestration; supports parallel runs
          and per-plan core-count control. <b>Required package</b> — install via OSGeo4W Shell:
          <code>pip install ras-commander</code>.<br>
          ⚠ On first run RAS Commander may show a "Verifying SciChart" folder-browse dialog —
          this is a one-time DLL licence check. Select any writable folder (e.g. Desktop) and click OK.
          It will not appear again after the DLL path is cached.
        </div>
        <div class="ok">
          ✅ <b>RAS Executable</b> — launches <code>Ras.exe</code> directly. No extra packages.
          Sequential only (parallel not supported without RAS Commander).
        </div>
        <div class="err">
          ✖ <b>RAS Controller (COM)</b> — incompatible with 64-bit QGIS Python
          due to bitness mismatch with 32-bit HEC-RAS COM server. Do not use.
        </div>
        <h3>Settings (Tab 1)</h3>
        <ul>
          <li>Set <b>RAS Executable</b> path to your <code>Ras.exe</code></li>
          <li>Use <b>Auto-find</b> to scan common HEC-RAS install locations</li>
          <li>Settings are saved to QGIS QSettings and restored on next launch</li>
        </ul>
        <div class="warn">
          ⚠ Progress is reported live in the <b>Log</b> tab (Tab 6).
          Each plan's stdout/stderr is captured and colour-coded.
        </div>
    """

    _HELP_TAB3 = """
        <h2>Tab 4 — Result Viewer</h2>
        <div class="step">
          1. Click <b>↺ Scan for HDFs</b> (auto-runs on project load)<br>
          2. Select a <b>Plan</b> — plan HDF and postprocessing HDF shown<br>
          3. Select <b>2D Flow Area</b> and <b>Variable</b><br>
          4. Choose time step and output options<br>
          5. Click <b>▶ Load to QGIS</b>
        </div>

        <h3>HDF types</h3>
        <table>
          <tr><th>File</th><th>Contains</th><th>Used for</th></tr>
          <tr><td><code>.p##.hdf</code></td><td>Plan results: WSE, velocity, depth per cell per timestep</td>
              <td>Point layer, Cell mesh</td></tr>

        </table>

        <h3>Variables</h3>
        <table>
          <tr><th>Variable</th><th>Source HDF</th><th>Description</th></tr>
          <tr><td>Depth</td><td>Plan HDF</td><td>Water depth = WSE − terrain (computed per cell)</td></tr>
          <tr><td>WSE</td><td>Plan HDF</td><td>Absolute water surface elevation</td></tr>
          <tr><td>Velocity</td><td>Plan HDF</td><td>Face-averaged velocity magnitude</td></tr>
           <tr><td>Bed Level</td><td>Plan HDF</td><td>Cell minimum terrain elevation (static)</td></tr>
        </table>

        <h3>Output options</h3>
        <table>
          <tr><th>Output</th><th>Source</th><th>Notes</th></tr>
          <tr><td>Point layer</td><td>Plan HDF</td><td>3D point per cell centroid (Z = value). Fast.</td></tr>
          <tr><td>Cell mesh</td><td>Plan HDF</td><td>Exact HDF cell boundary polygons with value attribute.</td></tr>

        </table>


        <h3>Dry threshold</h3>
        <div class="step">
          Default 0.003 m — matches HEC-RAS Cell Volume Tolerance. Cells with
          depth below this are excluded from output when <i>Remove dry cells</i>
          is ticked.
        </div>

        <h3>RAS Mapper launcher</h3>
        <div class="step">
          Click <b>🗺 Open in RAS Mapper</b> to launch RASMapper.exe with the
          project <code>.rasmap</code> file. Use <b>🔍 Auto-find</b> to detect the
          RASMapper.exe path automatically.
        </div>

        <h3>🌍 Terrain</h3>
        <div class="step">
          Click <b>🔍 Scan for terrain</b> to read the project's terrain
          reference from the <code>.rasmap</code> file's <code>&lt;Terrains&gt;</code>
          entry, then <b>🌍 Load Terrain</b> to add it as a raster layer in QGIS.
          Terrain scanning runs automatically when a <code>.rasmap</code> file
          is auto-detected on project load.
        </div>
        <div class="warn">
          ⚠ HEC-RAS's <code>Terrain.hdf</code> is only an index/metadata file
          (it lists the GeoTIFF tiles and merge priority) — it is <b>not</b> a
          raster GDAL/QGIS can open directly. When the project references a
          <code>.hdf</code> terrain, this tool automatically looks for and
          loads the sibling <code>.vrt</code> file instead, which HEC-RAS
          creates specifically for use in external GIS software. If no
          matching <code>.vrt</code> is found, open RAS Mapper once to
          regenerate it, or add the terrain's source GeoTIFF(s) to QGIS manually.
        </div>
    """

    _HELP_TAB4 = """
        <h2>Tab 5 — Animate</h2>
        <div class="step">
          Loads all time steps for a variable into memory, then steps
          through them live on the QGIS canvas as a point layer.
        </div>
        <h3>Workflow</h3>
        <ul>
          <li>Select <b>Plan</b>, <b>2D Flow Area</b>, and <b>Variable</b></li>
          <li>Click <b>📥 Load Animation Data</b> — all time steps read from HDF</li>
          <li>Drag the <b>slider</b> to jump to any frame, or click <b>▶ Play</b></li>
          <li>Adjust <b>Frame speed</b>: 100 ms = fast, 3000 ms = slow</li>
          <li>Click <b>■ Stop</b> to pause</li>
        </ul>
        <div class="warn">
          ⚠ For large models (&gt;500 000 cells × many time steps), loading all
          frames may use several hundred MB of RAM. Consider a shorter output
          interval or sub-area before re-running.
        </div>
    """

    _HELP_TAB5 = """
        <h2>Tab 6 — Postprocess</h2>
        <div class="step">
          <b>Plan Selection</b> at the top is independent of the Result Viewer
          tab — pick the plan and 2D Flow Area to analyse here directly.
        </div>
        <div class="step">
          Extracts flow rate (m³/s) vs time from the plan result HDF and
          plots it directly in this tab — <b>no QGIS table layer is created</b>;
          results go straight to the graph below. Use <b>📄 Save flow data as
          CSV</b> if you want the raw numbers exported. Four source types:<br><br>
          <table>
            <tr><th>Source</th><th>How it works</th></tr>
            <tr><td><b>Draw cross-section on map</b></td>
                <td>Click <b>✏ Draw</b>, then click two points anywhere on the QGIS canvas
                    inside the 2D flow area. The tool finds every mesh face the line
                    crosses and computes flow using the same method HEC-RAS/RAS Mapper
                    uses internally for "Profile Line → Plot Flow":<br>
                    1. <code>Q = Σ Face Flow</code> — direct sum, if the dataset is
                       stored (most accurate, no calculation needed)<br>
                    2. <code>Q = Σ (V_face × A_face)</code> — the official method:
                       A_face is read from HEC-RAS's own precomputed Face Area
                       hydraulic property table (a rating curve of area vs.
                       water surface elevation, stored per face), evaluated at
                       that face's own Face Water Surface — not an approximation<br>
                    3. <code>Q = Σ (V_f × h_f × W_f)</code> — last-resort
                       approximation, only used if the Face Area table or Face
                       Water Surface isn't present in this HDF version. Flagged
                       clearly in the Log tab when used, since it's less accurate
                       than method 2.</td></tr>
            <tr><td><b>Reference Line</b></td>
                <td>Flow hydrograph at a named cross-section already defined in
                    RAS Mapper. Must exist before the plan was run.</td></tr>
            <tr><td><b>SA/2D Connection</b></td>
                <td>Flow through an internal structure or connection between
                    storage areas.</td></tr>
            <tr><td><b>Face Flow (all)</b></td>
                <td>Sum of absolute flow through every face in the 2D area —
                    a total flux check, not tied to any specific line.</td></tr>
          </table><br>
          For <b>Draw on map</b>: click ✏ Draw → click point 1 → click point 2 →
          the line appears in red on the canvas and stays there as a layer named
          "Cross-section (drawn)". Alternatively click <b>📍 Use existing layer</b>
          to pick the first/last vertex of a line feature from any line layer
          already in your project (e.g. a digitised channel centreline or a
          surveyed cross-section). Click <b>📊 Plot</b> to extract and graph
          the hydrograph below. For the other three types, click <b>🔍 Scan</b>
          first to populate the name list.
        </div>

        <h3>📉 Hydrograph Plot</h3>
        <div class="step">
          The plot renders automatically after each extraction using
          matplotlib (bundled with QGIS). Use <b>💾 Save plot as PNG</b> to
          export the chart, <b>📄 Save flow data as CSV</b> to export the
          numbers, or <b>🗑 Clear plot</b> to reset it. If matplotlib
          isn't available, install it via OSGeo4W Shell:
          <code>pip install matplotlib</code>
        </div>
        <div class="step">
          <b>Max timesteps to plot</b> limits how many timesteps are read,
          calculated, and plotted — useful on long simulations where the
          full timestep count would be slow. Data is evenly subsampled
          across the full simulation duration (not truncated to the start),
          so the hydrograph shape is preserved at a lower resolution.
          Default 1440 (~1-minute resolution over 24 hours).
        </div>

        <div class="warn">
          ⚠ If the status bar reports the line falls outside the mesh extent,
          this means a CRS mismatch — the Log tab (Tab 7) will show both the
          project CRS and the HDF's native CRS so you can verify and correct
          the project CRS via <b>Project ▸ Properties ▸ CRS</b> in QGIS.
        </div>
        <div class="warn">
          ⚠ Reference lines must be defined in RAS Mapper and the plan run with
          <code>Run RASMapper=-1</code> for that data to exist in the HDF.
          The drawn cross-section option has no such requirement — it works on
          any plan that has 2D mesh and Face Flow output.
        </div>
        <div class="warn">
          ⚠ Sign convention matches official HEC-RAS: positive flow passes
          through the line from <b>left to right when looking in the
          direction you drew it</b> (point 1 → point 2 = "looking downstream").
          If your hydrograph comes out negative, either reverse the order
          you clicked the two points, or simply read it as flow in the
          opposite direction — the magnitude is unaffected.
        </div>
    """

    _HELP_TAB6 = """
        <h2>Tab 7 — Log</h2>
        <div class="step">
          All plugin activity is written here in real time.
        </div>
        <ul>
          <li><span style="color:#1e8449;">■</span> <b>Green</b> — success / completion</li>
          <li><span style="color:#c0392b;">■</span> <b>Red</b> — error</li>
          <li><span style="color:#d68910;">■</span> <b>Orange</b> — warning</li>
          <li>■ Black — informational</li>
        </ul>
        <div class="step">
          HEC-RAS console output from each plan run is also captured here.
          Use <b>Clear Log</b> to reset.
        </div>
    """

    _HELP_PAGES = [_HELP_TAB0, _HELP_TAB1, _HELP_TAB2, _HELP_TAB3, _HELP_TAB4,
                   _HELP_TAB5, _HELP_TAB6]

    def _build_help_panel(self):
        panel = QWidget()
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(4, 0, 0, 0)
        tb = QTextBrowser()
        tb.setOpenExternalLinks(True)
        tb.setHtml(self._HELP_CSS + self._HELP_TAB0)
        pl.addWidget(tb)
        return tb, panel   # return browser so _update_help_panel can swap content

    def _update_help_panel(self, tab_idx):
        pages = self._HELP_PAGES
        html = pages[tab_idx] if tab_idx < len(pages) else pages[0]
        self._help_browser.setHtml(self._HELP_CSS + html)

    # ══════════════════════════════════════════════════════════════════════════
    # Project loading
    # ══════════════════════════════════════════════════════════════════════════
    def _browse_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open HEC-RAS Project", "", "HEC-RAS Project (*.prj);;All Files (*)")
        if path:
            self.txt_project_path.setText(path)
            self._load_project(path)

    def _reload_project(self):
        path = self.txt_project_path.text().strip()
        if path:
            self._load_project(path)

    def _load_project(self, path):
        if os.path.isdir(path):
            prj = find_project_file(path)
            if not prj:
                self.status_lbl.setText("No .prj file found in folder.")
                return
        elif path.lower().endswith(".prj"):
            prj = path
        else:
            self.status_lbl.setText("Select a .prj file or project folder.")
            return

        # Clean up any layers/temp files left from a previously-loaded project
        # in this same session, so switching projects never leaves stale data
        # or a stale CRS reference behind.
        if self._project_data is not None:
            self._qct_cleanup_all()

        self._project_data = parse_prj(prj)
        self._populate_browser()
        self._populate_run_table()
        self._refresh_pe_combo()
        self._scan_results()
        self._refresh_anim_plans()
        self.btn_run.setEnabled(bool(self._project_data["plans"]))
        n = len(self._project_data["plans"])
        self.lbl_project_title.setText(
            f"<b>{self._project_data['title'] or os.path.basename(prj)}</b>  "
            f"— {n} plan(s)  |  {os.path.dirname(prj)}")
        self.status_lbl.setText(f"✅ Project loaded: {prj}")
        self.append_log(f"Project loaded: {prj}", "SUCCESS")
        self._load_project_units_crs()

    # ── Units + CRS ───────────────────────────────────────────────────────────
    def _load_project_units_crs(self):
        """Read Units System and CRS from any available plan HDF."""
        import re
        d = self._project_data
        folder = d["folder"]
        base = os.path.splitext(os.path.basename(d["prj_path"]))[0]

        self._project_units = "SI"   # default
        self._project_crs_wkt = ""
        self._project_crs_name = ""
        self._project_crs_auth = ""

        h5py = _get_h5py()
        if not h5py:
            self.lbl_project_info.setText("h5py not available — unit/CRS info unavailable")
            return

        # Find first available plan HDF
        hdf_path = None
        for p in d["plans"]:
            hdf = os.path.join(folder, f"{base}.{p['short_id']}.hdf")
            if os.path.isfile(hdf):
                hdf_path = hdf
                break

        if not hdf_path:
            self.lbl_project_info.setText("No plan HDF found yet — run plans to generate results.")
            return

        try:
            with h5py.File(hdf_path, "r") as hf:
                units = hf.attrs.get("Units System", b"SI Units")
                if isinstance(units, bytes):
                    units = units.decode()
                self._project_units = "SI" if "SI" in units.upper() else "US"

                proj = hf.attrs.get("Projection", b"")
                if isinstance(proj, bytes):
                    proj = proj.decode()
                self._project_crs_wkt = proj

                # Parse CRS name from WKT
                m = re.match(r'PROJCS\["([^"]+)"', proj)
                if m:
                    self._project_crs_name = m.group(1)
                else:
                    m2 = re.match(r'GEOGCS\["([^"]+)"', proj)
                    self._project_crs_name = m2.group(1) if m2 else "Unknown"

                # Parse EPSG / AUTHORITY
                m3 = re.search(r'AUTHORITY\["([^"]+)","(\d+)"\]', proj)
                if m3:
                    self._project_crs_auth = f"{m3.group(1)}:{m3.group(2)}"

                # Get QGIS CRS for display
                from qgis.core import QgsCoordinateReferenceSystem, QgsProject
                qgs_crs = QgsCoordinateReferenceSystem()
                qgs_crs.createFromWkt(proj)
                auth_id = qgs_crs.authid() or self._project_crs_auth or "Custom WKT"

                # Sync the QGIS PROJECT's own CRS to match this model, so it
                # never lingers from a previously-loaded project/session with
                # a different CRS. This is what previously caused cross-section
                # draw/extraction to silently use the wrong coordinate system.
                if qgs_crs.isValid():
                    current_proj_crs = QgsProject.instance().crs()
                    if current_proj_crs != qgs_crs:
                        QgsProject.instance().setCrs(qgs_crs)
                        self.append_log(
                            f"QGIS project CRS updated to match model: "
                            f"{auth_id} ({self._project_crs_name})", "INFO")

                d_unit = "m" if self._project_units == "SI" else "ft"
                v_unit = "m/s" if self._project_units == "SI" else "ft/s"

                info = (
                    f"<b>Units:</b> {units}  |  "
                    f"<b>Depth/WSE:</b> {d_unit}  |  "
                    f"<b>Velocity:</b> {v_unit}<br>"
                    f"<b>CRS:</b> {self._project_crs_name}  ({auth_id})")
                self.lbl_project_info.setText(info)
                self.append_log(f"Units: {units} | CRS: {self._project_crs_name} ({auth_id})", "INFO")

        except Exception as e:
            self.lbl_project_info.setText(f"Could not read HDF: {e}")
            self._project_units = "SI"

    # ── Tree population ───────────────────────────────────────────────────────
    def _populate_browser(self):
        d = self._project_data
        self.tree_plans.clear()
        folder = d["folder"]
        base = os.path.splitext(os.path.basename(d["prj_path"]))[0]

        def _exists_icon(exists):
            return "✅" if exists else "❌"

        # Plans
        plans_root = QTreeWidgetItem(self.tree_plans, ["Plans", "", ""])
        plans_root.setExpanded(True)
        for p in d["plans"]:
            marker = " ★" if p["short_id"] == d["current_plan"] else ""
            title = (p.get("title") or p["short_id"]) + marker
            item = QTreeWidgetItem(plans_root, [title, p["short_id"],
                                                _exists_icon(p["exists"])])
            item.setData(0, Qt.UserRole, ("plan", p))
            item.setExpanded(False)

            # Plan sub-items: geometry, flow, terrain, HDF results
            geom_id = p.get("geom_file", "")
            if geom_id:
                geom_f = os.path.join(folder, f"{base}.{geom_id}")
                gi = QTreeWidgetItem(item, [f"Geom: {geom_id}", geom_id,
                                            _exists_icon(os.path.isfile(geom_f))])
                gi.setData(0, Qt.UserRole, ("geom_file", geom_f))
                # Terrain from geom file
                terrain = self._get_terrain_from_geom(geom_f)
                if terrain:
                    ti = QTreeWidgetItem(gi, [f"Terrain: {os.path.basename(terrain)}", "", "✅"])
                    ti.setData(0, Qt.UserRole, ("terrain", terrain))

            flow_id = p.get("flow_file", "")
            if flow_id:
                flow_f = os.path.join(folder, f"{base}.{flow_id}")
                QTreeWidgetItem(item, [f"Flow: {flow_id}", flow_id,
                                       _exists_icon(os.path.isfile(flow_f))])

            # Result HDF
            hdf_path = os.path.join(folder, f"{base}.{p['short_id']}.hdf")
            if os.path.isfile(hdf_path):
                sz = os.path.getsize(hdf_path) // 1024
                ri = QTreeWidgetItem(item, [f"Result HDF ({sz:,} KB)",
                                            p["short_id"] + ".hdf", "✅"])
                ri.setData(0, Qt.UserRole, ("result_hdf", hdf_path))

        # Geometry files
        geom_root = QTreeWidgetItem(self.tree_plans, ["Geometry Files", "", ""])
        for g in d["geom_files"]:
            QTreeWidgetItem(geom_root, [os.path.basename(g["file"]),
                                        g["id"], _exists_icon(g["exists"])])

        # Unsteady/Flow files
        if d["unsteady_files"]:
            ust_root = QTreeWidgetItem(self.tree_plans, ["Unsteady Flow Files", "", ""])
            for u in d["unsteady_files"]:
                QTreeWidgetItem(ust_root, [os.path.basename(u["file"]),
                                           u["id"], _exists_icon(u["exists"])])
        elif d["flow_files"]:
            flow_root = QTreeWidgetItem(self.tree_plans, ["Flow Files", "", ""])
            for f in d["flow_files"]:
                QTreeWidgetItem(flow_root, [os.path.basename(f["file"]),
                                            f["id"], _exists_icon(f["exists"])])

        self.tree_plans.expandItem(plans_root)

    def _get_terrain_from_geom(self, geom_path):
        """Read Terrain Filename from a .gXX geometry file."""
        if not os.path.isfile(geom_path):
            return None
        try:
            with open(geom_path, "r", errors="replace") as f:
                for line in f:
                    if line.strip().startswith("Terrain Filename="):
                        val = line.strip().split("=", 1)[1].strip()
                        return val if val else None
        except Exception:
            pass
        return None

    def _on_tree_selection(self):
        items = self.tree_plans.selectedItems()
        if not items:
            return
        data = items[0].data(0, Qt.UserRole)
        if not data:
            return
        kind, info = data
        if kind == "plan":
            self._show_plan_details(info)
        elif kind == "result_hdf":
            self.tabs.setCurrentIndex(3)   # jump to Result Viewer

    def _show_plan_details(self, plan):
        d = self._project_data
        folder = d["folder"]
        base = os.path.splitext(os.path.basename(d["prj_path"]))[0]
        sid = plan["short_id"]

        # Geometry info
        geom_id = plan.get("geom_file", "")
        geom_f = os.path.join(folder, f"{base}.{geom_id}") if geom_id else ""
        terrain = self._get_terrain_from_geom(geom_f) or "—"
        geom_hdf = os.path.join(folder, f"{base}.{geom_id}.hdf") if geom_id else ""

        # Result files
        hdf_path = os.path.join(folder, f"{base}.{sid}.hdf")
        pp_path = ""
        for entry in os.listdir(folder):
            pp_check = os.path.join(folder, entry, "PostProcessing.hdf")
            if os.path.isfile(pp_check) and plan.get("title", "").lower() in entry.lower():
                pp_path = pp_check
                break

        rows = [
            ("Plan ID", sid),
            ("Title", plan.get("title", "")),
            ("Flow Type", plan.get("flow_type", "")),
            ("Geometry File", f"{geom_id}  {'✅' if os.path.isfile(geom_f) else '❌'}"),
            ("Terrain", terrain),
            ("Geom HDF", f"{'✅' if os.path.isfile(geom_hdf) else '❌'} {os.path.basename(geom_hdf)}"),
            ("Flow/Unsteady File", plan.get("flow_file", "")),
            ("Simulation Date", plan.get("simulation_date", "")),
            ("Computation Interval", plan.get("computation_interval", "")),
            ("Output Interval", plan.get("output_interval", "")),
            ("Map Output Interval", plan.get("map_output_interval", "")),
            ("Run Geom Preprocessor", plan.get("run_htab", "")),
            ("Run Unsteady", plan.get("run_unsteady", "")),
            ("Run Sediment", plan.get("run_sediment", "")),
            ("Run RAS Mapper", plan.get("run_rasmap", "")),
            ("Result HDF", f"{'✅' if os.path.isfile(hdf_path) else '❌ not found'}  {os.path.basename(hdf_path)}"),
            ("PostProcessing HDF", f"{'✅' if pp_path else '❌'}  {os.path.basename(pp_path) if pp_path else ''}"),
            ("Notes", plan.get("plan_notes", "")),
        ]
        self.tbl_details.setRowCount(len(rows))
        for i, (prop, val) in enumerate(rows):
            self.tbl_details.setItem(i, 0, QTableWidgetItem(prop))
            self.tbl_details.setItem(i, 1, QTableWidgetItem(str(val)))

    # ══════════════════════════════════════════════════════════════════════════
    # Plan Editor logic
    # ══════════════════════════════════════════════════════════════════════════
    def _refresh_pe_combo(self):
        self.pe_combo.blockSignals(True)
        self.pe_combo.clear()
        if not self._project_data:
            self.pe_combo.blockSignals(False)
            return
        d = self._project_data
        for p in d["plans"]:
            t = p.get("title") or p["short_id"]
            self.pe_combo.addItem(f"{t}  [{p['short_id']}]", p)
        self.pe_combo.blockSignals(False)
        if self.pe_combo.count():
            self._on_pe_plan_selected(0)

    # ══════════════════════════════════════════════════════════════════════════
    # Plan Editor — HDF5 Output Variables checklist
    # ══════════════════════════════════════════════════════════════════════════
    # Confirmed key syntax from a real .pXX plan file: each CHECKED optional
    # variable is its own line "HDF Additional Output Variable=<exact label>"
    # (CRLF line endings). Unchecked variables simply have no line at all —
    # there is no bitmask and no explicit "off" value. The label text must
    # match the HEC-RAS GUI checkbox text exactly, including parenthetical
    # notes and the trailing "*" on "Face Point (Node) Velocities*".
    _HDF_VAR_KEY = "HDF Additional Output Variable"

    # (label, plugin relevance note or None). Grouped to mirror the GUI dialog.
    _HDF_VAR_GROUPS = [
        ("Cell Variables", [
            ("Cell Cumulative Excess Depth", None),
            ("Cell Cumulative Infiltration Depth", None),
            ("Cell Cumulative Percolation Depth", None),
            ("Cell Cumulative Precipitation Depth", None),
            ("Cell Eddy Viscosity", None),
            ("Cell Cumulative Evapotranspiration Depth", None),
            ("Cell Evapotranspiration Potential Rate", None),
            ("Cell Evapotranspiration Rate", None),
            ("Cell Excess Rate", None),
            ("Cell Flow Balance (inflows - outflows)", None),
            ("Cell Hydraulic Depth", None),
            ("Cell Infiltration Rate", None),
            ("Cell Invert Depth (WSE - Cell Min Elev)", None),
            ("Cell Percolation Rate", None),
            ("Cell Potential Infiltration Rate", None),
            ("Cell Precipitation Rate", None),
            ("Cell Saturated Wetting Front Depth", None),
            ("Cell Soil Moisture Deficit", None),
            ("Cell Unsaturated Water Content", None),
            ("Cell Unsaturated Wetting Front Depth", None),
            ("Cell Velocity",
             "Direct cell-center velocity — potentially more accurate than this "
             "plugin's own face→cell averaging for Result Viewer/Animation "
             "velocity display. Not yet used by this plugin."),
            ("Cell Volume", None),
            ("Cell Volume Error", None),
            ("Cell Water Surface Error", None),
            ("Cell Courant", None),
        ]),
        ("Face Variables", [
            ("Face Courant", None),
            ("Face Manning's n", None),
            ("Face Air Density", None),
            ("Face Dispersive Stress", None),
            ("Face Eddy Viscosity", None),
            ("Face Flow",
             "★ Enables Priority 1 (direct sum) in this plugin's Flow Hydrograph "
             "— the most accurate, no-calculation method. Recommended."),
            ("Face Period-Average Flow", None),
            ("Face Cumulative Volume", None),
            ("Face Area",
             "★ Enables Priority 2 (official Q=V×A method) in this plugin's "
             "Flow Hydrograph, together with Face Water Surface. Recommended."),
            ("Face Mixture Dynamic Viscosity", None),
            ("Face Point (Node) Velocities*",
             "⚠ NOT used by this plugin or by RAS Mapper for any mapping. "
             "Unreliable at wet-dry boundaries (e.g. a dry levee crest reports "
             "the higher of its two adjacent cells' velocities rather than a "
             "true average). This plugin always uses Face Velocity instead."),
            ("Face Shear Stress", None),
            ("Face Tangential Velocity (Both sides of each face)", None),
            ("Face Viscous Stress", None),
            ("Face Water Surface",
             "★ Enables Priority 2 (official Q=V×A method) in this plugin's "
             "Flow Hydrograph, together with Face Area. Recommended."),
            ("Face Wind Shear Stress", None),
            ("Face Wind Velocity", None),
            ("Face Yield Stress", None),
        ]),
        ("Advanced / Specialized", [
            ("Governing Equation Terms", None),
            ("Cell Spiral Intensity", None),
            ("Cell Equilibrium Spiral Intensity", None),
            ("Cell Flow Curvature", None),
            ("Cell Effective Flow Curvature", None),
            ("Cell Radius of Curvature", None),
            ("Cell Effective Radius of Curvature", None),
            ("Cell Momentum Dispersive Stresses", None),
            ("Cell Dispersion Terms", None),
            ("Cell Spiral Intensity Adaptation Length", None),
            ("Face Spiral Velocity", None),
            ("Face Surface Velocity",
             "Separate from Face Velocity (normal component) — this is a "
             "surface-layer velocity variant. Not currently used by this plugin."),
            ("Face Near-bed Velocity", None),
            ("Face Momentum Dispersion", None),
            ("Face Spiral Intensity Diffusion Coefficient", None),
        ]),
    ]

    def _build_pe_hdf_vars_tab(self):
        """Checklist mirroring HEC-RAS's Output Control Options -> HDF5 Write
        Parameters -> Additional 2D Variables dialog. Reads/writes the raw
        plan text in self.pe_raw directly, so it stays in sync with the
        normal Save/Reload/Save As workflow — no separate file I/O."""
        tab = QScrollArea()
        tab.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        note = QLabel(
            "Mirrors HEC-RAS's <i>Output Control Options → HDF5 Write Parameters</i> "
            "dialog. Checking a box adds <code>HDF Additional Output Variable=&lt;name&gt;</code> "
            "to the plan text (one line per variable); unchecking removes it. "
            "Variables marked ★ are used directly by this plugin's Flow Hydrograph "
            "or Result Viewer features.")
        note.setWordWrap(True)
        lay.addWidget(note)

        btn_row = QHBoxLayout()
        btn_detect = QPushButton("🔍 Read current selection from plan")
        btn_detect.clicked.connect(self._pe_hdf_vars_load_from_text)
        btn_apply = QPushButton("✅ Apply to plan text")
        btn_apply.clicked.connect(self._pe_hdf_vars_apply_to_text)
        btn_row.addWidget(btn_detect)
        btn_row.addWidget(btn_apply)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._pe_hdf_var_checks = {}   # label -> QCheckBox

        for group_name, items in self._HDF_VAR_GROUPS:
            gb = QGroupBox(group_name)
            gl = QVBoxLayout(gb)
            gl.setSpacing(2)
            for label, note_text in items:
                cb = QCheckBox(label)
                if note_text:
                    cb.setToolTip(note_text)
                    cb.setStyleSheet("font-weight:bold;")
                gl.addWidget(cb)
                if note_text:
                    hint = QLabel(f"&nbsp;&nbsp;&nbsp;&nbsp;💡 {note_text}")
                    hint.setWordWrap(True)
                    hint.setStyleSheet("color:#1a5276; font-size:10px;")
                    gl.addWidget(hint)
                self._pe_hdf_var_checks[label] = cb
            lay.addWidget(gb)

        lay.addStretch()
        tab.setWidget(inner)
        return tab

    def _pe_hdf_vars_load_from_text(self):
        """Scan the currently loaded plan's raw text for existing
        'HDF Additional Output Variable=' lines and tick matching checkboxes."""
        if not self._pe_current_path:
            self.append_log("Load a plan first.", "WARNING")
            return
        text = self.pe_raw.toPlainText()
        found = set()
        for line in text.splitlines():
            line = line.strip()
            if line.startswith(self._HDF_VAR_KEY + "="):
                value = line.split("=", 1)[1].strip()
                found.add(value)

        matched = 0
        for label, cb in self._pe_hdf_var_checks.items():
            is_checked = label in found
            cb.setChecked(is_checked)
            if is_checked:
                matched += 1

        unmatched = found - set(self._pe_hdf_var_checks.keys())
        msg = f"Loaded {matched} checked variable(s) from plan."
        if unmatched:
            msg += f" {len(unmatched)} unrecognized entr(y/ies) left as-is: {sorted(unmatched)}"
        self.append_log(msg, "INFO")

    def _pe_hdf_vars_apply_to_text(self):
        """Write the checklist state back into self.pe_raw's text: remove all
        existing 'HDF Additional Output Variable=' lines, then re-insert one
        line per currently-checked variable, right after 'HDF Flush=' if
        present (matching the position HEC-RAS itself uses), else at the end."""
        if not self._pe_current_path:
            self.append_log("Load a plan first.", "WARNING")
            return

        text = self.pe_raw.toPlainText()
        # Detect line ending style already used in this file
        eol = "\r\n" if "\r\n" in text else "\n"
        lines = text.splitlines()

        # Remove all existing HDF Additional Output Variable lines, remember
        # the insertion point (right after "HDF Flush=" if found, matching
        # HEC-RAS's own file layout; otherwise just after the last one removed,
        # otherwise at the end of the file).
        insert_at = None
        kept = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(self._HDF_VAR_KEY + "="):
                if insert_at is None:
                    insert_at = len(kept)   # position in the *kept* list
                continue   # drop it; will re-add below
            kept.append(line)
            if stripped.startswith("HDF Flush=") and insert_at is None:
                insert_at = len(kept)   # insert right after this line

        if insert_at is None:
            insert_at = len(kept)   # fallback: end of file

        new_var_lines = [
            f"{self._HDF_VAR_KEY}={label}"
            for label, cb in self._pe_hdf_var_checks.items() if cb.isChecked()
        ]

        result_lines = kept[:insert_at] + new_var_lines + kept[insert_at:]
        new_text = eol.join(result_lines)
        if text.endswith(("\r\n", "\n")) and not new_text.endswith(("\r\n", "\n")):
            new_text += eol

        self.pe_raw.setPlainText(new_text)
        self.append_log(
            f"Applied {len(new_var_lines)} HDF output variable(s) to plan text. "
            f"Click 💾 Save to write to disk.", "SUCCESS")

    def _on_pe_plan_selected(self, idx):
        p = self.pe_combo.itemData(idx)
        if not p:
            return
        path = p.get("file", "")
        if not os.path.isfile(path):
            self.pe_raw.setPlaceholderText(f"File not found:\n{path}")
            self.pe_raw.clear()
            self._pe_current_path = None
            for b in [self.pe_reload_btn, self.pe_save_btn, self.pe_saveas_btn]:
                b.setEnabled(False)
            return
        self._pe_current_path = path
        self.pe_path_lbl.setText(path)
        try:
            with open(path, "r", errors="replace") as f:
                self.pe_raw.setPlainText(f.read())
        except Exception as e:
            self.pe_raw.setPlainText(f"Error reading file:\n{e}")
        for b in [self.pe_reload_btn, self.pe_save_btn, self.pe_saveas_btn]:
            b.setEnabled(True)
        self._pe_hdf_vars_load_from_text()

    def _pe_reload(self):
        if not self._pe_current_path:
            return
        try:
            with open(self._pe_current_path, "r", errors="replace") as f:
                self.pe_raw.setPlainText(f.read())
            self.append_log(f"Reloaded: {self._pe_current_path}", "INFO")
            self._pe_hdf_vars_load_from_text()
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _pe_save(self):
        if not self._pe_current_path:
            return
        reply = QMessageBox.question(
            self, "Save Plan File",
            f"Overwrite:\n{self._pe_current_path}",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Yes)
        if reply != QMessageBox.Yes:
            return
        try:
            with open(self._pe_current_path, "w", encoding="utf-8") as f:
                f.write(self.pe_raw.toPlainText())
            self.append_log(f"Saved: {self._pe_current_path}", "SUCCESS")
            self._reload_project()
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def _pe_save_as(self):
        if not self._project_data or not self._pe_current_path:
            return
        d = self._project_data
        folder = d["folder"]
        base = os.path.splitext(os.path.basename(d["prj_path"]))[0]

        # Find next available plan ID
        existing_ids = {p["short_id"] for p in d["plans"]}
        for n in range(1, 100):
            new_id = f"p{n:02d}"
            if new_id not in existing_ids:
                break
        new_path = os.path.join(folder, f"{base}.{new_id}")

        # Update Plan Title and Short Identifier in the raw text
        text = self.pe_raw.toPlainText()
        import re
        text = re.sub(r"(?m)^Short Identifier=.*$", f"Short Identifier={new_id}", text)
        if not re.search(r"(?m)^Short Identifier=", text):
            text = f"Short Identifier={new_id}\n" + text

        try:
            with open(new_path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))
            return

        # Register in .prj
        self._register_plan_in_prj(d["prj_path"], new_id)
        self.append_log(f"Saved as new plan: {new_path}", "SUCCESS")
        self._reload_project()
        # Select the new plan in combo
        for i in range(self.pe_combo.count()):
            if self.pe_combo.itemData(i) and \
               self.pe_combo.itemData(i).get("short_id") == new_id:
                self.pe_combo.setCurrentIndex(i)
                break

    def _pe_new_plan(self):
        if not self._project_data:
            QMessageBox.warning(self, "No Project", "Load a project first.")
            return
        d = self._project_data
        folder = d["folder"]
        base = os.path.splitext(os.path.basename(d["prj_path"]))[0]
        existing_ids = {p["short_id"] for p in d["plans"]}
        for n in range(1, 100):
            new_id = f"p{n:02d}"
            if new_id not in existing_ids:
                break
        new_path = os.path.join(folder, f"{base}.{new_id}")

        # Blank template
        template = (f"Plan Title=New Plan {new_id}\n"
                    f"Short Identifier={new_id}\n"
                    f"Simulation Date=\n"
                    f"Geom File=\nFlow File=\n"
                    f"Run HTab=-1\nRun UNet=-1\nRun Sediment= 0\n"
                    f"Run PostProcess=-1\nRun RASMapper=-1\n")
        try:
            with open(new_path, "w", encoding="utf-8") as f:
                f.write(template)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return

        self._register_plan_in_prj(d["prj_path"], new_id)
        self.append_log(f"New plan created: {new_path}", "SUCCESS")
        self._reload_project()

    def _pe_delete_plan(self):
        idx = self.pe_combo.currentIndex()
        p = self.pe_combo.itemData(idx)
        if not p:
            return
        reply = QMessageBox.question(
            self, "Delete Plan",
            f"Delete plan file AND remove from .prj?\n\n{p.get('file', '')}",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if reply != QMessageBox.Yes:
            return
        path = p.get("file", "")
        if os.path.isfile(path):
            try:
                os.remove(path)
            except Exception as e:
                QMessageBox.critical(self, "Delete Error", str(e))
                return
        self._unregister_plan_in_prj(self._project_data["prj_path"], p["short_id"])
        self.append_log(f"Deleted plan: {path}", "SUCCESS")
        self._reload_project()

    def _register_plan_in_prj(self, prj_path, plan_id):
        try:
            with open(prj_path, "r", errors="replace") as f:
                lines = f.readlines()
            # Add after last Plan File= line
            insert_at = len(lines)
            for i, line_txt in enumerate(lines):
                if line_txt.strip().startswith("Plan File="):
                    insert_at = i + 1
            lines.insert(insert_at, f"Plan File={plan_id}\n")
            with open(prj_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
        except Exception as e:
            self.append_log(f"Could not update .prj: {e}", "ERROR")

    def _unregister_plan_in_prj(self, prj_path, plan_id):
        try:
            with open(prj_path, "r", errors="replace") as f:
                lines = f.readlines()
            lines = [ln for ln in lines
                     if not (ln.strip().startswith("Plan File=") and ln.strip().split("=", 1)[1].strip() == plan_id)]
            with open(prj_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
        except Exception as e:
            self.append_log(f"Could not update .prj: {e}", "ERROR")

    # ══════════════════════════════════════════════════════════════════════════
    # Run Manager logic
    # ══════════════════════════════════════════════════════════════════════════
    def _populate_run_table(self):
        if not self._project_data:
            return
        plans = self._project_data["plans"]
        self.tbl_run_plans.setRowCount(len(plans))
        for row, p in enumerate(plans):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked)
            self.tbl_run_plans.setItem(row, 0, chk)
            self.tbl_run_plans.setItem(row, 1, QTableWidgetItem(p.get("title", "") or p["short_id"]))
            self.tbl_run_plans.setItem(row, 2, QTableWidgetItem(p["short_id"]))
            self.tbl_run_plans.setItem(row, 3, QTableWidgetItem(p.get("flow_type", "?")))
            self.tbl_run_plans.setItem(row, 4, QTableWidgetItem("—"))

    def _select_all_plans(self):
        for r in range(self.tbl_run_plans.rowCount()):
            self.tbl_run_plans.item(r, 0).setCheckState(Qt.Checked)

    def _select_no_plans(self):
        for r in range(self.tbl_run_plans.rowCount()):
            self.tbl_run_plans.item(r, 0).setCheckState(Qt.Unchecked)

    def _start_run(self):
        if not self._project_data:
            return
        plans = self._project_data["plans"]
        selected = []
        for row in range(self.tbl_run_plans.rowCount()):
            if self.tbl_run_plans.item(row, 0).checkState() == Qt.Checked:
                selected.append(plans[row])
                self._set_plan_status(row, "Queued")
        if not selected:
            QMessageBox.warning(self, "No Plans", "Select at least one plan to run.")
            return

        parallel = self.rb_parallel.isChecked()
        use_ras_commander = self.rb_rc.isChecked()
        use_controller = self.rb_ctrl.isChecked()
        max_workers = self.spn_workers.value()
        num_cores = self.spn_cores.value()
        prj_path = self._project_data["prj_path"]
        ras_exe = self.txt_ras_exe.text().strip()
        progid = self.txt_ras_progid.text().strip()
        if use_ras_commander:
            engine_str = "RAS Commander"
        elif use_controller:
            engine_str = "RAS Controller"
        else:
            engine_str = "RAS Executable"
        mode_str = "Parallel" if parallel else "Sequential"

        self.run_log.clear()
        self.run_log.append(
            f"▶ Starting run — {len(selected)} plan(s) — "
            f"{mode_str} — {engine_str}  [{datetime.now():%Y-%m-%d %H:%M:%S}]")

        self._run_manager = MultiPlanRunManager(
            plans=selected, prj_path=prj_path, run_dir="",
            parallel=parallel, max_workers=max_workers,
            progid_override=progid, ras_exe=ras_exe,
            use_controller=use_controller,
            use_ras_commander=use_ras_commander,
            num_cores=num_cores)
        self._run_manager.log_line.connect(self._on_run_log)
        self._run_manager.plan_finished.connect(self._on_plan_finished)
        self._run_manager.all_finished.connect(self._on_all_finished)

        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.prog_bar.setVisible(True)
        self._run_manager.start()

    def _cancel_run(self):
        if self._run_manager:
            self._run_manager.cancel()
            self.run_log.append("⚠️ Cancellation requested…")

    def _on_run_log(self, plan_title, message):
        self.run_log.append(f"• [{plan_title}] [{datetime.now():%H:%M:%S}] {message}")
        c = self.run_log.textCursor()
        c.movePosition(QTextCursor.End)
        self.run_log.setTextCursor(c)
        self.append_log(f"[{plan_title}] {message}", _log_level(message))

    def _on_plan_finished(self, title, success, elapsed):
        icon = "✅" if success else "❌"
        self.run_log.append(
            f"{icon} [{title}] [{datetime.now():%H:%M:%S}] "
            f"{'Completed' if success else 'Failed'} in {elapsed}s")
        for row in range(self.tbl_run_plans.rowCount()):
            if self.tbl_run_plans.item(row, 1).text() == title or \
               self.tbl_run_plans.item(row, 2).text() == title:
                self._set_plan_status(row, "✅ Done" if success else "❌ Failed")
                break

    def _on_all_finished(self, results):
        # results = list of (title, success, elapsed) tuples
        ok = sum(1 for r in results if (r[1] if isinstance(r, tuple) else r.get("success")))
        fail = len(results) - ok
        icon = "✅" if fail == 0 else "⚠️"
        msg = f"{icon} All plans complete — {ok} succeeded, {fail} failed."
        self.run_log.append(msg)
        self.append_log(msg, "SUCCESS" if fail == 0 else "WARNING")
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.prog_bar.setVisible(False)
        self._reload_project()   # refresh results in viewer

    def _set_plan_status(self, row, text):
        self.tbl_run_plans.setItem(row, 4, QTableWidgetItem(text))

    # ══════════════════════════════════════════════════════════════════════════
    # Result Viewer logic
    # ══════════════════════════════════════════════════════════════════════════
    def _scan_results(self):
        """Populate Result Viewer plan combo with available plan HDFs.
        Also detects postprocessing.hdf in the project folder."""
        if not self._project_data:
            return
        d = self._project_data
        folder = d["folder"]
        base = os.path.splitext(os.path.basename(d["prj_path"]))[0]

        self.rv_plan_combo.blockSignals(True)
        self.rv_plan_combo.clear()
        self.pp_plan_combo.blockSignals(True)
        self.pp_plan_combo.clear()
        found = 0
        for p in d["plans"]:
            sid = p["short_id"]
            plan_title = p.get("title", "") or sid
            hdf = os.path.join(folder, f"{base}.{sid}.hdf")
            if os.path.isfile(hdf):
                sz = os.path.getsize(hdf) / (1024 * 1024)
                lbl = f"{plan_title}  [{sid}]  ({sz:.0f} MB)"
                plan_data = {
                    "plan_title": plan_title,
                    "short_id": sid,
                    "hdf_path": hdf,
                }
                self.rv_plan_combo.addItem(lbl, plan_data)
                self.pp_plan_combo.addItem(lbl, dict(plan_data))
                found += 1
        self.rv_plan_combo.blockSignals(False)
        self.pp_plan_combo.blockSignals(False)
        self.append_log(f"Found {found} result HDF file(s).", "INFO")
        if found:
            self._rv_on_plan_changed(0)
            self._pp_on_plan_changed(0)
        # Auto-find .rasmap
        self._rv_find_rasmap()

    # ══════════════════════════════════════════════════════════════════════════
    # Settings
    # ══════════════════════════════════════════════════════════════════════════
    def _browse_ras_exe(self):
        p, _ = QFileDialog.getOpenFileName(self, "Select Ras.exe", "",
                                           "Executable (*.exe);;All Files (*)")
        if p:
            self.txt_ras_exe.setText(p)

    def _test_controller(self):
        try:
            import win32com.client
            progid = self.txt_ras_progid.text().strip()
            candidates = ([progid] if progid else
                          [f"RAS{v}.HECRASController"
                           for v in ["701", "700", "660", "650", "631", "630", "541", "535", "500"]])
            for pid in candidates:
                try:
                    win32com.client.Dispatch(pid)
                    QMessageBox.information(self, "Controller OK",
                                            f"Connected: {pid}")
                    return
                except Exception:
                    pass
            QMessageBox.warning(self, "Controller Failed",
                                "No RAS Controller COM object found.\n"
                                "Check HEC-RAS is installed and registered.")
        except ImportError:
            QMessageBox.warning(self, "win32com Missing",
                                "pywin32 not installed. Use RAS Executable engine instead.")

    def _save_settings(self):
        s = QSettings()
        s.beginGroup("QCTHECRASManager")
        s.setValue("ras_exe", self.txt_ras_exe.text())
        s.setValue("ras_progid", self.txt_ras_progid.text())
        s.endGroup()
        self.append_log("Settings saved.", "SUCCESS")

    def _restore_settings(self):
        s = QSettings()
        s.beginGroup("QCTHECRASManager")
        self.txt_ras_exe.setText(s.value("ras_exe", ""))
        self.txt_ras_progid.setText(s.value("ras_progid", ""))
        s.endGroup()

    # ══════════════════════════════════════════════════════════════════════════
    # Log
    # ══════════════════════════════════════════════════════════════════════════
    def append_log(self, message, level="INFO"):
        icons = {"SUCCESS": "✅", "WARNING": "⚠️", "ERROR": "❌", "INFO": "ℹ️"}
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{ts}] {icons.get(level, '•')} {message}")
        c = self.log_text.textCursor()
        c.movePosition(QTextCursor.End)
        self.log_text.setTextCursor(c)

    def _save_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Log", "", "Text files (*.txt);;All Files (*)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self.log_text.toPlainText())
                self.append_log(f"Log saved: {path}", "SUCCESS")
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _apply_point_style(self, layer, variable="", v_min=None, v_max=None):
        """
        Rule-based renderer with expression filters — evaluates live on every render
        so animation frames always show the correct colour after changeAttributeValues.
          Point   -> square marker sized to cell footprint
          Polygon -> solid fill, no outline
          depth    -> Blues  0-5 m in 0.1 m steps + >5 overflow
          velocity -> RdYlBu 0-10 m/s in 1 m/s steps + >10 overflow
          wse      -> Spectral data range (20 classes)
          bed_level-> BrBG   data range (20 classes)
        """
        try:
            from qgis.core import (
                QgsRuleBasedRenderer,
                QgsMarkerSymbol, QgsSimpleMarkerSymbolLayer,
                QgsFillSymbol, QgsSimpleFillSymbolLayer,
                QgsWkbTypes, QgsStyle)
            from qgis.PyQt.QtGui import QColor
            try:
                from qgis.core import QgsUnitTypes
                RMU = QgsUnitTypes.RenderMapUnits
            except (ImportError, AttributeError):
                try:
                    from qgis.core import Qgis
                    RMU = Qgis.RenderUnit.MapUnits
                except (ImportError, AttributeError):
                    RMU = 2   # RenderMapUnits integer fallback

            try:
                is_polygon = layer.geometryType() == QgsWkbTypes.PolygonGeometry
            except AttributeError:
                is_polygon = int(layer.geometryType()) == 2

            # Find the value field name
            SKIP = {"fid", "id", "cell_id", "objectid", "ogc_fid", "gid"}
            field = None
            for f in layer.fields():
                if f.name().lower() in SKIP:
                    continue
                if f.typeName().lower() in {"double", "real", "float", "numeric"}:
                    field = f.name()
                    break
            if not field:
                for f in layer.fields():
                    if f.name().lower() in SKIP:
                        continue
                    if f.typeName().lower() in {"integer", "int", "int64"}:
                        field = f.name()
                        break
            if not field:
                self.append_log("Style: no numeric field found", "WARNING")
                return

            ext = layer.extent()
            n = max(1, layer.featureCount())
            cs = max(1.0, ((ext.width() * ext.height() / n) ** 0.5))
            vl = variable.lower()

            def _get_ramp(name):
                r = QgsStyle.defaultStyle().colorRamp(name)
                if not r:
                    for fb in ("Blues", "Reds", "Spectral", "Viridis"):
                        r = QgsStyle.defaultStyle().colorRamp(fb)
                        if r:
                            break
                return r

            def _make_sym(col):
                if is_polygon:
                    sym = QgsFillSymbol()
                    sym.deleteSymbolLayer(0)
                    fl = QgsSimpleFillSymbolLayer()
                    fl.setColor(col)
                    fl.setStrokeStyle(0)
                    sym.appendSymbolLayer(fl)
                else:
                    sym = QgsMarkerSymbol()
                    sym.deleteSymbolLayer(0)
                    sq = QgsSimpleMarkerSymbolLayer()
                    sq.setShape(QgsSimpleMarkerSymbolLayer.Shape.Square)
                    sq.setSize(cs)
                    sq.setSizeUnit(RMU)
                    sq.setStrokeStyle(0)
                    sq.setColor(col)
                    sym.appendSymbolLayer(sq)
                return sym

            # ── Build explicit break list: (lo, hi, colour, label) ────────────
            classes = []

            # Determine variable type — field name is authoritative since it's set
            # explicitly by the caller (depth/wse/velocity/bed_level/value).
            # Only fall back to string matching on `variable` when field is generic.
            if field == "velocity":
                var_type = "velocity"
            elif field == "wse":
                var_type = "wse"
            elif field == "bed_level":
                var_type = "bed"
            elif field == "depth":
                var_type = "depth"
            else:
                # Unknown/generic field — fall back to keyword matching on variable string
                if "veloc" in vl:
                    var_type = "velocity"
                elif "bed" in vl or "terrain" in vl or "elev" in vl:
                    var_type = "bed"
                elif "wse" in vl or "water surf" in vl:
                    var_type = "wse"
                else:
                    var_type = "depth"

            if var_type == "velocity":
                # RdYlBu reversed: slow=blue, fast=red
                # 0–5 m/s in 0.5 steps  |  5–10 m/s in 1.0 steps  |  > 10 m/s
                ramp = _get_ramp("RdYlBu")
                vel_breaks = (
                    [(i * 0.5, (i + 1) * 0.5) for i in range(10)] + [(5.0 + i, 6.0 + i) for i in range(5)]
                )
                N_vel = len(vel_breaks)
                for idx_v, (lo, hi) in enumerate(vel_breaks):
                    t = 1.0 - idx_v / (N_vel - 1)
                    col = ramp.color(t) if ramp else QColor(
                        int(255 * idx_v / N_vel), 0, int(255 * (1 - idx_v / N_vel)))
                    classes.append((lo, hi, col, f"{lo:.1f}-{hi:.1f} m/s"))
                classes.append((10.0, 9999.0, QColor(103, 0, 31), "> 10.0 m/s"))

            elif var_type == "wse":
                ramp = _get_ramp("Spectral")
                if v_min is None or v_max is None:
                    _fidx = layer.fields().indexOf(field)
                    _vals = [f.attributes()[_fidx] for f in layer.getFeatures()
                             if f.attributes()[_fidx] is not None]
                    v_min = min(_vals) if _vals else 0.0
                    v_max = max(_vals) if _vals else 10.0
                N = 20
                step = (v_max - v_min) / N
                for i in range(N):
                    lo, hi = v_min + i * step, v_min + (i + 1) * step
                    t = 1.0 - i / (N - 1)
                    col = ramp.color(t) if ramp else QColor(0, 0, int(255 * i / N))
                    classes.append((lo, hi, col, f"{lo:.2f}-{hi:.2f} m"))

            elif var_type == "bed":
                ramp = _get_ramp("BrBG")
                if v_min is None or v_max is None:
                    _fidx = layer.fields().indexOf(field)
                    _vals = [f.attributes()[_fidx] for f in layer.getFeatures()
                             if f.attributes()[_fidx] is not None]
                    v_min = min(_vals) if _vals else 0.0
                    v_max = max(_vals) if _vals else 100.0
                N = 20
                step = (v_max - v_min) / N
                for i in range(N):
                    lo, hi = v_min + i * step, v_min + (i + 1) * step
                    t = i / (N - 1)
                    col = ramp.color(t) if ramp else QColor(
                        int(150 * i / N), int(100 * i / N), 0)
                    classes.append((lo, hi, col, f"{lo:.1f}-{hi:.1f} m"))

            else:
                # DEPTH: Blues — 0-1 m (0.2 step) | 1-3 m (0.5 step) | >3 m
                ramp = _get_ramp("Blues")
                _fine = [(round(i * 0.2, 1), round((i + 1) * 0.2, 1)) for i in range(5)]
                _coarse = [(1.0 + i * 0.5, 1.0 + (i + 1) * 0.5) for i in range(4)]
                depth_breaks = _fine + _coarse
                N_d = len(depth_breaks)
                for idx_d, (lo, hi) in enumerate(depth_breaks):
                    t = idx_d / (N_d - 1)
                    col = ramp.color(t) if ramp else QColor(
                        int(255 * (1 - t)), int(255 * (1 - t)), 255)
                    classes.append((lo, hi, col, f"{lo:.1f}-{hi:.1f} m"))
                classes.append((3.0, 9999.0, QColor(8, 48, 107), "> 3.0 m"))

            if not classes:
                return

            # Build QgsRuleBasedRenderer with expression filters evaluated live each render
            root = QgsRuleBasedRenderer.Rule(None)
            for i, (lo, hi, col, lbl) in enumerate(classes):
                sym = _make_sym(col)
                # Use exact decimal strings to avoid scientific notation in expressions
                lo_s = f"{lo:.10g}"
                hi_s = f"{hi:.10g}"
                if i < len(classes) - 1:
                    expr = f'"{field}" >= {lo_s} AND "{field}" < {hi_s}'
                else:
                    expr = f'"{field}" >= {lo_s}'
                rule = QgsRuleBasedRenderer.Rule(sym, label=lbl, filterExp=expr)
                root.appendChild(rule)
            # "else" rule for NULL / dry cells — transparent symbol
            null_sym = _make_sym(QColor(0, 0, 0, 0))
            null_sym.setOpacity(0)
            null_rule = QgsRuleBasedRenderer.Rule(null_sym, label="dry/null",
                                                  filterExp="ELSE")
            root.appendChild(null_rule)

            renderer = QgsRuleBasedRenderer(root)
            layer.setRenderer(renderer)
            layer.triggerRepaint()
            layer.emitStyleChanged()
            if self.iface:
                try:
                    self.iface.layerTreeView().refreshLayerSymbology(layer.id())
                except Exception:
                    pass

        except Exception as e:
            import traceback
            self.append_log(f"Style error: {e}\n{traceback.format_exc()[-200:]}", "WARNING")

    def closeEvent(self, event):
        self._anim_timer.stop()
        self._qct_cleanup_all()
        super().closeEvent(event)

    def _qct_track_layer(self, layer):
        """Register a QGIS layer this plugin added, so it can be removed on close."""
        if layer is not None:
            try:
                self._qct_temp_layer_ids.append(layer.id())
            except Exception:
                pass

    def _qct_track_temp_path(self, path):
        """Register a temp file/folder this plugin created, for cleanup on close."""
        if path:
            self._qct_temp_paths.append(path)

    def _qct_cleanup_all(self):
        """Remove all QGIS layers and temp files created by this plugin session."""
        from qgis.core import QgsProject
        proj = QgsProject.instance()

        # Remove tracked map layers
        for lyr_id in list(self._qct_temp_layer_ids):
            try:
                if proj.mapLayer(lyr_id) is not None:
                    proj.removeMapLayer(lyr_id)
            except Exception:
                pass
        self._qct_temp_layer_ids.clear()

        # Remove animation GeoPackage if still set and not already tracked
        gpkg = getattr(self, "_anim_gpkg", None)
        if gpkg:
            self._qct_track_temp_path(gpkg)

        # Delete tracked temp files/folders from disk
        removed, failed = 0, 0
        for path in list(set(self._qct_temp_paths)):
            try:
                if os.path.isfile(path):
                    os.remove(path)
                    removed += 1
                elif os.path.isdir(path):
                    import shutil as _shutil
                    _shutil.rmtree(path, ignore_errors=True)
                    removed += 1
            except Exception:
                failed += 1
        self._qct_temp_paths.clear()

        try:
            self.append_log(
                "Cleanup on close: removed"
                f" {removed} temp file(s)/layer(s)" + (f", {failed} failed" if failed else ""), "INFO")
        except Exception:
            pass   # log widget may already be gone during shutdown

        # Reset all internal project/CRS state so a fresh load never reuses
        # stale values from this session (this previously caused the project
        # CRS check to report wrong/stale info after loading a new project).
        self._project_data = None
        self._project_units = "SI"
        self._project_crs_wkt = ""
        self._project_crs_name = ""
        self._project_crs_auth = ""
        self._rv_terrain_info = None
        self._fh_drawn_line = None
        self._anim_layer = None
        self._anim_data = None
        self._anim_gpkg = None
        self._pp_last_stamps = None
        self._pp_last_flow_arr = None
        self._pp_last_col_names = None

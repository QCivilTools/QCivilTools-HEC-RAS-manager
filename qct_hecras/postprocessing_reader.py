# -*- coding: utf-8 -*-
"""
postprocessing_reader.py
Reads HEC-RAS plan PostProcessing.hdf result files.

Structure (v1.3):
  Results/Unsteady/Output/Output Blocks/Base Output/Unsteady Time Series/
    2D Flow Areas/<area>/
      Water Surface     (nTime, nCells)  float32
      Face Velocity     (nTime, nFaces)  float32
    Reference Lines/
      Flow / Velocity / Water Surface   (nTime, nRefLines)

Cell geometry (coordinates) are in the .g##.hdf companion file
in the main project folder.
"""
import os
import re
import sys

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

# ── h5py probe (same as ras_hdf_reader) ──────────────────────────────────────


def _get_h5py():
    try:
        import h5py
        return h5py
    except (ImportError, ValueError):
        pass
    vendor_dir = os.path.join(_PLUGIN_DIR, "vendor")
    h5py_dir = os.path.join(vendor_dir, "h5py")
    if os.path.isdir(h5py_dir):
        try:
            os.add_dll_directory(h5py_dir)
        except (AttributeError, OSError):
            pass
        os.environ["PATH"] = h5py_dir + os.pathsep + os.environ.get("PATH", "")
        if vendor_dir not in sys.path:
            sys.path.insert(0, vendor_dir)
        for key in list(sys.modules.keys()):
            if key == "h5py" or key.startswith("h5py."):
                del sys.modules[key]
        try:
            import h5py
            return h5py
        except (ImportError, ValueError):
            pass
    return None


_BASE_TS = ("Results/Unsteady/Output/Output Blocks/Base Output/"
            "Unsteady Time Series")


def scan_postprocessing(pp_hdf_path):
    """
    Scan a PostProcessing.hdf file.
    Returns dict:
      {
        'areas': { area_name: { 'variables': ['Water Surface', ...],
                                'n_cells': int, 'n_faces': int,
                                'n_timesteps': int } },
        'ref_lines': ['Flow', 'Velocity', ...],
        'n_timesteps': int,
        'error': str or None
      }
    """
    h5py = _get_h5py()
    if not h5py:
        return {"error": "h5py not available", "areas": {}}
    if not os.path.isfile(pp_hdf_path):
        return {"error": f"Not found: {pp_hdf_path}", "areas": {}}
    try:
        result = {"areas": {}, "ref_lines": [], "n_timesteps": 0, "error": None}
        with h5py.File(pp_hdf_path, "r") as hf:
            areas_grp = hf.get(f"{_BASE_TS}/2D Flow Areas")
            if areas_grp:
                for area_name in areas_grp.keys():
                    ag = areas_grp[area_name]
                    variables = []
                    n_cells = 0
                    n_faces = 0
                    n_timesteps = 0
                    for var_name in ag.keys():
                        ds = ag[var_name]
                        if not isinstance(ds, h5py.Dataset):
                            continue
                        if ds.ndim == 2:
                            n_timesteps = max(n_timesteps, ds.shape[0])
                            variables.append(var_name)
                            if "Face" in var_name or "face" in var_name:
                                n_faces = ds.shape[1]
                            else:
                                n_cells = max(n_cells, ds.shape[1])
                    result["areas"][area_name] = {
                        "variables": variables,
                        "n_cells": n_cells,
                        "n_faces": n_faces,
                        "n_timesteps": n_timesteps,
                    }
                    result["n_timesteps"] = max(result["n_timesteps"], n_timesteps)

            ref_grp = hf.get(f"{_BASE_TS}/Reference Lines")
            if ref_grp:
                result["ref_lines"] = [k for k in ref_grp.keys()
                                       if isinstance(ref_grp[k], h5py.Dataset)]
        return result
    except Exception as e:
        return {"error": str(e), "areas": {}}


def read_pp_variable(pp_hdf_path, area_name, variable, time_index=None):
    """
    Read a variable from PostProcessing.hdf.
    time_index=None → all timesteps as (nTime, nCells)
    time_index=int  → single timestep as (nCells,)
    """
    h5py = _get_h5py()
    if not h5py:
        return None
    try:
        import numpy as np
        dset_path = f"{_BASE_TS}/2D Flow Areas/{area_name}/{variable}"
        with h5py.File(pp_hdf_path, "r") as hf:
            ds = hf.get(dset_path)
            if ds is None:
                return None
            if time_index is None:
                arr = np.array(ds, dtype=np.float32)
                # Ensure (nTime, nCells) — nTime << nCells
                if arr.ndim == 2 and arr.shape[0] > arr.shape[1]:
                    arr = arr.T
                return arr
            else:
                row = ds[time_index] if ds.shape[0] > 1 else ds[0]
                return np.array(row, dtype=np.float32)
    except Exception:
        return None


def read_pp_timestamps(pp_hdf_path):
    """Read time stamp strings from PostProcessing.hdf."""
    h5py = _get_h5py()
    if not h5py:
        return []
    ts_path = f"{_BASE_TS}/Time Date Stamp"
    try:
        with h5py.File(pp_hdf_path, "r") as hf:
            ds = hf.get(ts_path)
            if ds is None:
                # Try to count timesteps from WSE
                areas = hf.get(f"{_BASE_TS}/2D Flow Areas")
                if areas:
                    for area in areas.values():
                        for var in area.values():
                            if hasattr(var, 'shape') and var.ndim == 2:
                                return [f"Step {i}" for i in range(var.shape[0])]
                return []
            return [v.decode("utf-8", "replace").strip()
                    if isinstance(v, bytes) else str(v).strip()
                    for v in ds]
    except Exception:
        return []


def find_geom_hdf(pp_hdf_path):
    """
    Find the companion .g##.hdf file that contains cell coordinates.
    PostProcessing.hdf is in: project/PlanName/PostProcessing.hdf
    The .g##.hdf is in:       project/Project.g##.hdf
    """
    plan_folder = os.path.dirname(pp_hdf_path)
    project_folder = os.path.dirname(plan_folder)
    # Search for .g##.hdf files in the project folder
    candidates = []
    for fname in os.listdir(project_folder):
        if re.match(r".+\.g\d{2}\.hdf$", fname, re.I):
            candidates.append(os.path.join(project_folder, fname))
    # Prefer the most recently modified one
    if candidates:
        return max(candidates, key=os.path.getmtime)
    return None


def get_geom_area_names(geom_hdf_path):
    """Return all 2D area names available in a geometry HDF."""
    h5py = _get_h5py()
    if not h5py:
        return []
    try:
        with h5py.File(geom_hdf_path, "r") as hf:
            g = hf.get("Geometry/2D Flow Areas")
            if not g:
                return []
            # Filter to actual mesh groups (have Cells Center Coordinate)
            return [k for k in g.keys() if isinstance(g[k], h5py.Group) and "Cells Center Coordinate" in g[k]]
    except Exception:
        return []


def read_cell_centroids_from_geom(geom_hdf_path, area_name):
    """
    Read cell centroids from the .g##.hdf geometry file.
    If area_name not found exactly, tries case-insensitive match and
    falls back to the first available area (handles name mismatches between
    PostProcessing.hdf and geometry HDF).
    """
    h5py = _get_h5py()
    if not h5py:
        return None, None
    try:
        import numpy as np
        with h5py.File(geom_hdf_path, "r") as hf:
            g = hf.get("Geometry/2D Flow Areas")
            if not g:
                return None, None

            # Collect all areas that have cell coordinates
            available = [k for k in g.keys() if isinstance(g[k], h5py.Group) and "Cells Center Coordinate" in g[k]]
            if not available:
                return None, None

            # Try exact match first
            target = area_name if area_name in available else None

            # Case-insensitive match
            if target is None:
                area_lower = area_name.lower()
                for a in available:
                    if a.lower() == area_lower:
                        target = a
                        break

            # Partial match (e.g. "Site_Extent" vs "Study_area" — no match, use first)
            if target is None and len(available) == 1:
                target = available[0]   # only one area — use it regardless of name

            if target is None:
                # Multiple areas, no name match — use first and warn via return
                target = available[0]

            arr = np.array(g[target]["Cells Center Coordinate"])
            return arr[:, 0], arr[:, 1]
    except Exception:
        return None, None


def read_projection_from_geom(geom_hdf_path):
    """
    Read CRS/projection from a HEC-RAS HDF file.
    Uses RC HdfBase.get_projection which checks:
      1. HDF file attributes (Projection attr on root)
      2. .rasmap file → RASProjectionFilename .prj file
      3. Geometry/2D Flow Areas/<area>/Projection dataset
    Returns EPSG string (e.g. "EPSG:2193") or WKT string, or None.
    """
    try:
        from ras_commander.hdf.HdfBase import HdfBase
        from pathlib import Path
        result = HdfBase.get_projection(Path(geom_hdf_path))
        if result:
            return result
    except Exception:
        pass

    # Fallback: direct HDF read
    h5py = _get_h5py()
    if not h5py:
        return None
    try:
        import numpy as np
        with h5py.File(geom_hdf_path, "r") as hf:
            # Root attribute
            proj = hf.attrs.get("Projection")
            if proj is not None:
                return proj.decode("utf-8") if isinstance(proj, bytes) else str(proj)
            # Dataset paths
            for pth in ["Projection", "CoordinateSystem"]:
                ds = hf.get(pth)
                if ds is not None:
                    v = ds[()]
                    if isinstance(v, bytes):
                        return v.decode("utf-8", "replace")
                    if isinstance(v, np.ndarray):
                        return v.tobytes().decode("utf-8", "replace").rstrip("\x00")
                    return str(v)
            # 2D Flow Areas sub-group
            areas = hf.get("Geometry/2D Flow Areas")
            if areas:
                for aname in areas.keys():
                    if isinstance(areas[aname], h5py.Group):
                        ds = areas[aname].get("Projection")
                        if ds is not None:
                            v = ds[()]
                            if isinstance(v, bytes):
                                return v.decode("utf-8", "replace")
    except Exception:
        pass
    return None


def read_projection_from_pp(pp_hdf_path):
    """
    Read CRS from PostProcessing.hdf itself (may have Projection attribute),
    then fall back to the companion geometry HDF.
    """
    # Try PostProcessing.hdf directly first
    h5py = _get_h5py()
    if h5py:
        try:
            with h5py.File(pp_hdf_path, "r") as hf:
                proj = hf.attrs.get("Projection")
                if proj:
                    return proj.decode("utf-8") if isinstance(proj, bytes) else str(proj)
        except Exception:
            pass
    # Try RC on the PP HDF
    try:
        from ras_commander.hdf.HdfBase import HdfBase
        from pathlib import Path
        result = HdfBase.get_projection(Path(pp_hdf_path))
        if result:
            return result
    except Exception:
        pass
    # Fall back to geom HDF
    geom = find_geom_hdf(pp_hdf_path)
    if geom:
        return read_projection_from_geom(geom)
    return None


def find_all_plan_hdfs(prj_path):
    """
    Given a .prj file, find all PostProcessing.hdf files in plan subfolders.
    Returns list of { plan_name, pp_hdf_path, geom_hdf_path, scan_result }
    """
    prj_folder = os.path.dirname(prj_path)
    results = []
    # Each subfolder that contains a PostProcessing.hdf is a plan folder
    for entry in sorted(os.listdir(prj_folder)):
        full = os.path.join(prj_folder, entry)
        if not os.path.isdir(full):
            continue
        pp = os.path.join(full, "PostProcessing.hdf")
        if not os.path.isfile(pp):
            continue
        geom_hdf = find_geom_hdf(pp)
        scan = scan_postprocessing(pp)
        results.append({
            "plan_name": entry,
            "pp_hdf_path": pp,
            "geom_hdf": geom_hdf,
            "scan": scan,
        })
    return results


def read_cell_min_elevation(geom_hdf_path, area_name):
    """
    Read cell minimum terrain elevation from .g##.hdf.
    Uses same area-name fallback logic as read_cell_centroids_from_geom.
    """
    h5py = _get_h5py()
    if not h5py:
        return None
    try:
        import numpy as np
        with h5py.File(geom_hdf_path, "r") as hf:
            g = hf.get("Geometry/2D Flow Areas")
            if not g:
                return None
            available = [k for k in g.keys() if isinstance(g[k], h5py.Group)]
            target = area_name if area_name in available else None
            if target is None and available:
                # Case-insensitive
                for a in available:
                    if a.lower() == area_name.lower():
                        target = a
                        break
            if target is None and len(available) >= 1:
                target = available[0]
            if target is None:
                return None
            ds = g[target].get("Cells Minimum Elevation")
            if ds is None:
                return None
            return np.array(ds, dtype=np.float32)
    except Exception:
        return None


def compute_depth_from_wse(wse_arr, terrain_arr):
    """
    Compute water depth from WSE and terrain elevation.
    depth = max(0, WSE - terrain)
    Dry cells (WSE == terrain) get depth = 0.
    """
    import numpy as np
    depth = np.maximum(0.0, wse_arr.astype(np.float32) - terrain_arr.astype(np.float32))
    return depth


def read_pp_depth(pp_hdf_path, geom_hdf_path, area_name, time_index=None):
    """
    Compute depth (WSE - terrain) from PostProcessing.hdf + geometry HDF.
    time_index=None  → max depth across all timesteps (nCells,)
    time_index=int   → depth at that timestep (nCells,)
    Returns (depth_arr, terrain_arr) or (None, None) on error.
    """
    import numpy as np
    terrain = read_cell_min_elevation(geom_hdf_path, area_name)
    if terrain is None:
        return None, None

    if time_index is None:
        # All timesteps → compute max depth
        wse_all = read_pp_variable(pp_hdf_path, area_name, "Water Surface")
        if wse_all is None:
            return None, None
        # depth at each timestep, take max
        depth_all = np.maximum(0.0, wse_all - terrain[np.newaxis, :])
        return depth_all.max(axis=0), terrain
    else:
        wse = read_pp_variable(pp_hdf_path, area_name, "Water Surface", time_index)
        if wse is None:
            return None, None
        return compute_depth_from_wse(wse, terrain), terrain


def read_pp_velocity(pp_hdf_path, area_name, time_index=None):
    """
    Read face velocity and compute cell-averaged velocity magnitude.
    Face Velocity shape: (nTime, nFaces) — one value per face.
    Cell velocity = average of adjacent face velocities.
    For simplicity, returns the raw face velocity array.
    """
    import numpy as np
    if time_index is None:
        data = read_pp_variable(pp_hdf_path, area_name, "Face Velocity")
        if data is None:
            return None
        return np.abs(data).max(axis=0)   # max velocity magnitude across time, per face
    else:
        data = read_pp_variable(pp_hdf_path, area_name, "Face Velocity", time_index)
        return np.abs(data) if data is not None else None

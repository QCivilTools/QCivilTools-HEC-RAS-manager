# -*- coding: utf-8 -*-
"""
ras_hdf_reader.py
Reads HEC-RAS 2D plan result HDF files via RAS Commander (bundled).
h5py is a dependency of RAS Commander — install once via OSGeo4W Shell:
  python3 -m pip install h5py   (then xcopy to QGIS site-packages if needed)
"""
import os
import re
import sys

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Ensure bundled ras_commander package is importable ────────────────────────
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

# ── Try h5py — must be installed in QGIS Python site-packages ─────────────────


def _ensure_h5py():
    """
    Load h5py. Tries in order:
    1. Already importable (fast path)
    2. Bundled vendor/h5py (h5py 3.10.0, numpy-1.x compatible, self-contained DLLs)
    3. System QGIS site-packages
    """
    # Fast path
    try:
        import h5py
        return True
    except (ImportError, ValueError):
        # ValueError = numpy ABI mismatch in already-installed h5py
        pass

    vendor_dir = os.path.join(_PLUGIN_DIR, "vendor")
    h5py_dir = os.path.join(vendor_dir, "h5py")

    if os.path.isdir(h5py_dir):
        # Register DLL directory so Windows finds hdf5.dll before any other
        try:
            os.add_dll_directory(h5py_dir)
        except (AttributeError, OSError):
            pass
        try:
            os.add_dll_directory(vendor_dir)
        except (AttributeError, OSError):
            pass
        # Prepend to PATH as fallback
        os.environ["PATH"] = h5py_dir + os.pathsep + os.environ.get("PATH", "")

        # Remove any broken h5py from sys.modules before retrying
        for key in list(sys.modules.keys()):
            if key == "h5py" or key.startswith("h5py."):
                del sys.modules[key]

        if vendor_dir not in sys.path:
            sys.path.insert(0, vendor_dir)
        try:
            import h5py  # noqa: F401,F811 — presence test; actual use via HAS_H5PY
            return True
        except (ImportError, ValueError):
            try:
                sys.path.remove(vendor_dir)
            except ValueError:
                pass

    return False


HAS_H5PY = _ensure_h5py()
if HAS_H5PY:
    import h5py
    import numpy as np
else:
    try:
        import numpy as np
    except BaseException:
        np = None

# ── Activate bundled RAS Commander ────────────────────────────────────────────


def _ensure_ras_commander():
    if not HAS_H5PY:
        return False
    import importlib.util
    for pkg in ('geopandas', 'xarray', 'shapely', 'ras_commander'):
        if importlib.util.find_spec(pkg) is None:
            return False
    return True


HAS_RAS_COMMANDER = _ensure_ras_commander()

# ── Public API ─────────────────────────────────────────────────────────────────


def find_hdf_files(prj_path):
    results = []
    if not os.path.isfile(prj_path):
        return results
    folder = os.path.dirname(prj_path)
    basename = os.path.splitext(os.path.basename(prj_path))[0]
    try:
        with open(prj_path, "r", errors="replace") as f:
            plan_ids = [m.group(1) for ln in f
                        for m in [re.match(r"Plan File\s*=\s*(\S+)", ln.strip(), re.I)] if m]
    except BaseException:
        plan_ids = []
    for pid in plan_ids:
        hdf_path = os.path.join(folder, f"{basename}.{pid}.hdf")
        plan_file = os.path.join(folder, f"{basename}.{pid}")
        title = pid
        if os.path.isfile(plan_file):
            try:
                with open(plan_file, "r", errors="replace") as f:
                    for ln in f:
                        if ln.strip().startswith("Plan Title="):
                            title = ln.strip().split("=", 1)[1].strip()
                            break
            except BaseException:
                pass
        results.append({"plan_id": pid, "plan_title": title,
                        "hdf_path": hdf_path, "exists": os.path.isfile(hdf_path)})
    return results


def get_2d_areas(hdf_path):
    if not HAS_H5PY or not os.path.isfile(hdf_path):
        return []
    if HAS_RAS_COMMANDER:
        try:
            from ras_commander.hdf.HdfMesh import HdfMesh
            from pathlib import Path
            areas = HdfMesh.get_mesh_area_names(Path(hdf_path)) or []
            # RC returns only valid mesh names — still filter just in case
            return [a for a in areas if a and a != "Attributes"]
        except BaseException:
            pass
    try:
        with h5py.File(hdf_path, "r") as hf:
            g = hf.get("Geometry/2D Flow Areas")
            if not g:
                return []
            # Filter to only groups that have cell coordinate data
            # (excludes "Attributes" and other metadata datasets)
            valid = []
            for name in g.keys():
                item = g[name]
                import h5py as _h5
                if not isinstance(item, _h5.Group):
                    continue
                if "Cells Center Coordinate" in item:
                    valid.append(name)
            return valid
    except BaseException:
        return []


def get_projection(hdf_path):
    if not HAS_H5PY:
        return None
    if HAS_RAS_COMMANDER:
        try:
            from ras_commander.hdf.HdfBase import HdfBase
            with h5py.File(hdf_path, "r") as hf:
                crs = HdfBase.get_projection(hf)
                return str(crs) if crs else None
        except BaseException:
            pass
    try:
        with h5py.File(hdf_path, "r") as hf:
            areas = list(hf.get("Geometry/2D Flow Areas", {}).keys())
            if not areas:
                return None
            p = hf.get(f"Geometry/2D Flow Areas/{areas[0]}/Projection")
            if p is None:
                return None
            v = p[()]
            if isinstance(v, bytes):
                return v.decode("utf-8", "replace")
            if isinstance(v, np.ndarray):
                return v.tobytes().decode("utf-8", "replace").rstrip("\x00")
            return str(v)
    except BaseException:
        return None


def get_summary_gdf(hdf_path, variable):
    """RC path: returns GeoDataFrame with geometry for summary results."""
    if not HAS_RAS_COMMANDER:
        return None
    try:
        from ras_commander.hdf.HdfResultsMesh import HdfResultsMesh
        from pathlib import Path
        if variable == "Maximum Water Surface":
            gdf = HdfResultsMesh.get_mesh_max_ws(Path(hdf_path))
        elif variable == "Maximum Depth":
            gdf = HdfResultsMesh.get_mesh_max_depth(Path(hdf_path))
        elif variable == "Maximum Velocity":
            gdf = HdfResultsMesh.get_mesh_max_face_v(Path(hdf_path))
        else:
            return None
        return gdf if gdf is not None and not gdf.empty else None
    except Exception:
        return None


# Keep backward compat name
read_summary_result_gdf = get_summary_gdf


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
        with h5py.File(hdf_path, "r") as hf:
            c = hf.get(f"Geometry/2D Flow Areas/{area_name}/Cells Center Coordinate")
            if c is None:
                return None, None
            a = np.array(c)
            return a[:, 0], a[:, 1]
    except BaseException:
        return None, None


def read_summary_result(hdf_path, area_name, variable):
    if not HAS_H5PY:
        return None
    _S = "Results/Unsteady/Output/Output Blocks/Base Output/Summary Output/2D Flow Areas"
    try:
        with h5py.File(hdf_path, "r") as hf:
            ds = hf.get(f"{_S}/{area_name}/{variable}")
            if ds is None:
                return None
            a = np.array(ds)
            return (a[0, :] if a.ndim == 2 and a.shape[0] == 2 else a).astype(np.float32)
    except BaseException:
        return None


def read_time_stamps(hdf_path):
    if not HAS_H5PY:
        return []
    if HAS_RAS_COMMANDER:
        try:
            from ras_commander.hdf.HdfBase import HdfBase
            from ras_commander.hdf.HdfUtils import HdfUtils
            with h5py.File(hdf_path, "r") as hf:
                start = HdfBase.get_simulation_start_time(hf)
                tp = ("Results/Unsteady/Output/Output Blocks/Base Output/"
                      "Unsteady Time Series/Time")
                if tp in hf:
                    times = HdfUtils.convert_timesteps_to_datetimes(
                        np.array(hf[tp][:]), start)
                    return [str(t) for t in times]
        except BaseException:
            pass
    _TS = ("Results/Unsteady/Output/Output Blocks/Base Output/"
           "Unsteady Time Series/Time Date Stamp")
    try:
        with h5py.File(hdf_path, "r") as hf:
            ds = hf.get(_TS)
            if ds is None:
                return []
            return [v.decode("utf-8", "replace").strip() if isinstance(v, bytes)
                    else str(v).strip() for v in ds]
    except BaseException:
        return []


def read_timeseries_result(hdf_path, area_name, variable, time_index):
    if not HAS_H5PY:
        return None
    _T = ("Results/Unsteady/Output/Output Blocks/Base Output/"
          f"Unsteady Time Series/2D Flow Areas/{area_name}/{variable}")
    try:
        with h5py.File(hdf_path, "r") as hf:
            ds = hf.get(_T)
            if ds is None:
                return None
            return np.array(ds[time_index], dtype=np.float32)
    except BaseException:
        return None


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
            with h5py.File(hdf_path, "r") as hf:
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


def hdf_summary(hdf_path):
    if not HAS_H5PY:
        return {"error": (
            "h5py not installed.\n"
            "In OSGeo4W Shell (as Administrator) run:\n"
            "  python3 -m pip download h5py --only-binary :all: -d C:\\h5py_tmp --no-deps\n"
            "  python3 -c \"import zipfile; zipfile.ZipFile('C:/h5py_tmp/h5py-3.16.0-cp312-cp312-win_amd64.whl').extractall('C:/h5py_tmp/extracted')\"\n"  # noqa: E501
            "  xcopy /E /I /Y C:\\h5py_tmp\\extracted\\h5py \"C:\\Program Files\\QGIS 3.44.8\\apps\\Python312\\Lib\\site-packages\\h5py\"\n"  # noqa: E501
            "Then restart QGIS."
        )}
    if not os.path.isfile(hdf_path):
        return {"error": f"File not found: {hdf_path}"}
    s = {"areas": {}, "time_steps": 0, "projection": None,
         "ras_commander": HAS_RAS_COMMANDER}
    s["projection"] = get_projection(hdf_path)
    times = read_time_stamps(hdf_path)
    s.update(time_steps=len(times),
             time_start=times[0] if times else "",
             time_end=times[-1] if times else "")
    _B = "Results/Unsteady/Output/Output Blocks/Base Output"
    SVS = ["Maximum Water Surface", "Maximum Depth", "Maximum Velocity"]
    TVS = ["Water Surface", "Depth", "Velocity"]
    try:
        with h5py.File(hdf_path, "r") as hf:
            for area in get_2d_areas(hdf_path):
                c = hf.get(f"Geometry/2D Flow Areas/{area}/Cells Center Coordinate")
                s["areas"][area] = {
                    "n_cells": c.shape[0] if c is not None else 0,
                    "summary_results": [v for v in SVS if hf.get(f"{_B}/Summary Output/2D Flow Areas/{area}/{v}")],
                    "timeseries_results": [
                        v for v in TVS if hf.get(f"{_B}/Unsteady Time Series/2D Flow Areas/{area}/{v}")],
                }
    except Exception as e:
        s["error"] = str(e)
    return s


# Compat aliases
RESULT_VARIABLES = {"Maximum Water Surface": ("", "Max WSE (m)", "float32"),
                    "Maximum Depth": ("", "Max Depth (m)", "float32"),
                    "Maximum Velocity": ("", "Max Velocity (m/s)", "float32")}
TIMESERIES_VARIABLES = {"Water Surface": ("", "WSE (m)"), "Depth": ("", "Depth (m)"),
                        "Velocity": ("", "Velocity (m/s)")}

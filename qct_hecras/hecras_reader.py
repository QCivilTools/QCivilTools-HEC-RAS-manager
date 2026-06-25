"""
hecras_reader.py
Parses HEC-RAS 6.x project folders without needing the RAS Controller.
"""

import os
import re


def find_project_file(folder):
    """Return the first .prj file found in folder, or None."""
    for f in os.listdir(folder):
        if f.lower().endswith(".prj"):
            return os.path.join(folder, f)
    return None


def parse_prj(prj_path):
    """
    Parse a HEC-RAS .prj file.
    Returns a dict with keys:
      title, current_plan, plans, flow_files, geom_files, unsteady_files
    Each plan entry: {id, title, file, short_id}
    """
    result = {
        "title": "",
        "prj_path": prj_path,
        "folder": os.path.dirname(prj_path),
        "current_plan": "",
        "plans": [],
        "flow_files": [],
        "geom_files": [],
        "unsteady_files": [],
    }

    if not os.path.isfile(prj_path):
        return result

    with open(prj_path, "r", errors="replace") as f:
        lines = f.readlines()

    plan_ids = []
    flow_ids = []
    geom_ids = []
    unsteady_ids = []

    for line in lines:
        line = line.strip()
        if line.startswith("Proj Title="):
            result["title"] = line.split("=", 1)[1].strip()
        elif line.startswith("Current Plan="):
            result["current_plan"] = line.split("=", 1)[1].strip()
        elif line.startswith("Plan File="):
            val = line.split("=", 1)[1].strip()
            plan_ids.append(val)
        elif line.startswith("Flow File="):
            val = line.split("=", 1)[1].strip()
            flow_ids.append(val)
        elif line.startswith("Geom File="):
            val = line.split("=", 1)[1].strip()
            geom_ids.append(val)
        elif line.startswith("Unsteady File="):
            val = line.split("=", 1)[1].strip()
            unsteady_ids.append(val)

    folder = result["folder"]
    base = os.path.splitext(os.path.basename(prj_path))[0]

    # Resolve plan files
    for pid in plan_ids:
        ext = pid  # e.g. "p01"
        plan_file = os.path.join(folder, base + "." + ext)
        plan_info = _parse_plan_file(plan_file, pid)
        result["plans"].append(plan_info)

    # Resolve flow files
    for fid in flow_ids:
        ffile = os.path.join(folder, base + "." + fid)
        result["flow_files"].append({
            "id": fid,
            "file": ffile,
            "exists": os.path.isfile(ffile),
        })

    # Resolve geometry files
    for gid in geom_ids:
        gfile = os.path.join(folder, base + "." + gid)
        result["geom_files"].append({
            "id": gid,
            "file": gfile,
            "exists": os.path.isfile(gfile),
        })

    # Resolve unsteady files
    for uid in unsteady_ids:
        ufile = os.path.join(folder, base + "." + uid)
        result["unsteady_files"].append({
            "id": uid,
            "file": ufile,
            "exists": os.path.isfile(ufile),
        })

    return result


def _parse_plan_file(plan_path, short_id):
    """Parse a .pXX plan file and return a dict of its properties."""
    info = {
        "short_id": short_id,
        "file": plan_path,
        "exists": os.path.isfile(plan_path),
        "title": "",
        "flow_file": "",
        "geom_file": "",
        "simulation_date": "",
        "start_date": "",
        "start_time": "",
        "end_date": "",
        "end_time": "",
        "computation_interval": "",
        "output_interval": "",
        "map_output_interval": "",
        "detailed_output_interval": "",
        "run_htab": "",
        "run_unsteady": "",
        "run_sediment": "",
        "run_rasmap": "",
        "run_floodplain": "",
        "plan_notes": "",
        "flow_type": "Unknown",
        "dss_file": "",
        "short_plan_id": "",
    }

    if not os.path.isfile(plan_path):
        return info

    with open(plan_path, "r", errors="replace") as f:
        lines = f.readlines()

    notes_lines = []
    in_notes = False

    for line in lines:
        s = line.strip()

        def _v():
            return s.split("=", 1)[1].strip()

        if s.startswith("Plan Title="):
            info["title"] = _v()
        elif s.startswith("Short Identifier="):
            info["short_plan_id"] = _v()
        elif s.startswith("Flow File="):
            info["flow_file"] = _v()
        elif s.startswith("Geom File="):
            info["geom_file"] = _v()
        elif s.startswith("Simulation Date="):
            raw = _v()
            info["simulation_date"] = raw
            parts = raw.split()
            if len(parts) >= 1 and "," in parts[0]:
                d, t = parts[0].split(",", 1)
                info["start_date"] = d
                info["start_time"] = t
            if len(parts) >= 2 and "," in parts[1]:
                d, t = parts[1].split(",", 1)
                info["end_date"] = d
                info["end_time"] = t
        elif s.startswith("Computation Interval="):
            info["computation_interval"] = _v()
        elif s.startswith("Output Interval="):
            info["output_interval"] = _v()
        elif s.startswith("Map Output Interval="):
            info["map_output_interval"] = _v()
        elif s.startswith("Detailed Output Interval="):
            info["detailed_output_interval"] = _v()
        elif s.startswith("Run HTab="):
            info["run_htab"] = _v()
        elif s.startswith("Run UNet="):
            info["run_unsteady"] = _v()
            info["flow_type"] = "Unsteady"
        elif s.startswith("Run Sediment="):
            info["run_sediment"] = _v()
        elif s.startswith("Run RASMapper="):
            info["run_rasmap"] = _v()
        elif s.startswith("Run Floodplain="):
            info["run_floodplain"] = _v()
        elif s.startswith("Project DSS File="):
            info["dss_file"] = _v()
        elif s.startswith("Plan Notes="):
            in_notes = True
            val = s.split("=", 1)[1].strip()
            if val:
                notes_lines.append(val)
        elif in_notes:
            if re.match(r"^[A-Za-z ]+=", s):
                in_notes = False
            else:
                notes_lines.append(s)

    if not info["run_unsteady"]:
        info["flow_type"] = "Steady"
    info["plan_notes"] = " ".join(notes_lines).strip()
    return info


def get_result_files(plan_info):
    """Return list of result files (.hdf, .log, .comp.hdf) for a plan."""
    results = []
    folder = os.path.dirname(plan_info["file"])
    base = os.path.splitext(os.path.basename(plan_info["file"]))[0]
    short = plan_info["short_id"]

    candidates = [
        os.path.join(folder, base + "." + short + ".hdf"),
        os.path.join(folder, base + "." + short + ".log"),
        os.path.join(folder, base + "." + short + ".O01"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            results.append(c)
    return results

# -*- coding: utf-8 -*-
"""
rasmap_reader.py
Scans a HEC-RAS .rasmap file and finds stored raster outputs (Depth, Velocity,
WSE .tif files) produced by RAS Mapper post-processing.
"""
import os
import re

# Use defusedxml.ElementTree.parse() to satisfy Bandit B314 (safe XML parsing).
# defusedxml is bundled with QGIS. If somehow absent, fall back to the stdlib
# variant — .rasmap files are local HEC-RAS project files, not untrusted network XML.
try:
    import defusedxml.ElementTree as ET
    HAS_XML = True
except ImportError:
    import xml.etree.ElementTree as ET  # nosec B405 — local files only fallback
    HAS_XML = True


def find_rasmap_file(prj_path):
    """Find the .rasmap file next to the .prj file."""
    folder = os.path.dirname(prj_path)
    basename = os.path.splitext(os.path.basename(prj_path))[0]
    candidate = os.path.join(folder, basename + ".rasmap")
    if os.path.isfile(candidate):
        return candidate
    # Sometimes it's just Project.rasmap
    for f in os.listdir(folder):
        if f.lower().endswith(".rasmap"):
            return os.path.join(folder, f)
    return None


def scan_rasmap(rasmap_path):
    """
    Parse a .rasmap file and return a list of plan result dicts, each with:
      plan_name, postprocessing_hdf, pp_exists, stored_maps: [{name, path, exists}]

    HEC-RAS plan subfolders:
      Each plan has its own subfolder (e.g. "ARI 100Y (Ex)\") containing
      PostProcessing.hdf and the computed .tif rasters.
    """
    if not HAS_XML or not os.path.isfile(rasmap_path):
        return []

    folder = os.path.dirname(rasmap_path)

    try:
        tree = ET.parse(rasmap_path)  # nosec B314
        root = tree.getroot()
    except ET.ParseError:
        return []

    plans = []

    # Find the Results node (may be nested or at root level)
    results_node = root.find(".//Results")
    if results_node is None:
        results_node = root

    # Each direct child of Results with Type="RASResults" is a plan
    for plan_layer in list(results_node):
        layer_type = plan_layer.get("Type", "")
        if layer_type != "RASResults":
            continue

        plan_name = plan_layer.get("Name", "Unknown Plan")
        pp_rel = plan_layer.get("Filename", "")
        pp_path = _resolve_path(folder, pp_rel)

        # Plan subfolder = directory containing PostProcessing.hdf
        plan_folder = os.path.dirname(pp_path) if pp_path else None

        stored_maps = []
        seen_paths = set()

        # 1. Walk XML for explicit RASResultsMapOutputLayer entries
        for child in plan_layer.iter():
            if child.get("Type") == "RASResultsMapOutputLayer":
                map_name = child.get("Name", "")
                map_rel = child.get("Filename", "")
                map_path = _resolve_path(folder, map_rel)
                if map_path and map_path not in seen_paths:
                    seen_paths.add(map_path)
                    stored_maps.append({
                        "name": f"{plan_name} — {map_name}",
                        "short_name": map_name,
                        "path": map_path,
                        "exists": os.path.isfile(map_path),
                        "plan_name": plan_name,
                    })

        # 2. Scan the plan subfolder for any .tif files not already listed
        #    (HEC-RAS may write rasters not referenced in the rasmap)
        if plan_folder and os.path.isdir(plan_folder):
            for fname in sorted(os.listdir(plan_folder)):
                if not (fname.lower().endswith(".tif") or fname.lower().endswith(".tiff")):
                    continue
                full = os.path.join(plan_folder, fname)
                if full in seen_paths:
                    continue
                seen_paths.add(full)
                short = os.path.splitext(fname)[0]
                # Strip .hdf suffix if present (e.g. "Depth (0.1m+).hdf.tif")
                short = re.sub(r'\.hdf$', '', short, flags=re.I)
                stored_maps.append({
                    "name": f"{plan_name} — {short}",
                    "short_name": short,
                    "path": full,
                    "exists": True,
                    "plan_name": plan_name,
                })

        plans.append({
            "plan_name": plan_name,
            "plan_folder": plan_folder,
            "postprocessing_hdf": pp_path,
            "pp_exists": os.path.isfile(pp_path) if pp_path else False,
            "stored_maps": stored_maps,
        })

    return plans


def _resolve_path(base_folder, rel_path):
    """Resolve a possibly-relative path from the .rasmap file."""
    if not rel_path:
        return ""
    # Replace backslashes, strip leading ./
    rel_path = rel_path.replace("\\", os.sep).replace("/", os.sep)
    rel_path = re.sub(r"^\." + re.escape(os.sep), "", rel_path)
    full = os.path.normpath(os.path.join(base_folder, rel_path))
    return full


def find_terrains(rasmap_path):
    """
    Parse a .rasmap file and return a list of terrain layer dicts:
      {name, path, exists}
    HEC-RAS stores project terrain references under a <Terrains> node, e.g.:
      <Terrains>
        <Layer Name="Terrain" Filename=".\\Terrain\\Terrain.hdf" .../>
      </Terrains>
    Terrain files are typically a merged .hdf (HEC-RAS Terrain format) or
    occasionally a direct .tif/.vrt reference.
    """
    if not HAS_XML or not os.path.isfile(rasmap_path):
        return []
    folder = os.path.dirname(rasmap_path)
    try:
        tree = ET.parse(rasmap_path)  # nosec B314
        root = tree.getroot()
    except ET.ParseError:
        return []

    terrains = []
    seen = set()
    terrains_node = root.find(".//Terrains")
    search_roots = [terrains_node] if terrains_node is not None else [root]

    for node in search_roots:
        if node is None:
            continue
        for layer in node.iter("Layer"):
            rel = layer.get("Filename", "")
            if not rel:
                continue
            full = _resolve_path(folder, rel)
            if full in seen:
                continue
            seen.add(full)
            terrains.append({
                "name": layer.get("Name", os.path.basename(full)),
                "path": full,
                "exists": os.path.isfile(full),
            })

    return terrains

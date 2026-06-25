[general]
name=QCT HEC-RAS Manager
qgisMinimumVersion=3.16
description=HEC-RAS 2D workflow manager for QGIS: project browser, plan editor, batch runner, result viewer, animation, and flow hydrograph extraction — no HEC-RAS GUI required for results.
version=1.0.0
author=Dat Vu
email=datmast@gmail.com
about=QCT HEC-RAS Manager is a self-contained QGIS plugin for the full HEC-RAS 2D unsteady-flow workflow.
    Tab 1 Project Browser: load .prj projects, auto-detect HDFs, sync QGIS CRS to model.
    Tab 2 Plan Editor: edit raw .pXX files; configure HDF5 output variables via checklist mirroring HEC-RAS Output Control Options dialog.
    Tab 3 Run Manager: sequential or parallel runs via RAS Commander or direct Ras.exe; per-plan core control.
    Tab 4 Result Viewer: load Depth/WSE/Velocity/Bed Level as point or cell-mesh layers; load terrain from .rasmap; launch RAS Mapper.
    Tab 5 Animate: step through all timesteps live on the QGIS canvas with colour-ramped cell-mesh layer.
    Tab 6 Postprocess: extract and plot flow hydrographs at drawn cross-sections or reference lines using official Q=V x A method matching RAS Mapper; save PNG/CSV.
    Tab 7 Log: colour-coded activity log.
    Requires: pip install h5py (OSGeo4W Shell). Optional: pip install ras-commander for parallel runs.
tracker=https://github.com/QCivilTools/QCivilTools-HEC-RAS-manager/issues
repository=https://github.com/QCivilTools/QCivilTools-HEC-RAS-manager
tags=HEC-RAS,hydraulic,flood,2D,unsteady,results,civil engineering,hydrograph,animation
homepage=https://github.com/QCivilTools/QCivilTools-HEC-RAS-manager
category=Plugins
icon=icon.png
experimental=False
deprecated=False

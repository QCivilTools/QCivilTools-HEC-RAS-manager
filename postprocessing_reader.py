# -*- coding: utf-8 -*-
"""
hecras_runner.py  —  QCT HEC-RAS Runner
Runs HEC-RAS plans via three engines (in priority order):
  1. RAS Commander (bundled ras_commander package) — recommended
  2. RAS Executable (Ras.exe -c) — subprocess, no COM
  3. RAS Controller (win32com) — legacy COM interface
Author: Dat Vu | https://github.com/datmast-cmd
"""
import os
import sys
import time
import shutil
import tempfile
import threading

from qgis.PyQt.QtCore import QObject, pyqtSignal, QThread

# Ensure bundled ras_commander is importable
# Try qct_hecras bundle first, then qct_ras_results bundle
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)


def _init_rc(prj_folder, ras_exe, ras_obj):
    """Call init_ras_project handling different API versions of ras_commander."""
    from ras_commander import init_ras_project
    import inspect
    sig = inspect.signature(init_ras_project)
    kwargs = {"ras_object": ras_obj}
    # suppress_logging only exists in some versions
    if "suppress_logging" in sig.parameters:
        kwargs["suppress_logging"] = True
    # ras_object vs ras_instance naming difference
    if "ras_object" not in sig.parameters and "ras_instance" in sig.parameters:
        kwargs = {"ras_instance": ras_obj}
        if "suppress_logging" in sig.parameters:
            kwargs["suppress_logging"] = True
    init_ras_project(prj_folder, ras_exe, **kwargs)


def _setup_rc_path():
    """Add ras_commander to sys.path from qct_ras_results if not already importable."""
    try:
        import ras_commander  # noqa: F401
        return True
    except ImportError:
        pass
    # Try qct_ras_results plugin directory
    try:
        import importlib.util
        spec = importlib.util.find_spec("qct_ras_results")
        if spec:
            plugin_dir = os.path.dirname(spec.origin)
            if plugin_dir and plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)
            import ras_commander  # noqa: F401,F811
            return True
    except Exception:
        pass
    # Try scanning QGIS plugin paths
    try:
        from qgis.core import QgsApplication
        plugin_paths = [
            os.path.join(p, "qct_ras_results")
            for p in QgsApplication.pluginPath().split(";")
            if p
        ]
        for pp in plugin_paths:
            if os.path.isdir(pp) and pp not in sys.path:
                sys.path.insert(0, pp)
                try:
                    import ras_commander  # noqa: F401,F811
                    return True
                except ImportError:
                    sys.path.remove(pp)
    except Exception:
        pass
    return False


_RC_AVAILABLE = _setup_rc_path()


# ── Single plan runner ─────────────────────────────────────────────────────────
class PlanRunner(QThread):
    """
    Runs one HEC-RAS plan in a temporary folder, then moves results back.
    Emits log_line(plan_title, message) and finished(plan_title, success, elapsed).
    """
    log_line = pyqtSignal(str, str)    # (plan_title, message)
    finished = pyqtSignal(str, bool, float)

    def __init__(self, plan_info, prj_path,
                 ras_exe="", use_controller=True, progid_override="",
                 use_ras_commander=True, num_cores=None):
        super().__init__()
        self.plan_info = plan_info
        self.prj_path = prj_path
        self.ras_exe = ras_exe
        self.use_controller = use_controller
        self.progid_override = progid_override
        self.use_ras_commander = use_ras_commander
        self.num_cores = num_cores
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def emit_log(self, msg):
        title = self.plan_info.get("title") or self.plan_info["short_id"]
        self.log_line.emit(title, msg)

    def run(self):
        title = self.plan_info.get("title") or self.plan_info["short_id"]
        t0 = time.time()
        success = False
        tmp_dir = None

        try:
            # ── Engine 1: RAS Commander ───────────────────────────────────────
            if self.use_ras_commander:
                success = self._run_via_ras_commander()
                if success is not None:   # None = RC not available, try next
                    elapsed = round(time.time() - t0, 1)
                    self.emit_log(f"{'Completed' if success else 'Failed'} in {elapsed}s")
                    self.finished.emit(title, success, elapsed)
                    return

            # ── Engines 2 & 3: temp folder approach ──────────────────────────
            self.emit_log("Setting up temporary run folder...")
            run_prj, tmp_dir = self._setup_temp_folder()

            if self._cancelled:
                self.emit_log("Cancelled before compute started.")
                self.finished.emit(title, False, 0.0)
                return

            if not self.use_controller:
                success = self._run_via_subprocess(run_prj)
            else:
                success = self._run_via_controller(run_prj)

            if success:
                self.emit_log("Collecting results → original project folder...")
                moved, skipped = self._collect_results(tmp_dir)
                self.emit_log(
                    f"Results collected: {moved} file(s) moved to project, "
                    f"{skipped} skipped (unchanged).")
            else:
                self.emit_log(
                    f"Run failed — results NOT moved. "
                    f"Temp folder kept for inspection: {tmp_dir}")
                tmp_dir = None

        except Exception as e:
            import traceback
            self.emit_log(f"ERROR: {e}")
            self.emit_log(traceback.format_exc())
            success = False

        finally:
            if tmp_dir and os.path.exists(tmp_dir):
                removed = False
                for _attempt in range(4):
                    try:
                        shutil.rmtree(tmp_dir)
                        self.emit_log("Temp folder removed.")
                        removed = True
                        break
                    except Exception:
                        time.sleep(0.5)
                if not removed:
                    try:
                        shutil.rmtree(tmp_dir, ignore_errors=True)
                        self.emit_log("Temp folder removed (ignore_errors).")
                    except Exception as e:
                        self.emit_log(f"Warning: could not remove temp folder: {e}")

        elapsed = round(time.time() - t0, 1)
        self.emit_log(f"{'Completed' if success else 'Failed'} in {elapsed}s")
        self.finished.emit(title, success, elapsed)

    # ── Engine 1: RAS Commander ───────────────────────────────────────────────
    def _run_via_ras_commander(self):
        """
        Run via bundled RAS Commander. Returns True/False on success/failure,
        or None if RAS Commander is not available.
        """
        if not _setup_rc_path():
            self.emit_log("RAS Commander not available — falling back to subprocess runner.")
            return None
        try:
            from ras_commander import init_ras_project, RasCmdr, RasPrj  # noqa: F401,F811
        except ImportError as e:
            self.emit_log(f"RAS Commander import failed ({e}) — falling back.")
            return None

        sid = self.plan_info["short_id"]   # e.g. "p01"
        # RC expects plan number like "01", not "p01"
        plan_num = sid.lstrip("p") if sid.startswith("p") else sid

        prj_folder = os.path.dirname(self.prj_path)
        ras_exe = self.ras_exe or "Ras.exe"

        self.emit_log(f"RAS Commander: init_ras_project({prj_folder})")
        try:
            ras_obj = RasPrj()
            _init_rc(prj_folder, ras_exe, ras_obj)
        except Exception as e:
            self.emit_log(f"RAS Commander init failed: {e} — falling back.")
            return None

        self.emit_log(f"RAS Commander: compute_plan({plan_num})...")

        def _stream(msg):
            if msg and str(msg).strip():
                self.emit_log(str(msg).strip())

        try:
            import inspect as _inspect
            _sig = _inspect.signature(RasCmdr.compute_plan)
            _kw = dict(
                ras_object=ras_obj,
                dest_folder=None,
                force_rerun=True,
                num_cores=self.num_cores,
                stream_callback=None,
            )
            # dialog_watchdog=True handles any other popups via RC's watchdog
            if "dialog_watchdog" in _sig.parameters:
                _kw["dialog_watchdog"] = True
            result = RasCmdr.compute_plan(plan_num, **_kw)
            # ComputeResult is truthy on success
            success = bool(result)
            self.emit_log(f"RAS Commander: {'success' if success else 'failed'}.")
            return success
        except Exception as e:
            self.emit_log(f"RAS Commander compute_plan error: {e}")
            import traceback
            self.emit_log(traceback.format_exc())
            return False

    # ── Engine 2: RAS Executable (subprocess) ────────────────────────────────
    def _run_via_subprocess(self, run_prj):
        """
        Run Ras.exe with capture_output=True — same as RC's compute_plan.
        capture_output captures both stdout AND stderr into pipes, which
        prevents HEC-RAS from attaching to the console and showing the TCU dialog.
        """
        import subprocess
        if not self.ras_exe or not os.path.isfile(self.ras_exe):
            self.emit_log(
                "ERROR: RAS Executable path not set or file not found. "
                "Set the path in Project + Settings tab.")
            return False
        cmd = [self.ras_exe, "-c", run_prj]
        self.emit_log(f"Launching headless: {self.ras_exe} -c {run_prj}")
        try:
            # Use list form (no shell=True) — avoids shell injection risk and
            # correctly handles paths with spaces on Windows without quoting.
            # capture_output=True: redirects both stdout+stderr to pipes,
            # preventing HEC-RAS GUI/TCU dialog from appearing.
            result = subprocess.run(
                cmd,
                shell=False,
                capture_output=True,
                text=True,
                cwd=os.path.dirname(run_prj))
            # Emit captured output to log
            for line in (result.stdout or "").splitlines():
                if line.strip():
                    self.emit_log(line.strip())
            if result.returncode == 0:
                self.emit_log("RAS Executable finished successfully.")
                return True
            else:
                for line in (result.stderr or "").splitlines():
                    if line.strip():
                        self.emit_log(line.strip())
                self.emit_log(f"RAS Executable exited with code {result.returncode}")
                return False
        except Exception as e:
            self.emit_log(f"RAS Executable error: {e}")
            return False

    # ── Engine 3: RAS Controller (COM) ───────────────────────────────────────
    def _run_via_controller(self, run_prj):
        try:
            import win32com.client
        except ImportError:
            self.emit_log("ERROR: pywin32 not installed. Use RAS Executable or RAS Commander.")
            return False

        progid = self.progid_override
        if not progid:
            for ver in ["701", "700", "660", "650", "631", "630", "541", "535", "500"]:
                try:
                    win32com.client.Dispatch(f"RAS{ver}.HECRASController")
                    progid = f"RAS{ver}.HECRASController"
                    break
                except Exception:
                    pass
        if not progid:
            self.emit_log("ERROR: No RAS Controller COM object found.")
            return False

        self.emit_log(f"RAS Controller connected via: {progid}")
        try:
            ras = win32com.client.Dispatch(progid)
            self.emit_log(f"Opening project via RAS Controller ({progid})...")
            ras.Project_Open(run_prj)
            sid = self.plan_info["short_id"]
            plan_file = os.path.join(
                os.path.dirname(run_prj),
                os.path.splitext(os.path.basename(run_prj))[0] + f".{sid}")
            ras.Plan_SetCurrent(plan_file)
            self.emit_log(f"Plan set: {self.plan_info.get('title') or sid}")
            self.emit_log("Computing...")
            nmsg, msgs, _, _, _ = ras.Compute_CurrentPlan(None, None, True)
            for i in range(nmsg):
                try:
                    m = str(msgs[i]).strip()
                    if m:
                        self.emit_log(m)
                except Exception:
                    pass
            ras.Project_Close()
            self.emit_log("RAS Controller finished.")
            return True
        except Exception as e:
            self.emit_log(f"RAS Controller error: {e}")
            try:
                ras.Project_Close()
            except Exception:
                pass
            return False

    # ── Temp folder helpers ───────────────────────────────────────────────────
    def _setup_temp_folder(self):
        sid = self.plan_info["short_id"]
        title = (self.plan_info.get("title") or sid).replace(" ", "_").replace("/", "_")
        prefix = f"QCT_HEC_{title[:30]}_"
        tmp_dir = tempfile.mkdtemp(prefix=prefix)
        self.emit_log(f"Temp folder: {tmp_dir}")
        src = os.path.dirname(self.prj_path)
        for item in os.listdir(src):
            s = os.path.join(src, item)
            d = os.path.join(tmp_dir, item)
            try:
                if os.path.isfile(s):
                    shutil.copy2(s, d)
                elif os.path.isdir(s):
                    shutil.copytree(s, d)
            except Exception:
                pass
        prj_name = os.path.basename(self.prj_path)
        run_prj = os.path.join(tmp_dir, prj_name)
        self.emit_log(f"Project copied to temp ({os.path.basename(tmp_dir)})")
        return run_prj, tmp_dir

    RESULT_EXTS = {
        ".hdf", ".dss", ".log", ".blf", ".bco", ".rst", ".O01", ".sdf",
        ".tif", ".tiff", ".xml", ".rasmap", ".prj.hdf",
    }

    def _collect_results(self, tmp_dir):
        src_folder = os.path.dirname(self.prj_path)
        moved = skipped = 0
        for root, dirs, files in os.walk(tmp_dir):
            rel = os.path.relpath(root, tmp_dir)
            dst_dir = os.path.join(src_folder, rel) if rel != "." else src_folder
            os.makedirs(dst_dir, exist_ok=True)
            for fname in files:
                if any(fname.lower().endswith(e) for e in self.RESULT_EXTS):
                    src_f = os.path.join(root, fname)
                    dst_f = os.path.join(dst_dir, fname)
                    try:
                        # Always overwrite result files — skip only unchanged input files
                        force_exts = {".hdf", ".log", ".blf", ".bco", ".O01", ".dss"}
                        force = any(fname.lower().endswith(e) for e in force_exts)
                        if not force and os.path.isfile(dst_f):
                            src_m = os.path.getmtime(src_f)
                            dst_m = os.path.getmtime(dst_f)
                            if src_m <= dst_m + 1:
                                skipped += 1
                                continue
                        shutil.copy2(src_f, dst_f)
                        moved += 1
                    except Exception:
                        skipped += 1
        return moved, skipped


# ── Multi-plan manager ─────────────────────────────────────────────────────────
class MultiPlanRunManager(QObject):
    """
    Manages sequential or parallel execution of multiple plans.
    Signals:
      log_line(plan_title, message)
      plan_finished(title, success, elapsed)
      all_finished(list of (title, success, elapsed))
    """
    log_line = pyqtSignal(str, str)
    plan_finished = pyqtSignal(str, bool, float)
    all_finished = pyqtSignal(list)

    def __init__(self, plans, prj_path, run_dir,
                 parallel=False, max_workers=2, progid_override="",
                 ras_exe="", use_controller=True,
                 use_ras_commander=True, num_cores=None):
        super().__init__()
        self.plans = plans
        self.prj_path = prj_path
        self.run_dir = run_dir
        self.parallel = parallel
        self.max_workers = max_workers
        self.progid_override = progid_override
        self.ras_exe = ras_exe
        self.use_controller = use_controller
        self.use_ras_commander = use_ras_commander
        self.num_cores = num_cores
        self._results = []
        self._threads = []
        self._runners = []
        self._pending = []
        self._active = 0
        self._lock = threading.Lock()

    def cancel(self):
        for r in self._runners:
            r.cancel()

    def start(self):
        if self.run_dir:
            os.makedirs(self.run_dir, exist_ok=True)
        self._results = []

        # If using RAS Commander parallel — let RC handle it natively
        if self.use_ras_commander and self.parallel and len(self.plans) > 1:
            self._run_rc_parallel()
        elif self.parallel:
            self._run_parallel()
        else:
            self._run_sequential()

    def _make_runner(self, plan):
        return PlanRunner(
            plan_info=plan,
            prj_path=self.prj_path,
            ras_exe=self.ras_exe,
            use_controller=self.use_controller,
            progid_override=self.progid_override,
            use_ras_commander=self.use_ras_commander,
            num_cores=self.num_cores,
        )

    # ── RC native parallel ────────────────────────────────────────────────────
    def _run_rc_parallel(self):
        """Use RAS Commander compute_parallel — handles worker folders natively."""
        class _RCParallelThread(QThread):
            done = pyqtSignal(list)

            def __init__(self, plans, prj_path, ras_exe, max_workers, num_cores, log_fn):
                super().__init__()
                self.plans = plans
                self.prj_path = prj_path
                self.ras_exe = ras_exe
                self.max_workers = max_workers
                self.num_cores = num_cores
                self.log_fn = log_fn

            def run(self):
                results = []
                try:
                    from ras_commander import init_ras_project, RasCmdr, RasPrj  # noqa: F401,F811
                    prj_folder = os.path.dirname(self.prj_path)
                    ras_obj = RasPrj()
                    _init_rc(prj_folder, self.ras_exe or "Ras.exe", ras_obj)
                    plan_nums = []
                    for p in self.plans:
                        sid = p["short_id"]
                        plan_nums.append(sid.lstrip("p") if sid.startswith("p") else sid)
                    self.log_fn("all", f"RAS Commander: compute_parallel({plan_nums}, "
                                f"max_workers={self.max_workers})")
                    rc_results = RasCmdr.compute_parallel(
                        plan_number=plan_nums,
                        max_workers=self.max_workers,
                        num_cores=self.num_cores or 2,
                        dest_folder=None,
                        force_rerun=True,
                        ras_object=ras_obj,
                    )
                    for p in self.plans:
                        sid = p["short_id"]
                        num = sid.lstrip("p") if sid.startswith("p") else sid
                        ok = bool(rc_results.get(num, False))
                        title = p.get("title") or sid
                        self.log_fn(title, f"{'✅ Complete' if ok else '❌ Failed'}")
                        results.append((title, ok, 0.0))
                except Exception as e:
                    import traceback
                    self.log_fn("all", f"RAS Commander parallel error: {e}\n{traceback.format_exc()}")
                    for p in self.plans:
                        results.append((p.get("title") or p["short_id"], False, 0.0))
                self.done.emit(results)

        self._rc_thread = _RCParallelThread(
            self.plans, self.prj_path, self.ras_exe,
            self.max_workers, self.num_cores,
            lambda title, msg: self.log_line.emit(title, msg))

        def _on_done(results):
            self._results = results
            for title, ok, elapsed in results:
                self.plan_finished.emit(title, ok, elapsed)
            self.all_finished.emit(self._results)

        self._rc_thread.done.connect(_on_done)
        self._rc_thread.start()

    # ── Sequential ────────────────────────────────────────────────────────────
    def _run_sequential(self):
        self._pending = list(self.plans)
        self._launch_next_sequential()

    def _launch_next_sequential(self):
        if not self._pending:
            self.all_finished.emit(self._results)
            return
        plan = self._pending.pop(0)
        runner = self._make_runner(plan)
        self._runners.append(runner)
        runner.log_line.connect(self.log_line)
        runner.finished.connect(self._on_sequential_done)
        runner.start()

    def _on_sequential_done(self, title, success, elapsed):
        self._results.append((title, success, elapsed))
        self.plan_finished.emit(title, success, elapsed)
        self._launch_next_sequential()

    # ── Parallel (non-RC) ─────────────────────────────────────────────────────
    def _run_parallel(self):
        self._pending = list(self.plans)
        self._active = 0
        self._launch_batch()

    def _launch_batch(self):
        while self._pending and self._active < self.max_workers:
            plan = self._pending.pop(0)
            self._launch_one(plan, on_done=self._on_parallel_done)
            self._active += 1

    def _launch_one(self, plan, on_done):
        runner = self._make_runner(plan)
        self._runners.append(runner)
        runner.log_line.connect(self.log_line)
        runner.finished.connect(on_done)
        runner.start()

    def _on_parallel_done(self, title, success, elapsed):
        with self._lock:
            self._results.append((title, success, elapsed))
            self._active -= 1
        self.plan_finished.emit(title, success, elapsed)
        if self._pending:
            self._launch_batch()
        elif self._active == 0:
            self.all_finished.emit(self._results)

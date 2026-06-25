def classFactory(iface):
    # Nuke __pycache__ so stale .pyc files never mask updated code
    import os
    import shutil
    _dir = os.path.dirname(__file__)
    for _root, _dirs, _files in os.walk(_dir):
        for _d in _dirs:
            if _d == "__pycache__":
                try:
                    shutil.rmtree(os.path.join(_root, _d))
                except Exception:
                    pass
    from .plugin import QCTHECRASPlugin
    return QCTHECRASPlugin(iface)

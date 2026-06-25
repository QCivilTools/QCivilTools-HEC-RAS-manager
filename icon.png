# -*- coding: utf-8 -*-
import os
import shutil
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon

PLUGIN_DIR = os.path.dirname(__file__)


def _clear_pycache():
    for root, dirs, files in os.walk(PLUGIN_DIR):
        for d in list(dirs):
            if d == "__pycache__":
                try:
                    shutil.rmtree(os.path.join(root, d))
                except Exception:
                    pass


_clear_pycache()


def get_or_create_qct_menu(iface):
    from qgis.PyQt.QtWidgets import QMenu
    mb = iface.mainWindow().menuBar()
    for a in mb.actions():
        if a.text() == "QCivilTools":
            return a.menu()
    menu = QMenu("QCivilTools", iface.mainWindow())
    mb.insertMenu(iface.pluginMenu().menuAction(), menu)
    return menu


def remove_action_from_qct_menu(iface, action):
    mb = iface.mainWindow().menuBar()
    for a in mb.actions():
        if a.text() == "QCivilTools":
            a.menu().removeAction(action)
            if not a.menu().actions():
                mb.removeAction(a)
            return


class QCTHECRASPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dialog = None

    def initGui(self):
        icon = QIcon(os.path.join(PLUGIN_DIR, "icon.png"))
        self.action = QAction(icon, "HEC-RAS Manager", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        get_or_create_qct_menu(self.iface).addAction(self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        remove_action_from_qct_menu(self.iface, self.action)
        self.iface.removeToolBarIcon(self.action)
        if self.dialog:
            self.dialog.close()
            self.dialog = None

    def run(self):
        if not self.dialog:
            from .hecras_dialog import HECRASDialog
            self.dialog = HECRASDialog(self.iface, self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()

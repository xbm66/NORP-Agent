"""
PyInstaller hook for pywebview GTK backend on Linux.
Ensures gobject-introspection typelibs are collected.
"""
import os
import glob
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# Collect all gi.repository submodules
hiddenimports = collect_submodules('gi.repository')

# Ensure WebKit2 and related typelibs
hiddenimports += [
    'gi.repository.Gtk',
    'gi.repository.Gdk',
    'gi.repository.GLib',
    'gi.repository.GObject',
    'gi.repository.WebKit2',
    'gi.repository.Soup',
    'gi.repository.JavaScriptCore',
    'gi.repository.Atk',
    'gi.repository.Pango',
    'gi.repository.cairo',
    'gi.repository.GdkPixbuf',
    'gi.repository.Gio',
    'gi.repository.GModule',
]

# Collect typelib files from system
datas = []
typelib_dirs = [
    '/usr/lib/girepository-1.0',
    '/usr/lib/x86_64-linux-gnu/girepository-1.0',
    '/usr/lib64/girepository-1.0',
]

for typelib_dir in typelib_dirs:
    if os.path.isdir(typelib_dir):
        for f in glob.glob(os.path.join(typelib_dir, '*.typelib')):
            datas.append((f, 'gi_typelibs'))

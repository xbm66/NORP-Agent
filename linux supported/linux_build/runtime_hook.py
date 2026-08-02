"""
PyInstaller runtime hook for pywebview on Linux.
Ensures GTK typelib path is set correctly in frozen executables.
"""
import os
import sys

# When running as a PyInstaller bundle, add the bundled typelib path
if getattr(sys, 'frozen', False):
    # The typelibs are extracted to a temp directory by PyInstaller
    # We need to help gi find them
    typelib_path = os.path.join(sys._MEIPASS, 'gi_typelibs')
    if os.path.isdir(typelib_path):
        # Prepend to GI_TYPELIB_PATH
        existing = os.environ.get('GI_TYPELIB_PATH', '')
        if existing:
            os.environ['GI_TYPELIB_PATH'] = typelib_path + os.pathsep + existing
        else:
            os.environ['GI_TYPELIB_PATH'] = typelib_path

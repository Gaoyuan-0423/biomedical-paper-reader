"""Desktop data locations, independent of the replaceable application folder."""
import os
import sys
from pathlib import Path


def desktop_data_dir(platform=None, environ=None, home=None):
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    explicit = environ.get('BIOREADER_DATA_DIR')
    if explicit:
        return Path(explicit).expanduser()
    if platform == 'win32':
        return Path(environ.get('LOCALAPPDATA') or home / 'AppData' / 'Local') / 'BioReader'
    if platform == 'darwin':
        return home / 'Library' / 'Application Support' / 'BioReader'
    return Path(environ.get('XDG_DATA_HOME') or home / '.local' / 'share') / 'BioReader'

"""Launcher for the Streamlit app.

Exists because the preview system starts processes from a working directory that macOS
will not let us read: the shell reports "getcwd: cannot access parent directories", `cd`
then refuses to run, and Streamlit dies in `Path.cwd()` while looking for config.toml.

`os.chdir()` to an absolute path is a plain syscall that never reads the old directory,
so doing the chdir here -- before Streamlit is imported -- sidesteps all of it.

Honours $PORT so the preview system can assign a free port.
"""
import os
import sys

# Launchers invoke this with an absolute path, so __file__ is absolute and abspath()
# never needs the (possibly unreadable) current directory. Resolving from __file__ rather
# than a fixed string is what lets a clone of the repo run from any location.
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

sys.argv = [
    "streamlit", "run", os.path.join(HERE, "app.py"),
    "--server.headless", "true",
    "--server.port", os.environ.get("PORT", "8501"),
]

from streamlit.web.cli import main  # noqa: E402  (must follow the chdir)

sys.exit(main())

"""PyInstaller entry script for the desktop app (packaging/jobmatch.spec).

freeze_support() must run first in the frozen main script: NiceGUI opens the native window
in a child process, and a frozen app would otherwise start itself again in a loop.
"""

import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from jobmatch.app.__main__ import main

    main()

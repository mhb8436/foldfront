import sys
import time

print("Starting embedded PyMOL...")
import pymol

pymol.pymol_argv = ["pymol", "-qc"]  # Quiet and no GUI
pymol.finish_launching()

print("Loading claudemol plugin...")
import claudemol.plugin

claudemol.plugin.claude_start()

print("Ready and listening. Press Ctrl+C to exit.")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Exiting...")
    claudemol.plugin.claude_stop()
    pymol.cmd.quit()

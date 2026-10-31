# Shared kit file (blender-addon-kit). Do not edit here: change it in the kit
# and run sync_kit.py.
"""Run every plain-Python test and print a summary.

    python tests/plain/_run_all.py

These tests need no Blender. They load the add-on's logic modules with
small stand-ins for bpy and mathutils. Add whatever they need (numpy, say) to your dev dependencies.
"""
import subprocess
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
tests = sorted(path for path in here.glob("*.py") if not path.name.startswith("_"))
failed = []

for path in tests:
    result = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)
    last = (result.stdout.strip().splitlines() or ["(no output)"])[-1]

    if result.returncode:
        failed.append(path.name)
        print(f"FAIL  {path.name}")
        print((result.stdout + result.stderr).strip()[-1500:])
    else:
        print(f"ok    {path.name}: {last}")

print()
print("All plain tests passed" if not failed else f"FAILED: {', '.join(failed)}")
sys.exit(1 if failed else 0)

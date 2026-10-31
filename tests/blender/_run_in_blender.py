# Shared kit file (blender-addon-kit). Do not edit here: change it in the kit
# and run sync_kit.py.
"""Runs ONE Blender test script inside Blender. Started by build.py.

    blender --python _run_in_blender.py -- <test file>

Settings come from environment variables set by build.py:
    ADDON_MODULE  the name Blender imports the add-on under
    ADDON_ROOT    the folder the add-on was installed into (must be the one tested)
    ADDON_RESULT  where to write the result (JSON)

A test file that needs a real window (it switches scenes, say) says so in its
first lines with a comment:  # needs-window
In headless runs (--background) such a test is reported as "skipped", never as passed.

A test passes when it prints at least one line starting with "PASS:" and ends
with a line starting with "ALL " and ending with "PASSED". Any exception fails
it. The result file is how build.py learns the outcome, so a crash or a
timeout can never look like a pass.
"""
import contextlib
import io
import json
import os
import sys
import traceback

import bpy


class Tee(io.StringIO):
    """Keep a copy of the output and still print it."""

    def write(self, text):
        stream = sys.__stdout__

        if stream is not None:  # a GUI Blender without a console has no stdout
            stream.write(text)
            stream.flush()

        return super().write(text)


def needs_window(source):
    return any(line.strip() == "# needs-window" for line in source.splitlines()[:15])


def run_test(test_path, module_name, expected_root, result):
    import addon_utils

    module = addon_utils.enable(module_name, default_set=False, persistent=False)

    if module is None:
        raise RuntimeError(f"The add-on '{module_name}' could not be enabled. See the error above")

    loaded = os.path.realpath(module.__file__)
    expected = os.path.realpath(expected_root)

    if not loaded.startswith(expected + os.sep):
        raise RuntimeError(f"Tested the wrong copy of the add-on: {loaded} (expected under {expected})")

    with open(test_path, encoding="utf-8") as handle:
        source = handle.read()

    # bpy.app.background is the real "no UI" flag. bpy.context.window is not:
    # Blender 5.x gives background runs a window object.
    if needs_window(source) and bpy.app.background:
        result["status"] = "skipped"
        result["error"] = "Needs a Blender window (run without --headless)"
        return

    output = Tee()

    with contextlib.redirect_stdout(output):
        exec(compile(source, test_path, "exec"), {"__name__": "__main__"})

    lines = output.getvalue().splitlines()
    result["passes"] = sum(line.startswith("PASS:") for line in lines)
    finished = any(line.startswith("ALL ") and line.endswith("PASSED") for line in lines)

    if not result["passes"] or not finished:
        raise RuntimeError("The test ended without its 'ALL ... PASSED' line")

    result["status"] = "passed"


def main():
    arguments = sys.argv[sys.argv.index("--") + 1:]
    test_path = arguments[0]
    result_path = os.environ["ADDON_RESULT"]
    result = {"test": os.path.basename(test_path), "status": "failed", "passes": 0, "error": ""}

    try:
        run_test(test_path, os.environ["ADDON_MODULE"], os.environ["ADDON_ROOT"], result)
    except BaseException:
        result["status"] = "failed"
        result["error"] = traceback.format_exc()[-3000:]
        traceback.print_exc()
    finally:
        with open(result_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle)

        if not bpy.app.background:
            bpy.ops.wm.quit_blender()


if bpy.app.background:
    main()
else:
    # Let the window finish opening first.
    bpy.app.timers.register(main, first_interval=1.0)

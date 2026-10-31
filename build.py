#!/usr/bin/env python3
# Shared kit file (blender-addon-kit). Do not edit here: change it in the kit
# and run sync_kit.py.
"""Test a Blender add-on, then build its release zip.

    python build.py                 plain tests, Blender tests, then dist/<id>-<version>.zip
    python build.py --tests-only    run the tests, build nothing
    python build.py --skip-blender  plain tests only (the zip is still built, with a warning)
    python build.py --headless      Blender without a window (tests marked "# needs-window" are skipped)
    python build.py --blender PATH  the Blender executable
    python build.py --repo          (extensions) also write dist/repo/ with an index.json for hosting

Layout it expects: the repo root holds build.py, tests/ and exactly ONE add-on
folder, either
  * a legacy add-on: <id>/__init__.py with a bl_info dict, or
  * an extension:    <id>/blender_manifest.toml.

The Blender tests run against the BUILT zip, installed into a temporary
Blender settings folder (BLENDER_USER_* variables), so your real Blender
config and add-ons are never read or changed. Nothing is built into dist/
unless every test passes.
"""
import argparse
import ast
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

ROOT = Path(__file__).resolve().parent
TESTS = ROOT / "tests"
DIST = ROOT / "dist"

ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
NOT_ADDONS = {"tests", "dist", "build", "docs", "node_modules", "venv", "env"}
SKIP_DIRS = {"__pycache__", ".git", ".ruff_cache", ".pytest_cache", ".mypy_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
SKIP_NAMES = {".DS_Store", "Thumbs.db"}
BLENDER_FALLBACKS = ("/usr/bin/blender", "/usr/local/bin/blender", "/opt/blender/blender")
ZIP_TIME = (1980, 1, 1, 0, 0, 0)  # fixed, so the same files give the same zip


def step(title):
    print(f"\n=== {title} ===")


def fail(message):
    print(f"\nBUILD STOPPED: {message}")
    sys.exit(1)


# ------------------------------------------------------------ the add-on
class Addon:
    def __init__(self, path, kind, addon_id, version):
        self.path = path
        self.kind = kind          # "legacy" or "extension"
        self.id = addon_id
        self.version = version

    @property
    def module(self):
        """The name Blender imports the add-on under."""
        if self.kind == "extension":
            return f"bl_ext.user_default.{self.id}"
        return self.id

    @property
    def zip_name(self):
        return f"{self.id}-{self.version}.zip"


def has_bl_info(init_file):
    try:
        tree = ast.parse(init_file.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False

    return any(
        isinstance(node, ast.Assign)
        and any(getattr(target, "id", "") == "bl_info" for target in node.targets)
        for node in tree.body
    )


def read_manifest(path):
    if tomllib is None:
        fail("Python 3.11 or newer is needed to read blender_manifest.toml")

    with open(path, "rb") as handle:
        return tomllib.load(handle)


def find_addon(root=ROOT):
    """Return the one add-on folder in `root`, or stop with a clear message."""
    found = []

    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name in NOT_ADDONS:
            continue

        if (child / "blender_manifest.toml").is_file():
            found.append((child, "extension"))
        elif (child / "__init__.py").is_file() and has_bl_info(child / "__init__.py"):
            found.append((child, "legacy"))

    if not found:
        fail("no add-on folder found (needs <id>/__init__.py with bl_info, or <id>/blender_manifest.toml)")

    if len(found) > 1:
        fail("more than one add-on folder: " + ", ".join(path.name for path, _ in found))

    path, kind = found[0]

    if kind == "extension":
        manifest = read_manifest(path / "blender_manifest.toml")
        addon_id = manifest.get("id", "")
        version = manifest.get("version", "")
    else:
        addon_id = path.name
        tree = ast.parse((path / "__init__.py").read_text(encoding="utf-8"))
        version = ""

        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "bl_info" for t in node.targets):
                try:
                    info = ast.literal_eval(node.value)
                    version = ".".join(str(part) for part in info["version"])
                except (ValueError, KeyError, TypeError):
                    fail("bl_info must be a plain dict with a 'version' tuple")

    if not ID_PATTERN.match(str(addon_id)):
        fail(
            f"'{addon_id}' is not a valid add-on id. Use lowercase letters, digits and "
            "underscores, starting with a letter (no hyphens or spaces)"
        )

    if not re.match(r"^\d+\.\d+\.\d+$", str(version)):
        fail(f"version '{version}' must look like 1.2.3")

    return Addon(path, kind, addon_id, version)


# --------------------------------------------------------------- the zip
def exclude_patterns(addon):
    """Extra exclude patterns from an extension manifest's [build] table."""
    if addon.kind != "extension":
        return []

    build = read_manifest(addon.path / "blender_manifest.toml").get("build", {})
    return [pattern.strip("/") for pattern in build.get("paths_exclude_pattern", [])]


def is_excluded(relative, patterns):
    parts = relative.split("/")

    if any(part in SKIP_DIRS for part in parts[:-1]) or parts[-1] in SKIP_NAMES:
        return True

    if Path(parts[-1]).suffix in SKIP_SUFFIXES:
        return True

    # Simplified matching: a pattern may name a file, a folder, or a glob.
    return any(
        fnmatch.fnmatch(relative, pattern) or any(fnmatch.fnmatch(part, pattern) for part in parts)
        for pattern in patterns
    )


def collect_files(addon):
    """List (source path, path inside the zip) for everything that ships."""
    patterns = exclude_patterns(addon)
    prefix = f"{addon.id}/" if addon.kind == "legacy" else ""
    files = []

    for path in sorted(addon.path.rglob("*")):
        if path.is_file():
            relative = path.relative_to(addon.path).as_posix()

            if not is_excluded(relative, patterns):
                files.append((path, prefix + relative))

    # The license travels with the add-on (the GPL expects it).
    top_level = [name.removeprefix(prefix) for _, name in files if "/" not in name.removeprefix(prefix)]
    has_license = any(name.upper().startswith("LICENSE") for name in top_level)
    licenses = sorted(ROOT.glob("LICENSE*"))

    if licenses and not has_license:
        files.append((licenses[0], prefix + licenses[0].name))
    elif not licenses and not has_license:
        print("WARNING: no LICENSE file in the repo, so none is shipped")

    return files


def build_zip(addon, destination):
    files = collect_files(addon)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)

    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, name in files:
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())

    names = {name for _, name in files}
    required = "blender_manifest.toml" if addon.kind == "extension" else f"{addon.id}/__init__.py"

    if required not in names or any(name.split("/")[0] == "tests" for name in names):
        fail("the zip does not look right")

    return len(files)


# ------------------------------------------------------------ plain tests
def run_plain_tests():
    step("Plain Python tests")
    runner = TESTS / "plain" / "_run_all.py"

    if not runner.exists():
        print("No tests/plain/_run_all.py, skipping")
        return

    if subprocess.run([sys.executable, str(runner)]).returncode:
        fail("plain tests failed")


# ----------------------------------------------------------- Blender tests
def find_blender(given):
    for candidate in (given, os.environ.get("BLENDER"), shutil.which("blender"), *BLENDER_FALLBACKS):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate

    return None


def unpack_legacy(zip_path, destination):
    """Extract a legacy zip the way Blender's installer does."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()

    with zipfile.ZipFile(zip_path) as archive:
        for name in archive.namelist():
            target = (destination / name).resolve()

            if root not in target.parents and target != root:
                fail(f"unsafe path in zip: {name}")

        archive.extractall(destination)


def isolated_env(work):
    """Environment that points Blender at a temporary profile inside `work`."""
    env = os.environ.copy()

    # Blender ignores a BLENDER_USER_* path whose folder does not exist and
    # silently falls back to the REAL profile, so create every one of them.
    for folder in ("scripts", "config", "datafiles", "extensions"):
        (work / folder).mkdir(parents=True, exist_ok=True)

    env.update({
        "BLENDER_USER_SCRIPTS": str(work / "scripts"),
        "BLENDER_USER_CONFIG": str(work / "config"),
        "BLENDER_USER_DATAFILES": str(work / "datafiles"),
        "BLENDER_USER_EXTENSIONS": str(work / "extensions"),
    })
    return env


def prepare_blender(addon, zip_path, blender, work):
    """Install the built zip into a temporary Blender profile. Return (env, install dir)."""
    env = isolated_env(work)
    env.update({
        "ADDON_ID": addon.id,
        "ADDON_MODULE": addon.module,
    })

    if addon.kind == "legacy":
        unpack_legacy(zip_path, work / "scripts" / "addons")
        return env, work / "scripts" / "addons" / addon.id

    command = [blender, "--command", "extension", "install-file", "-r", "user_default", str(zip_path)]
    result = subprocess.run(command, env=env, capture_output=True, text=True)

    if result.returncode:
        print((result.stdout + result.stderr)[-2500:])
        fail("Blender could not install the built extension (see above)")

    return env, work / "extensions" / "user_default" / addon.id


def run_blender_tests(addon, zip_path, blender, headless, timeout):
    step("Blender tests" + (" (headless)" if headless else ""))
    tests = sorted(path for path in (TESTS / "blender").glob("*.py") if not path.name.startswith("_"))
    runner = TESTS / "blender" / "_run_in_blender.py"

    if not tests:
        print("No Blender tests, skipping")
        return

    if not runner.exists():
        fail("tests/blender/_run_in_blender.py is missing")

    work = Path(tempfile.mkdtemp(prefix=f"{addon.id}-blender-"))
    results = []

    try:
        env, install_dir = prepare_blender(addon, zip_path, blender, work)
        env["ADDON_ROOT"] = str(install_dir)

        for test in tests:
            result_file = work / f"{test.stem}.json"
            env["ADDON_RESULT"] = str(result_file)
            command = [blender]

            if headless:
                command.append("--background")

            command += ["--python", str(runner), "--", str(test)]
            print(f"\n--- {test.name}")
            started = time.time()

            try:
                process = subprocess.run(command, env=env, timeout=timeout,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                log, code = process.stdout, process.returncode
            except subprocess.TimeoutExpired as error:
                log = error.stdout or ""
                log = log.decode(errors="replace") if isinstance(log, bytes) else log
                print(log[-1500:])
                results.append((test.name, "failed", 0, f"Timed out after {timeout} s"))
                continue

            if result_file.exists():
                data = json.loads(result_file.read_text(encoding="utf-8"))
                status, passes, error = data["status"], data["passes"], data["error"]
            else:
                status, passes, error = "failed", 0, f"Blender ended without a result (exit code {code})"

            if status != "passed":
                print(log[-2500:])
            else:
                # A passing test can still say something: lines it prints as "INFO: ..."
                for line in log.splitlines():
                    if line.startswith("INFO:"):
                        print(f"    {line}")

            results.append((test.name, status, passes, error))
            print(f"    {status.upper()} in {time.time() - started:.1f} s")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()

    for name, status, passes, error in results:
        lines = error.strip().splitlines()
        detail = f"{passes} checks" if status == "passed" else (lines[-1] if lines else "")
        print(f"{status:8} {name}  {detail}")

    if any(status == "failed" for _, status, _, _ in results):
        fail("Blender tests failed")

    skipped = [name for name, status, _, _ in results if status == "skipped"]

    if skipped:
        print(f"\nWARNING: not run (need a window): {', '.join(skipped)}")


# ---------------------------------------------------------------- publish
def git_dirty():
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
        return bool(out.stdout.strip()) if out.returncode == 0 else False
    except FileNotFoundError:
        return False


def write_repo(addon, zip_path, blender):
    """Write dist/repo/ for hosting as an extensions repository (e.g. GitHub Pages)."""
    step("Extension repository")

    if addon.kind != "extension":
        fail("--repo only applies to extensions")

    if blender is None:
        fail("Blender is needed to generate the repository index (--blender PATH)")

    repo = DIST / "repo"
    shutil.rmtree(repo, ignore_errors=True)
    repo.mkdir(parents=True)
    shutil.copy2(zip_path, repo / zip_path.name)
    command = [blender, "--command", "extension", "server-generate", "--repo-dir", str(repo)]
    work = Path(tempfile.mkdtemp(prefix=f"{addon.id}-repo-"))

    try:
        result = subprocess.run(command, env=isolated_env(work), capture_output=True, text=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if result.returncode or not (repo / "index.json").exists():
        print((result.stdout + result.stderr)[-2500:])
        fail("could not generate the repository (check: blender --command extension server-generate --help)")

    print(f"Wrote {repo.relative_to(ROOT)}/ (host this folder; users add its index.json URL in Blender)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tests-only", action="store_true", help="run the tests, build nothing")
    parser.add_argument("--skip-blender", action="store_true", help="skip the Blender tests")
    parser.add_argument("--headless", action="store_true", help='no Blender window ("# needs-window" tests are skipped)')
    parser.add_argument("--blender", help="path to the Blender executable")
    parser.add_argument("--repo", action="store_true", help="(extensions) also write dist/repo/ for hosting")
    parser.add_argument("--timeout", type=int, default=240, help="seconds allowed per Blender test")
    args = parser.parse_args()

    addon = find_addon()
    print(f"{addon.id} {addon.version} ({addon.kind})")
    run_plain_tests()
    stage = Path(tempfile.mkdtemp(prefix=f"{addon.id}-build-"))
    blender = find_blender(args.blender)

    try:
        zip_path = stage / addon.zip_name
        count = build_zip(addon, zip_path)

        if args.skip_blender:
            print("\nWARNING: Blender tests skipped")
        elif blender is None:
            fail("Blender not found. Use --blender PATH, set BLENDER, or --skip-blender")
        else:
            print(f"\nUsing {blender}")
            run_blender_tests(addon, zip_path, blender, args.headless, args.timeout)

        if args.tests_only:
            print("\nAll tests passed. Nothing built (--tests-only)")
            return

        step("Build")

        if git_dirty():
            print("WARNING: there are uncommitted changes, so this zip is not an exact commit")

        DIST.mkdir(exist_ok=True)
        target = DIST / addon.zip_name
        shutil.copy2(zip_path, target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        print(f"Built {target.relative_to(ROOT)}  ({target.stat().st_size / 1024:.0f} KB, {count} files)")
        print(f"sha256 {digest}")

        if args.repo:
            write_repo(addon, target, blender)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


if __name__ == "__main__":
    main()

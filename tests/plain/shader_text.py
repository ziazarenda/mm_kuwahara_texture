"""The shader code must stay the same as the Material Maker port.

The hashes below were taken from the version 0.5.0 code, where the GLSL lived
in Python strings, before it was moved into shaders/*.glsl. They pin:

  * each shader file,
  * the complete fragment source built for every stage,
  * the vertex source.

Layout does not matter: indentation, blank lines, trailing spaces and tabs
versus spaces are ignored, so an editor may format the .glsl files freely.
Anything else counts, including comments and every token of code.

If you change a shader ON PURPOSE, update the hashes here in the same commit
(run this test to see the new values).
"""
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fake_blender import load_addon  # noqa: E402

PART_HASHES = {
    "TENSOR_GLSL": "205c667814d943aff746d633f20c2956b05dacc45095e56902b7722e18f66ae9",
    "KUWAHARA_GLSL": "2b9b4ba318d75b9b373aa19cc0ba810c39c991bff8bf17eff17911d5838c0af5",
    "SAMPLING_GLSL": "19b2bf4159e716c863dbafac827ea0e5f881a084a7cf2241576b697d60ea1793",
    "BLUR_GLSL": "ac99a7c7f8d9379bcc14b7a3aaf3b4365302d7b9107cb6d27014079221b4af13",
}
STAGE_HASHES = {
    "tensor": "24ac6d115304b1a44577e37d894b22b8dbced0da74066c7334031850a9ce2dd2",
    "blur": "2e6042c9b26438394422e37ed32685bc35012e13141bc83a88a1445d4d887bd8",
    "filter": "6a840985ab3d476f7994c1a05ef3a0e1fecf8a7717089577d664a3b89af21833",
    "export": "4e326bba6e1b899d9aa04908e14dd88bb623ff1394c0b1e37faa6b3dc58ba21a",
}
# Vertex source is built in Python, not from a file, so it is pinned exactly.
VERTEX_HASH = "90bab800bd6f30c0340d7097d7f1b27440d1b2c5c43ddcbad19a4e47166106ef"
FILES = {"TENSOR_GLSL": "tensor.glsl", "KUWAHARA_GLSL": "kuwahara.glsl",
         "SAMPLING_GLSL": "sampling.glsl", "BLUR_GLSL": "blur.glsl"}


def normalize(text):
    """Strip layout: per line, trim and collapse whitespace; drop blank lines.

    Line breaks are kept (a // comment ends at its line), so only layout is
    ignored, never code.
    """
    lines = [re.sub(r"\s+", " ", line.strip()) for line in text.replace("\r\n", "\n").split("\n")]
    return "\n".join(line for line in lines if line)


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- the normalizer itself: layout is ignored, code is not ------------------
sample = "vec2 d = 1.0/vec2(resolution);\n\tfloat a = 0.58; // note\n"
reformatted = "\n  vec2   d =   1.0/vec2(resolution);   \n\n        float a = 0.58;   // note\n\n\n"
assert normalize(sample) == normalize(reformatted), "layout changes must not matter"
assert normalize(sample) != normalize(sample.replace("0.58", "0.59")), "a changed number must matter"
assert normalize(sample) != normalize(sample.replace("// note", "// other")), "a changed comment must matter"
assert normalize(sample) != normalize(sample.replace("1.0/", "1.0 /vec2(2.0)/")), "an added token must matter"
assert normalize("a;\n// c\nb;") != normalize("a; // c\nb;"), "moving code into a comment must matter"
print("ok: layout is ignored, code and comments are not")

addon, _ = load_addon(ROOT / "mm_kuwahara_texture" / "__init__.py")

changed = [FILES[name] for name, expected in PART_HASHES.items() if sha(normalize(getattr(addon, name))) != expected]
assert not changed, (
    "shader code differs from the 0.5.0 text in: " + ", ".join(changed) +
    ". This is a real change (layout is ignored). If it is intended, update the hashes in this test."
)
print("ok: the four shader files hold the 0.5.0 code")

for stage, expected in STAGE_HASHES.items():
    info = addon.make_shader(stage)
    assert sha(normalize(info.fragment)) == expected, f"fragment source for '{stage}' differs from the 0.5.0 code"
    assert sha(info.vertex) == VERTEX_HASH, "vertex source differs"
    print("ok: fragment source of", stage, "is unchanged")

# Windows line endings in a checkout must load the same text.
shader = ROOT / "mm_kuwahara_texture" / "shaders" / "blur.glsl"
original = shader.read_bytes()
shader.write_bytes(original.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
try:
    assert "\r" not in addon.load_glsl("blur.glsl"), "CRLF is not normalized"
finally:
    shader.write_bytes(original)
print("ok: CRLF checkouts load without carriage returns")

# Every shader file is used, and nothing else sits in the folder.
used = set(FILES.values())
present = {path.name for path in (ROOT / "mm_kuwahara_texture" / "shaders").iterdir()}
assert present == used, f"unexpected shader files: {present ^ used}"
print("ok: shaders folder holds exactly the four shader files")

# One version number: ADDON_VERSION is derived from bl_info.
assert addon.ADDON_VERSION == ".".join(str(part) for part in addon.bl_info["version"])
assert not (ROOT / "mm_kuwahara_texture" / "blender_manifest.toml").exists(), "this is a legacy add-on: no manifest"
print("ok: ADDON_VERSION comes from bl_info")

print("\nALL SHADER TEXT TESTS PASSED")

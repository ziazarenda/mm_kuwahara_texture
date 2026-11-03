"""Check which area each pass has to cover for a preview (no Blender needed).

The preview filters only the middle of the image. This test pins that every pass
covers what the next one reads, so the preview shows the same pixels as a full run.
"""
from pathlib import Path

source = (Path(__file__).resolve().parents[2] / "mm_kuwahara_texture" / "engine.py").read_text(encoding="utf-8")
start = source.index("BLUR_REACH = ")
end = len(source)
namespace = {}
exec(source[start:end], namespace)
preview_plan = namespace["preview_plan"]
reach = namespace["BLUR_REACH"]
checks = 0


def inside(inner, outer):
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    return ox <= ix and oy <= iy and ix + iw <= ox + ow and iy + ih <= oy + oh


def check(condition, message):
    global checks
    checks += 1
    assert condition, message


for res in (128, 200, 256, 300, 360, 512, 1024, 2048, 4096):
    for size in (128, 256, 512):
        crop, (tensor, blur_x, blur_y, filtered) = preview_plan(res, size)
        label = f"res={res} size={size}"
        frame = (0, 0, res, res)

        check(inside(crop, frame), f"{label}: crop leaves the frame")
        check(crop[2] == crop[3] == min(size, res), f"{label}: crop size")

        for region in (tensor, blur_x, blur_y, filtered):
            check(inside(region, frame), f"{label}: region leaves the frame")

        if tensor == frame:  # whole frame: sampling that wraps around stays inside it
            check(blur_x == blur_y == filtered == frame, f"{label}: whole-frame plan")
            continue

        x, y, w, h = crop
        check(inside(crop, filtered) and inside(crop, blur_y), f"{label}: crop not covered")
        check(inside((x, y - reach, w, h + 2 * reach), blur_x), f"{label}: blur X too small")
        check(inside((blur_x[0] - reach, blur_x[1], blur_x[2] + 2 * reach, blur_x[3]), tensor),
              f"{label}: tensor too small")

# A big image really does get a small amount of work.
crop, regions = preview_plan(4096, 256)
work = sum(r[2] * r[3] for r in regions)
check(work < 4 * 4096 * 4096 / 50, "a preview at 4096 should be a small fraction of a full run")

print(f"{checks} checks")

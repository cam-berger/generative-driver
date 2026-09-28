"""Mapping-consistency check for a raw Cortex-M image, as a logged tool.

A thin wrapper over tools/workspace/image_map_check.py, which stays standard-library-only so the same file can be
copied beside an image into an offline worker. The verdict is heuristic: a decisive best mapping is a
ranked hypothesis to confirm by resolving one known literal pointer, not a proof of the load map.
"""
import importlib.util
import json
import os

from . import core

CHECKER = os.path.join(core.BENCH, "tools", "workspace", "image_map_check.py")


def _checker():
    spec = importlib.util.spec_from_file_location("image_map_check", CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ints(values):
    """Accept one value or a list, as ints or '0x..' strings."""
    if values is None:
        return []
    if isinstance(values, (int, str)):
        values = [values]
    return [int(v, 0) if isinstance(v, str) else int(v) for v in values]


@core.tool('image_map_check')
def image_map_check(run_dir, image, base=None, header_offsets=None, resolve=None, max_words=200000, **_):
    """Score (header offset, load base) hypotheses for a raw little-endian Cortex-M image and rank them by
    how many absolute literal pointers resolve to ASCII strings. `base` fixes the load base instead of
    deriving it from the reset vector; `header_offsets` adds hypotheses to the default list; `resolve` lists
    load addresses to resolve under every mapping. Exit 0 when one mapping is decisively best, 1 otherwise.
    The full report is written under the run directory."""
    run = core.resolve_run(run_dir)
    path = image if os.path.isabs(image) else os.path.join(core.BENCH, image)
    if not os.path.isfile(path):
        return {"ok": False, "reason": f"no such image: {path}", "_exit": 1}
    with open(path, "rb") as fh:
        data = fh.read()
    report = _checker().check_image(data, bases=_ints(base), headers=_ints(header_offsets),
                                    resolve=_ints(resolve), max_words=int(max_words))
    report["image"] = path
    out_dir = os.path.join(run, "imagemap")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"check-{report['image_sha256'][:16]}.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
        fh.write("\n")
    report["report_path"] = out
    report["ok"] = report["decisive"]
    report["_files"] = {path: report["image_sha256"], out: core.sha256(out)}
    report["_exit"] = 0 if report["decisive"] else 1
    return report

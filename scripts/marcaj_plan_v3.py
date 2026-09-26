"""Correction plan for the Marcaj project: what is in Marcaj now (v1 pre-annotations) vs the v3 detector, per tile.

Per tile, from the row axes of both versions (direction = length-weighted mean angle, mod 180):
  goleste   v1 has rows, v3 none, no vineyard on the image (model: 0 canopies, or rows not on vegetation)
  verifica  v1 has rows, v3 none, but a vineyard may be in a corner / very young vines: check, keep only real rows
  refa      both have rows, directions differ by > 10 deg: the rows in Marcaj run across the real rows
  a_doua    v3 finds a second vineyard with another direction on the tile: add its rows
  curata    same direction, but Marcaj has many more rows (over fields / trees / houses): delete the extra ones
  adauga    v3 has rows, v1 none: vineyard missed in Marcaj, draw its rows
  ok        nothing to do for rows (canopies / waste still to check)
Waste actions (deseu / deseu_top) are kept from the earlier plan (web/data/marcaj_tasks.json).
Outputs: out/marcaj_plan_v3.json, out/marcaj_plan_v3.md, web/data/marcaj_tasks.json (Corectură tab), web/data/review_v3.json
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.cvat_io import read_cvat  # noqa: E402

CORNER = {"r032_c019", "r034_c025", "r033_c021", "r030_c019", "r034_c021", "r020_c010", "r018_c010", "r036_c022",
          "r025_c016", "r025_c017", "r030_c018", "r035_c024", "r027_c019", "r036_c027", "r036_c023", "r012_c007"}   # checked visually: vines in a corner / very young


def dirs(objs):
    """{group: (angle deg, total length px, rows)} from the row polylines (group = tile-local / global vineyard id)."""
    acc = defaultdict(lambda: [0.0, 0.0, 0.0, 0])
    for o in objs:
        if o["label"] != "row":
            continue
        (x0, y0), (x1, y1) = o["points"][0], o["points"][-1]
        L = math.hypot(x1 - x0, y1 - y0)
        a = 2 * math.atan2(y1 - y0, x1 - x0)
        g = acc[o["attrs"].get("vineyard_id", "")]
        g[0] += L * math.cos(a); g[1] += L * math.sin(a); g[2] += L; g[3] += 1
    return {k: (math.degrees(math.atan2(s, c) / 2) % 180, L, n) for k, (c, s, L, n) in acc.items()}


def adiff(a, b):
    d = abs(a - b) % 180
    return min(d, 180 - d)


def main():
    v1 = read_cvat(C.OUT / "pre_global.xml")
    v3 = read_cvat(C.OUT / "pre_global_v3.xml")
    local = read_cvat(C.OUT / "baseline_all_v3v.xml")         # tile-local ids: a second direction has the "b" suffix
    task = json.loads((C.OUT / "marcaj_tile_task.json").read_text())
    emptied = set(json.loads((C.OUT / "emptied_by_model.json").read_text()))
    sup1 = {r["tile"]: r["support"] for r in json.loads((C.OUT / "row_support.json").read_text())}
    old = json.loads((C.ROOT / "web" / "data" / "marcaj_tasks.json").read_text())
    waste = {f["tile"]: [a for a in f["actions"] if a.startswith("deseu")] for t in old["tasks"] for f in t["frames"]}
    plan, review = {}, []
    for tile in sorted(task):
        n1 = sum(o["label"] == "row" for o in v1.get(tile, []))
        n3 = sum(o["label"] == "row" for o in v3.get(tile, []))
        d1 = max(dirs(v1.get(tile, [])).values(), key=lambda x: x[1], default=None)
        dl = dirs(local.get(tile, []))
        main3 = max((v for k, v in dl.items() if not k.endswith("b")), key=lambda x: x[1], default=None)
        second = [v for k, v in dl.items() if k.endswith("b")]
        short = tile[7:16]
        if n1 and not n3:
            act = "verifica" if short in CORNER else "goleste"
        elif n3 and not n1:
            act = "adauga"
        elif n1 and n3:
            if d1 and main3 and adiff(d1[0], main3[0]) > 10 and not any(adiff(d1[0], s[0]) <= 10 for s in second):
                act = "refa"
            elif second and d1 and all(adiff(d1[0], s[0]) > 10 for s in second):
                act = "a_doua"
            elif n1 >= 1.5 * n3 and n1 - n3 >= 5:
                act = "curata"
            else:
                act = "ok"
        else:
            act = "ok"
        acts = ([] if act == "ok" else [act]) + waste.get(tile, [])
        tk, fr = task[tile]
        plan[tile] = {"task": tk, "frame": fr, "action": act, "rows_marcaj": n1, "rows_v3": n3,
                      "dir_marcaj": round(d1[0]) if d1 else None, "dir_v3": round(main3[0]) if main3 else None,
                      "second_dir_v3": [round(s[0]) for s in second], "support_marcaj": sup1.get(tile), "model_zero": tile in emptied,
                      "objects_marcaj": len(v1.get(tile, [])), "waste": waste.get(tile, [])}
        if act != "ok":
            k = 512 / 2048
            rows = lambda d: [[[round(x * k, 1), round(y * k, 1)] for x, y in o["points"]] for o in d.get(tile, []) if o["label"] == "row"]
            review.append({"tile": tile, **plan[tile], "v1": rows(v1), "v3": rows(v3)})
    out = C.OUT / "marcaj_plan_v3.json"
    out.write_text(json.dumps(plan, indent=0))
    count = defaultdict(int)
    for p in plan.values():
        count[p["action"]] += 1
    # Corectură tab: same format as before, v3 actions + the waste checks
    tasks = defaultdict(list)
    for tile, p in plan.items():
        acts = ([] if p["action"] == "ok" else [p["action"]]) + p["waste"]
        if acts:
            tasks[p["task"]].append({"frame": p["frame"], "tile": tile, "actions": acts, "objects": p["objects_marcaj"],
                                     "rows_marcaj": p["rows_marcaj"], "rows_v3": p["rows_v3"]})
    web = {"project": 38, "version": "v3", "about": "Plan de corectură Marcaj (v1 din Marcaj vs detectorul v3): golește tile-urile fără vie, "
           "refă rândurile trase în altă direcție decât via, adaugă via ratată sau a doua vie de pe tile, șterge rândurile în plus; "
           "plus deșeurile de verificat.", "counts": dict(count),
           "tasks": [{"task": t, "frames": sorted(tasks.get(t, []), key=lambda f: f["frame"])} for t in range(134, 197)]}
    (C.ROOT / "web" / "data" / "marcaj_tasks.json").write_text(json.dumps(web, ensure_ascii=False))
    (C.ROOT / "web" / "data" / "review_v3.json").write_text(json.dumps({"counts": dict(count), "tiles": review}, separators=(",", ":")))
    L = ["# Plan de corectură Marcaj — v1 (în Marcaj) vs v3", "", f"Tile-uri: {dict(count)}", ""]
    labels = {"goleste": "golește tot (fără vie)", "verifica": "verifică: vie doar în colț / foarte tânără",
              "refa": "refă rândurile (direcție greșită)", "a_doua": "adaugă a doua vie (altă direcție)",
              "curata": "șterge rândurile în plus", "adauga": "adaugă rândurile (vie ratată)"}
    for t in range(134, 197):
        fs = sorted((p for p in plan.values() if p["task"] == t), key=lambda p: p["frame"])
        work = [p for p in fs if p["action"] != "ok" or p["waste"]]
        L.append(f"## Task #{t}" + ("" if work else " — doar Submit"))
        for p in work:
            tile = next(k for k, v in plan.items() if v is p)
            what = [labels[p["action"]]] if p["action"] in labels else []
            what += ["deșeu probabil" if w == "deseu_top" else "posibil deșeu" for w in p["waste"]]
            L.append(f"- frame {p['frame']} · {tile[7:16]} · " + "; ".join(what) + f" (rânduri acum {p['rows_marcaj']}, v3 {p['rows_v3']})")
        L.append("")
    (C.OUT / "marcaj_plan_v3.md").write_text("\n".join(L))
    print(dict(count), "->", out, "and web/data/marcaj_tasks.json, review_v3.json")


if __name__ == "__main__":
    main()

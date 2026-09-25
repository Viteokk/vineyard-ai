"""Read / write CVAT for images 1.1 (the Marcaj import/export format).

Object model (pixel coordinates of one tile):
  {"label": "vineyard"|"interrow_area", "type": "polygon",  "points": [[x,y],...], "attrs": {...}}
  {"label": "row",                      "type": "polyline", "points": [[x,y],...], "attrs": {...}}
  {"label": "waste",                    "type": "box",      "xtl","ytl","xbr","ybr",  "attrs": {...}}
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

# Exact label block copied from the organisers' example (05_examples) — do not edit.
META = """<meta><task><name>{name}</name><labels>
<label><name>vineyard</name><type>polygon</type><attributes>
  <attribute><name>vineyard_id</name><mutable>False</mutable><input_type>text</input_type><default_value></default_value><values></values></attribute></attributes></label>
<label><name>waste</name><type>rectangle</type><attributes>
  <attribute><name>vineyard_id</name><mutable>False</mutable><input_type>text</input_type><default_value></default_value><values></values></attribute></attributes></label>
<label><name>row</name><type>polyline</type><attributes>
  <attribute><name>vineyard_id</name><mutable>False</mutable><input_type>text</input_type><default_value></default_value><values></values></attribute>
  <attribute><name>row_id</name><mutable>False</mutable><input_type>text</input_type><default_value></default_value><values></values></attribute>
  <attribute><name>row_structure</name><mutable>False</mutable><input_type>select</input_type><default_value>regular</default_value><values>regular
disrupted
unassessable</values></attribute></attributes></label>
<label><name>interrow_area</name><type>polygon</type><attributes>
  <attribute><name>vineyard_id</name><mutable>False</mutable><input_type>text</input_type><default_value></default_value><values></values></attribute>
  <attribute><name>interrow_cover</name><mutable>False</mutable><input_type>select</input_type><default_value>bare_soil</default_value><values>bare_soil
vegetation
mixed
unassessable</values></attribute></attributes></label>
</labels></task></meta>"""


def _pts(s: str) -> list[list[float]]:
    return [[float(v) for v in p.split(",")] for p in s.split(";") if p]


def read_cvat(xml_path: Path | str) -> dict[str, list[dict]]:
    """Return {image_name: [objects]}."""
    root = ET.parse(xml_path).getroot()
    out: dict[str, list[dict]] = {}
    for im in root.iter("image"):
        objs = []
        for el in im:
            attrs = {a.get("name"): (a.text or "") for a in el.findall("attribute")}
            if el.tag in ("polygon", "polyline"):
                objs.append({"label": el.get("label"), "type": el.tag,
                             "points": _pts(el.get("points")), "attrs": attrs})
            elif el.tag == "box":
                objs.append({"label": el.get("label"), "type": "box",
                             **{k: float(el.get(k)) for k in ("xtl", "ytl", "xbr", "ybr")},
                             "attrs": attrs})
        out[im.get("name")] = objs
    return out


def _fmt(points) -> str:
    return ";".join(f"{x:.1f},{y:.1f}" for x, y in points)


def _attrs(attrs: dict) -> str:
    return "".join(f'<attribute name="{k}">{escape(str(v))}</attribute>' for k, v in attrs.items())


def write_cvat(images: dict[str, list[dict]], xml_path: Path | str,
               name: str = "vineyard-ai pre-annotations", size: int = 2048) -> None:
    """images: {tile_name: [objects]} — every tile must be present (empty list allowed)."""
    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<annotations>", "<version>1.1</version>",
             META.format(name=escape(name))]
    for i, (img, objs) in enumerate(sorted(images.items())):
        lines.append(f'<image id="{i}" name="{img}" width="{size}" height="{size}">')
        for o in objs:
            a = _attrs(o.get("attrs", {}))
            if o["type"] in ("polygon", "polyline"):
                lines.append(f'<{o["type"]} label="{o["label"]}" source="auto" occluded="0" '
                             f'points="{_fmt(o["points"])}" z_order="0">{a}</{o["type"]}>')
            elif o["type"] == "box":
                lines.append(f'<box label="{o["label"]}" source="auto" occluded="0" '
                             f'xtl="{o["xtl"]:.1f}" ytl="{o["ytl"]:.1f}" xbr="{o["xbr"]:.1f}" '
                             f'ybr="{o["ybr"]:.1f}" z_order="0">{a}</box>')
        lines.append("</image>")
    lines.append("</annotations>")
    Path(xml_path).write_text("\n".join(lines), encoding="utf-8")

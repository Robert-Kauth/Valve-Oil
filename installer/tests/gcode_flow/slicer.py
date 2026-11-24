"""The slicer packs' templates expanded as OrcaSlicer and QIDI Studio expand them, and prints built around them.

Both slicers' template language as these packs use it: `{if expr}`, `{elsif expr}`, `{else}`, and `{endif}` on lines
of their own, `{expr}` and `[name]` substituted inline. Expressions are arithmetic, comparisons, `and`, `or`, `not`
(`!`), and indexing into per-filament vectors. A name the context doesn't have fails the expansion, as the slicer
refuses to slice.
"""
from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass

from installer.tests.helpers import REPO_ROOT

PACKS = {"orca": REPO_ROOT / "orcaslicer_gcode", "qidistudio": REPO_ROOT / "qidistudio_gcode"}
CONTROL = re.compile(r"^\s*\{(if|elsif|else|endif)\b\s*(.*?)\}\s*$")
INLINE = re.compile(r"\{([^{}]+)\}|\[([A-Za-z_][A-Za-z0-9_]*)\]")

_BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
           ast.Mod: operator.mod}
_COMPARE = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
            ast.Gt: operator.gt, ast.GtE: operator.ge}


class TemplateError(Exception):
    pass


def evaluate(expression: str, context: dict):
    source = re.sub(r"!(?!=)", " not ", expression).replace("&&", " and ").replace("||", " or ")
    tree = ast.parse(source.strip(), mode="eval")

    def value(node):
        if isinstance(node, ast.Expression):
            return value(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in ("true", "false"):
                return node.id == "true"
            if node.id not in context:
                raise TemplateError(f"unknown placeholder {node.id!r} in {{{expression}}}")
            return context[node.id]
        if isinstance(node, ast.Subscript):
            return value(node.value)[int(value(node.slice))]
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            return _BINARY[type(node.op)](value(node.left), value(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -value(node.operand)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not value(node.operand)
        if isinstance(node, ast.BoolOp):
            values = [value(v) for v in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("min", "max") \
                and not node.keywords:
            return {"min": min, "max": max}[node.func.id](*(value(arg) for arg in node.args))
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _COMPARE:
            return _COMPARE[type(node.ops[0])](value(node.left), value(node.comparators[0]))
        raise TemplateError(f"unsupported expression {{{expression}}}")

    return value(tree)


def _text(result) -> str:
    if isinstance(result, bool):
        return "true" if result else "false"
    if isinstance(result, float):
        text = f"{result:.3f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(result)


def _inline(match, context):
    result = evaluate(match.group(1) or match.group(2), context)
    if match.group(2) and isinstance(result, list):
        # [vector] is the current extruder's value.
        result = result[context.get("current_extruder", context.get("initial_tool", 0))]
    return result


def expand(template: str, context: dict) -> str:
    out: list[str] = []
    # Each open {if}: (this branch is taken, some branch was taken, the enclosing block is taken).
    stack: list[list[bool]] = []

    def active():
        return all(frame[0] for frame in stack)

    for line in template.splitlines():
        control = CONTROL.match(line)
        if control:
            word, expression = control.groups()
            if word == "if":
                taken = active() and bool(evaluate(expression, context))
                stack.append([taken, taken, active()])
            elif word == "elsif":
                frame = stack[-1]
                frame[0] = frame[2] and not frame[1] and bool(evaluate(expression, context))
                frame[1] = frame[1] or frame[0]
            elif word == "else":
                frame = stack[-1]
                frame[0] = frame[2] and not frame[1]
                frame[1] = True
            else:
                stack.pop()
            continue
        if not active():
            continue
        out.append(INLINE.sub(lambda m: _text(_inline(m, context)), line))
    if stack:
        raise TemplateError("unclosed {if}")
    return "\n".join(out)


def pack_file(slicer: str, printer_id: str, name: str) -> str:
    return (PACKS[slicer] / printer_id / name).read_text(encoding="utf-8")


@dataclass
class Filament:
    """A filament profile's values the packs read (OrcaSlicer's generic PLA unless set)."""

    temperature: int = 220
    range_high: int = 240
    e_feedrate: float = 1200.
    long_retraction_when_cut: bool = False
    retraction_distance_when_cut: float = 18.
    retract_length_toolchange: float = 2.


@dataclass
class Layer:
    z: float = 2.0
    number: int = 9
    max_print_height: float = 280.


def change_filament_context(filaments: list[Filament], current: int, following: int, layer: Layer,
                            flush=(40., 40., 40., 40.)) -> dict:
    return {
        "current_extruder": current, "next_extruder": following, "previous_extruder": current,
        "max_layer_z": layer.z, "layer_z": layer.z, "layer_num": layer.number,
        "max_print_height": layer.max_print_height,
        "old_filament_temp": filaments[current].temperature, "new_filament_temp": filaments[following].temperature,
        "nozzle_temperature_range_high": [f.range_high for f in filaments],
        "long_retractions_when_cut": [f.long_retraction_when_cut for f in filaments],
        "retraction_distances_when_cut": [f.retraction_distance_when_cut for f in filaments],
        "old_filament_e_feedrate": filaments[current].e_feedrate,
        "new_filament_e_feedrate": filaments[following].e_feedrate,
        "flush_length_1": flush[0], "flush_length_2": flush[1], "flush_length_3": flush[2],
        "flush_length_4": flush[3],
        "new_retract_length_toolchange": filaments[following].retract_length_toolchange,
        "retraction_length": .8, "timelapse_type": 0,
    }


@dataclass
class Job:
    text: str
    change_line: int
    change_lines: int
    resume_xy: tuple


def print_with_change(slicer: str, printer_id: str, *, filaments: list[Filament], current: int, following: int,
                      at=(150., 150.), layer: Layer = Layer(), cancelled: bool = False) -> Job:
    """A print as the slicer writes it around one filament change: a part's moves on the layer, the change inside
    that part's block, the travel back, and more moves. OrcaSlicer holds a part's EXCLUDE_OBJECT_END back until the
    next part's label starts, and writes it before a change only when printing one part at a time or from a wipe
    tower, so a layer-by-layer change without a wipe tower runs inside the label of the part printed just before it
    (OrcaSlicer src/libslic3r/GCode.cpp, read 2026-11). With
    `cancelled`, the first part is cancelled from Fluidd before the layer, after enough extrusion for Klipper's
    exclude_object to have learned the extruder's position."""
    x, y = at
    change = expand(pack_file(slicer, printer_id, "change_filament.gcode"),
                    change_filament_context(filaments, current, following, layer))
    before = [
        "EXCLUDE_OBJECT_DEFINE NAME=part_1 CENTER=150,150 POLYGON=[[140,140],[160,140],[160,160],[140,160]]",
        "EXCLUDE_OBJECT_DEFINE NAME=part_2 CENTER=150,150 POLYGON=[[140,140],[160,140],[160,160],[140,160]]",
        "EXCLUDE_OBJECT_START NAME=part_1",
        f"G1 X{x - 10:g} Y{y:g} F12000",
        f"G1 Z{layer.z:g} F600",
        f"G1 X{x:g} Y{y:g} E0.5 F3000",
        f"G1 X{x:g} Y{y + 5:g} E0.25 F3000",
        "G1 E-0.8 F2100",
    ]
    if cancelled:
        # Cancelled as its block starts; Klipper still lets its first five extrusions through while it learns the
        # extruder's position, so the part's own moves after those are the ones dropped.
        learning = [f"G1 X{x - 10 + i:g} Y{y - 5:g} E0.2 F3000" for i in range(5)]
        before = before[:2] + ["EXCLUDE_OBJECT NAME=part_1"] + before[2:3] + learning + before[3:]
    after = [
        "EXCLUDE_OBJECT_END NAME=part_1",
        "EXCLUDE_OBJECT_START NAME=part_2",
        f"G1 X{x:g} Y{y:g} F12000",
        f"G1 Z{layer.z:g} F600",
        "G1 E0.8 F2100",
        f"G1 X{x + 5:g} Y{y:g} E0.25 F3000",
        "EXCLUDE_OBJECT_END NAME=part_2",
    ]
    lines = before + change.splitlines() + after
    return Job("\n".join(lines), len(before), len(change.splitlines()), (x, y))


def print_with_pause(slicer: str, printer_id: str, *, at=(150., 150.), layer: Layer = Layer()) -> Job:
    """A print with the pack's pause G-code at a layer, as OrcaSlicer's Add pause and QIDI Studio's pause insert
    it between two parts' moves."""
    x, y = at
    pause = expand(pack_file(slicer, printer_id, "pause.gcode"), {"layer_num": layer.number, "layer_z": layer.z})
    before = [
        "EXCLUDE_OBJECT_DEFINE NAME=part_1 CENTER=150,150 POLYGON=[[140,140],[160,140],[160,160],[140,160]]",
        "EXCLUDE_OBJECT_START NAME=part_1",
        f"G1 X{x - 10:g} Y{y:g} F12000",
        f"G1 Z{layer.z:g} F600",
        f"G1 X{x:g} Y{y:g} E0.5 F3000",
        "G1 E-0.8 F2100",
        "EXCLUDE_OBJECT_END NAME=part_1",
    ]
    after = [
        "EXCLUDE_OBJECT_START NAME=part_1",
        f"G1 X{x:g} Y{y:g} F12000",
        f"G1 Z{layer.z:g} F600",
        "G1 E0.8 F2100",
        f"G1 X{x + 5:g} Y{y:g} E0.25 F3000",
        "EXCLUDE_OBJECT_END NAME=part_1",
    ]
    lines = before + pause.splitlines() + after
    return Job("\n".join(lines), len(before), len(pause.splitlines()), (x, y))


def start_context(filaments: list[Filament], tool: int, *, bed=60, chamber=0, area=((140., 140.), (160., 160.)),
                  bed_area=((0., 0.), (390., 390.)), layer_height=.2) -> dict:
    """What the packs' start G-code reads, for a print whose first layer covers `area` and starts on `tool`."""
    return {
        "initial_tool": tool, "initial_no_support_extruder": tool, "current_extruder": tool,
        "bed_temperature_initial_layer_single": bed, "chamber_temperature": [chamber] * len(filaments),
        "chamber_temperatures": [chamber] * len(filaments),
        "chamber_minimal_temperature": [chamber] * len(filaments),
        "nozzle_temperature_initial_layer": [f.temperature for f in filaments],
        "nozzle_temperature_range_high": [f.range_high for f in filaments],
        "filament_max_volumetric_speed": [12.] * len(filaments),
        "filament_diameter": [1.75] * len(filaments), "total_layer_count": 50,
        "first_layer_print_min": list(area[0]), "first_layer_print_max": list(area[1]),
        "print_bed_min": list(bed_area[0]), "print_bed_max": list(bed_area[1]),
        "initial_layer_print_height": layer_height,
    }


def print_from_start(slicer: str, printer_id: str, *, filaments: list[Filament], tool: int, **context) -> Job:
    """A whole print's beginning: the pack's start G-code, then the first part's first moves."""
    start = expand(pack_file(slicer, printer_id, "start.gcode"), start_context(filaments, tool, **context))
    after = [
        "EXCLUDE_OBJECT_DEFINE NAME=part_1 CENTER=150,150 POLYGON=[[140,140],[160,140],[160,160],[140,160]]",
        "EXCLUDE_OBJECT_START NAME=part_1",
        "G1 X140 Y150 F12000",
        "G1 Z.2 F600",
        "G1 X150 Y150 E0.5 F3000",
        "EXCLUDE_OBJECT_END NAME=part_1",
    ]
    lines = start.splitlines() + after
    return Job("\n".join(lines), 0, len(start.splitlines()), (150., 150.))

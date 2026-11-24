"""What every flow must hold on the printer, checked on a FlowPrinter after it ran."""
from __future__ import annotations

import math

from installer.tests.printer_geometry import REAR_LIMITS

# Commands the packs or QIDI's macros send that no Klipper on the printer registers, as far as is recorded, so Klipper
# answers "Unknown command" and goes on, as it does on the printer.
UNKNOWN_ON_THE_PRINTER = {
    # Both packs send UNLOAD_T[current_extruder]; 01.01.07.01 has no such command (qidi.py's command list).
    "UNLOAD_T": "the packs' UNLOAD_T[n]",
    # QIDI's CUT_FILAMENT_1 starts with _CG28, which QIDI never defined (the Max 4 cut macro's comment).
    "_CG28": "QIDI's cut macro",
    # QIDI's homing calls it on the Plus 5, which has no closed-loop drivers.
    "SET_MOTOR_TEMP_PROTECT": "closed-loop driver setting on a printer without them",
    # Stock PAUSE and RESUME_PRINT send it with the Box enabled; it isn't in the recorded 01.01.07.01 command list.
    "BUFFER_MONITORING": "stock pause and resume with the Box",
    # QIDI's power-loss resume (virtual_sdcard.py) sends it; the Smart 5's 01.01.02.01 macros don't define it.
    "CLEAR_NOZZLE_PLR": "QIDI's power-loss resume on the Smart 5",
}
# How far from the chute (QIDI's park position in _km_globals) filament may leave the nozzle outside the print, mm.
CHUTE_REACH = 5.


def chute(printer):
    km = printer.lookup_object("gcode_macro _km_globals").variables
    return km["park_x"], km["park_y"]


def at_chute(printer, xy) -> bool:
    x, y = chute(printer)
    return math.hypot(xy[0] - x, xy[1] - y) <= CHUTE_REACH


def assert_no_errors(test, printer, codes=()):
    """No errors, shutdown, or unknown commands; `codes` are QIDI error codes the flow is expected to report."""
    errors = [line for line in printer.responses.errors
              if not any(line.startswith("!! Code:%s" % (code,)) for code in codes)]
    test.assertEqual(errors, [])
    test.assertEqual(sorted(set(printer.vendor.codes)), sorted(set(codes)))
    test.assertFalse(printer.is_shutdown(), printer.shutdown_message)
    stats = printer.status("print_stats")
    test.assertNotEqual(stats["state"], "error", stats["message"])
    unknown = [line for line in printer.unknown_commands
               if not any(line.split()[0].startswith(name) for name in UNKNOWN_ON_THE_PRINTER)]
    test.assertEqual(unknown, [])


def assert_purges_only_at_the_chute(test, printer, first, last):
    """Filament that leaves the nozzle in moves[first:last] leaves it at the chute. A forward move first takes back
    what earlier retracts pulled in; only what it pushes past that leaves the nozzle."""
    debt = 0.
    for index, move in enumerate(printer.toolhead.moves):
        if move.extrude < 0:
            debt -= move.extrude
            continue
        out = max(0., move.extrude - debt)
        debt = max(0., debt - move.extrude)
        if first <= index < last and out > .01 and not at_chute(printer, move.end[:2]):
            x, y = chute(printer)
            test.fail(f"{out:.1f} mm left the nozzle at X{move.end[0]:g} Y{move.end[1]:g}, away from the chute at "
                      f"X{x:g} Y{y:g} ({' > '.join(move.stack)})")


def assert_rear_moves_around_the_chute(test, printer):
    """Toward the back, each printer's toolhead fits only between narrowing X limits (printer_geometry.REAR_LIMITS):
    motor mounts and the cable chain's mount on the Max 4, the chute's surround on the Plus 5 and Smart 5."""
    for move in printer.toolhead.moves:
        (x0, y0), (x1, y1) = move.start[:2], move.end[:2]
        for rear_y, x_low, x_high in REAR_LIMITS[printer.printer_id]:
            # X changes linearly along the part of the move behind rear_y, so its ends are its extremes.
            points = [(x, y) for x, y in ((x0, y0), (x1, y1)) if y > rear_y]
            if (y0 > rear_y) != (y1 > rear_y):
                points.append((x0 + (x1 - x0) * (rear_y - y0) / (y1 - y0), rear_y))
            for x, y in points:
                test.assertTrue(x_low <= x <= x_high, f"X{x:g} Y{y:g} behind Y{rear_y:g} outside X{x_low}-{x_high} "
                                                      f"({' > '.join(move.stack)})")


def cuts(printer) -> int:
    return sum(1 for command in printer.trace if command.name == "_VO_CUTTER_STRIKE")


def moves_from_line(printer, line) -> int:
    """How many moves the toolhead had queued when the print reached file line `line`."""
    return printer.moves_from_line(line)

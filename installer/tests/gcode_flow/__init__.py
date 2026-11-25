"""End-to-end G-code flows: slicer G-code run through Klipper's own G-code, macro, and motion modules with every
installed macro, on stand-in hardware and stand-ins for QIDI's compiled modules.

printer.py builds a printer from a real Valve Oil install into a printer's newest stock printer_data; hardware.py
holds the stand-in toolhead, heaters, fans, and sensors; qidi.py stands in for QIDI's compiled modules from what
openspec/observations records; slicer.py expands the slicer packs' templates into a print; checks.py holds what
every flow must hold."""

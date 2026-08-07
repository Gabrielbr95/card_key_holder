# Implementation Plan

## Slice 1: Top-half key-retention pads
- [x] 1. Add a centred, ramped friction pad to each top-half key cavity after splitting. (Verification: `assemble.py` rebuilt the STEP and both STL files without CadQuery errors.)

## Slice 2: Uniform slots for varied key thicknesses
- [x] 2. Round the maximum key thickness up to the print-layer increment, cut every key cavity to that shared thickness, and size each top pad from its actual key thickness plus configured overlap. (Verification: `assemble.py` rebuilt the STEP and both STL files without CadQuery errors.)

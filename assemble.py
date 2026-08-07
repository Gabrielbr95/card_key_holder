"""
Reads config.toml, builds a card primitive and 1-3 key primitives from the
configured dimensions, distributes the keys evenly along the card's X axis
(fixed margin at both ends, equal gaps elsewhere), and subtracts the keys
from the card.

Outputs: holder.step, holder.stl
"""

import tomllib
from math import ceil
from pathlib import Path

import cadquery as cq
from cadquery import selectors

HERE = Path(__file__).parent
CONFIG = HERE / "config.toml"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

with open(CONFIG, "rb") as f:
    cfg = tomllib.load(f)

layer_t  = cfg["layer"]["thickness"]
cover_layers = cfg["layer"]["cover_layers"]
card_cfg = cfg["card"]
key_cfgs = [cfg[k] for k in ("key1", "key2", "key3") if k in cfg]

assert 1 <= len(key_cfgs) <= 3, f"expected 1..3 keys, got {len(key_cfgs)}"
assert layer_t > 0, f"layer thickness must be positive, got {layer_t}"

card_L      = card_cfg["length"]
card_W      = card_cfg["width"]
card_fillet = card_cfg["fillet"]
card_cham   = card_cfg["chamfer"]
margin_x    = card_cfg["margin_x"]
finger_slot_dia = card_cfg["finger_slot_dia"]

key_thicknesses = [k["thickness"] for k in key_cfgs]
max_key_t = max(key_thicknesses)
slot_T = ceil(max_key_t / layer_t) * layer_t
card_T = slot_T + layer_t * cover_layers * 2

# ---------------------------------------------------------------------------
# Primitive builders (mirrors card.py / key.py logic, fed from config)
# ---------------------------------------------------------------------------

def build_card(L, W, T, fillet, chamfer):
    sk = cq.Sketch().push([(0, -W/2)]).rect(L, W)
    c = cq.Workplane("XY").placeSketch(sk).extrude(T/2, both=True)
    c = c.edges("|Z").fillet(fillet)
    c = (c.faces(">Z").edges("#Z").chamfer(chamfer)
           .faces("<Z").edges("#Z").chamfer(chamfer))
    return c

def build_key(k, cavity_thickness):
    t        = cavity_thickness
    hw, hl   = k["head_w"],  k["head_l"]
    sw, sl   = k["skirt_w"], k["skirt_l"]
    bw, bl   = k["body_w"],  k["body_l"]
    hf, sf, bf = k["head_fillet"], k["skirt_fillet"], k["body_fillet"]
    ch       = k["chamfer"]

    sk = (
        cq.Sketch()
        .push([(0, -hl/2)]).rect(hw, hl)
        .push([(0, -sl/2)]).rect(sw, sl)
        .push([(0, -bl/2)]).rect(bw, bl)
        .clean()
    )
    key = cq.Workplane("XY").placeSketch(sk).extrude(t/2, both=True)

    def nearest(wp, pt):
        return wp.edges("|Z").edges(selectors.NearestToPointSelector(pt))

    for pt in [( hw/2, -hl, 0), (-hw/2, -hl, 0)]:
        key = nearest(key, pt).fillet(hf)
    for pt in [( bw/2, -bl, 0), (-bw/2, -bl, 0)]:
        key = nearest(key, pt).fillet(bf)
    for pt in [( sw/2, -sl, 0), (-sw/2, -sl, 0)]:
        key = nearest(key, pt).fillet(sf)

#    key = (key.faces(">Z").edges("#Z").chamfer(ch)
#              .faces("<Z").edges("#Z").chamfer(ch))
    return key

# ---------------------------------------------------------------------------
# Build card + keys
# ---------------------------------------------------------------------------

card = build_card(card_L, card_W, card_T, card_fillet, card_cham)

# Each cavity uses the shared, layer-aligned thickness of the thickest key.
keys = [build_key(k, slot_T) for k in key_cfgs]
key_widths = [max(k["head_w"], k["skirt_w"], k["body_w"]) for k in key_cfgs]

# Distribute keys evenly along X with fixed margin at both ends.
# Equal gaps: before first, between, after last.
n = len(keys)
total_key_w = sum(key_widths)
gap = (card_L - 2 * margin_x - total_key_w) / (n + 1)
assert gap >= 0, f"keys don't fit: gap={gap}"

# X center of each key (left-to-right)
key_x = []
cursor = -card_L/2 + margin_x + gap # left edge of first key
for w in key_widths:
    key_x.append(cursor + w/2)
    cursor += w + gap

# ---------------------------------------------------------------------------
# Boolean: card - keys
# ---------------------------------------------------------------------------

holder = card
for x, key in zip(key_x, keys):
    holder = holder.cut(key.translate((x, 0, 0)))

# ---------------------------------------------------------------------------
# Cylindrical finger-pull slots: one per key at (key_x[i], Y=0, Z=0).
# Cuts through the full card thickness along Z.
# ---------------------------------------------------------------------------

finger_radius = finger_slot_dia / 2
# Cylinder centered on Z=0 (axis along Z), tall enough to clear the full card.
cyl_h = card_T + 2.0
for x in key_x:
    cyl = (
        cq.Workplane("XY")
        .workplane(offset=0)
        .moveTo(x, 0)
        .circle(finger_radius)
        .extrude(cyl_h)
        .translate((0, 0, -cyl_h/2))
    )
    holder = holder.cut(cyl)

split_z = slot_T / 2  # top surface of the shared key cavity

# ---------------------------------------------------------------------------
# Split into bottom + top halves at split_z.
# ---------------------------------------------------------------------------

split_results = (
    holder
    .workplane(offset=split_z)
    .split(keepTop=True, keepBottom=True)
)
top_part    = split_results.vals()[0]
bottom_part = split_results.vals()[1]

# ---------------------------------------------------------------------------
# Assembly pins (on top part) + holes (in bottom part).
# Both added AFTER splitting, BEFORE flipping -- single coordinate frame.
# Pins protrude DOWN from the top part's bottom face (at Z = split_z) into
#   the assembly (toward the keys). Tapered: wide base at split_z, narrow tip.
# Holes cut DOWN from the bottom part's top face (at Z = split_z). Tapered:
#   wide opening at split_z, narrow base.
# After the later flip, the top part's Y reverses; pins (at -Y here, body side)
#   end up at +Y in printed orientation, which is correct.
# ---------------------------------------------------------------------------

PIN_WIDTH       = 4.0
PIN_HEIGHT      = layer_t * 5
PIN_END_PADDING = 4.0       # mm from card bottom edge
HOLE_CLEAR_XY   = 0.1       # mm per side (X and Y)
HOLE_CLEAR_TOP  = layer_t   # 1 layer height extra depth
TAPER_DEG       = 30.0

# Raised pads on the top half retain keys after they slide in from Y = 0.
# Each pad sits in the body-only region (below the skirt), with a ramp on its
# Y = 0-facing end. The height deliberately intrudes into the key cavity.
FRICTION_PAD_WIDTH      = 6.0  # mm across X; centred on the key body
FRICTION_PAD_LENGTH     = 5.0  # mm along Y
FRICTION_PAD_OVERLAP    = 0.3  # mm pressed past each key's actual top surface
FRICTION_PAD_ENTRY_RAMP = 2.0  # mm along Y at the Y = 0-facing end

max_skirt_l = max(k["skirt_l"] for k in key_cfgs)
pin_y_top    = -max_skirt_l              # just below the skirt
pin_y_bottom = -card_W + PIN_END_PADDING # near bottom of card

# Gap X centers (N+1 gaps: before first key, between keys, after last key)
key_left  = [x - w/2 for x, w in zip(key_x, key_widths)]
key_right = [x + w/2 for x, w in zip(key_x, key_widths)]
left_edge  = -card_L/2 + margin_x
right_edge =  card_L/2 - margin_x

gap_x_centers = [(left_edge + key_left[0]) / 2]
for i in range(1, n):
    gap_x_centers.append((key_right[i-1] + key_left[i]) / 2)
gap_x_centers.append((key_right[n-1] + right_edge) / 2)

def pill_sketch(x_center, y_top, y_bottom, width):
    """Pill shape (stadium) centered at x_center, Y from y_bottom to y_top."""
    radius = width / 2
    straight_length = (y_top - y_bottom) - width
    straight_center_y = (y_top + y_bottom) / 2
    return (
        cq.Sketch()
        .push([(x_center, straight_center_y)]).rect(width, straight_length)
        .push([(x_center, y_top    - radius)]).circle(radius)
        .push([(x_center, y_bottom + radius)]).circle(radius)
        .clean()
    )

# --- Pins: tapered extrude DOWN from split_z, fused to top_part ---
for x in gap_x_centers:
    pin = (
        cq.Workplane("XY")
        .workplane(offset=split_z)
        .placeSketch(pill_sketch(x, pin_y_top, pin_y_bottom, PIN_WIDTH))
        .extrude(-PIN_HEIGHT, taper=TAPER_DEG)
    )
    top_part = top_part.fuse(pin.val())

# --- Friction pads: raised from the top part into each key's body cavity ---
for x, key_cfg in zip(key_x, key_cfgs):
    body_only_length = key_cfg["body_l"] - key_cfg["skirt_l"]
    assert FRICTION_PAD_WIDTH <= key_cfg["body_w"], (
        f"friction pad is wider than key body: {FRICTION_PAD_WIDTH} > "
        f"{key_cfg['body_w']}"
    )
    assert FRICTION_PAD_LENGTH <= body_only_length, (
        f"friction pad does not fit in body-only region: "
        f"{FRICTION_PAD_LENGTH} > {body_only_length}"
    )
    assert FRICTION_PAD_ENTRY_RAMP < FRICTION_PAD_LENGTH, (
        "friction-pad entry ramp must be shorter than the pad"
    )

    # The body-only region runs from -skirt_l to -body_l. On the YZ plane,
    # make a trapezoid: its sloped, +Y edge is the key-entry ramp.
    pad_y_center = -(key_cfg["skirt_l"] + key_cfg["body_l"]) / 2
    pad_y_front = pad_y_center + FRICTION_PAD_LENGTH / 2
    pad_y_back = pad_y_center - FRICTION_PAD_LENGTH / 2
    # A thinner key sits lower in the shared-thickness cavity. Extend the pad
    # to that key's actual top surface, then apply the requested overlap.
    pad_depth = (slot_T - key_cfg["thickness"]) / 2 + FRICTION_PAD_OVERLAP
    pad_z_bottom = split_z - pad_depth
    pad = (
        cq.Workplane("YZ")
        .workplane(offset=x)
        .moveTo(pad_y_front, split_z)
        .lineTo(pad_y_front - FRICTION_PAD_ENTRY_RAMP, pad_z_bottom)
        .lineTo(pad_y_back, pad_z_bottom)
        .lineTo(pad_y_back, split_z)
        .close()
        .extrude(FRICTION_PAD_WIDTH / 2, both=True)
    )
    top_part = top_part.fuse(pad.val())

# --- Holes: tapered extrude DOWN from split_z, cut from bottom_part ---
hole_depth = PIN_HEIGHT + HOLE_CLEAR_TOP
for x in gap_x_centers:
    hole = (
        cq.Workplane("XY")
        .workplane(offset=split_z)
        .placeSketch(pill_sketch(
            x,
            pin_y_top    + HOLE_CLEAR_XY,
            pin_y_bottom - HOLE_CLEAR_XY,
            PIN_WIDTH + 2 * HOLE_CLEAR_XY,
        ))
        .extrude(-hole_depth, taper=TAPER_DEG)
    )
    bottom_part = bottom_part.cut(hole.val())

# ---------------------------------------------------------------------------
# Reposition: both parts' bottom face on Z = 0; flip top part upside down;
# place top part in -Y with a visible gap below the bottom part.
# ---------------------------------------------------------------------------

def rest_z_on_zero(solid):
    bb = solid.BoundingBox()
    return solid.translate((0, 0, -bb.zmin))

bottom = rest_z_on_zero(bottom_part)

top_center = top_part.Center()
top = top_part.rotate(top_center, (top_center.x + 1, top_center.y, top_center.z), 180)
top = rest_z_on_zero(top)

print_gap_y = 5.0  # mm of empty space between the two parts in Y
bottom_bb = bottom.BoundingBox()
top_bb    = top.BoundingBox()
# Bottom part stays where it is (Y from -card_W to 0).
# Top part goes fully below it (more negative Y), with print_gap_y gap.
top_target_ymax = bottom_bb.ymin - print_gap_y
top = top.translate((0, top_target_ymax - top_bb.ymax, 0))

# Combine into one Workplane for export (assembly of 2 solids).
assembly = cq.Workplane("XY").add(bottom).add(top)

# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

assembly.export("holder.step")
top.exportStl("holder_top.stl")
bottom.exportStl("holder_bottom.stl")

print(f"card: {card_L} x {card_W} x {card_T} mm  "
      f"(T = shared_slot_t {slot_T} + {layer_t}*{cover_layers}*2)")
print(f"keys: {n}")
for i, (k, x, w) in enumerate(zip(key_cfgs, key_x, key_widths), 1):
    print(f"  key{i}: width={w} mm  X_center={x:.2f}  thickness={k['thickness']}")
print(f"margin_x={margin_x}  gap_between={gap:.2f}")
print(f"shared slot thickness: {slot_T:.2f} mm  "
      f"(max key thickness {max_key_t:.2f}, rounded up to {layer_t:.2f} mm layers)")
print(f"split at Z = {split_z:.2f}  (shared slot top surface)")
print(f"pins: {len(gap_x_centers)}  width={PIN_WIDTH}  height={PIN_HEIGHT:.2f}  "
      f"Y {pin_y_top:.1f}..{pin_y_bottom:.1f}  taper {TAPER_DEG}deg")
print(f"  gap X centers: {[f'{x:.2f}' for x in gap_x_centers]}")
print(f"friction pads: {n}  width={FRICTION_PAD_WIDTH:.1f}  "
      f"length={FRICTION_PAD_LENGTH:.1f}  overlap={FRICTION_PAD_OVERLAP:.2f}  "
      f"entry ramp={FRICTION_PAD_ENTRY_RAMP:.1f}")
print(f"bottom: bbox {bottom_bb.xlen:.1f} x {bottom_bb.ylen:.1f} x {bottom_bb.zlen:.1f}  "
      f"(z {bottom.BoundingBox().zmin:.2f}..{bottom.BoundingBox().zmax:.2f})")
print(f"top   : bbox {top_bb.xlen:.1f} x {top_bb.ylen:.1f} x {top_bb.zlen:.1f}  "
      f"(z {top.BoundingBox().zmin:.2f}..{top.BoundingBox().zmax:.2f})")
print(f"print_gap_y = {print_gap_y} mm")
print(f"solids: {len(assembly.vals())}")

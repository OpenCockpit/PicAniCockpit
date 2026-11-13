// Mask bake kernel: copy of a picture whose per-pixel alpha comes from a mask function of progress t.
// One kernel covers cross-dissolve (uniform), wipe (linear, soft edge) and iris (radial, soft edge).
// Runs ahead of the transition during the slideshow hold time (no frame deadline). No enigma2 dependency.
#pragma once
#include "frame.h"

namespace pt {

enum MaskKind {
	MASK_UNIFORM = 0, // alpha = 255 * t everywhere (cross-dissolve)
	MASK_LINEAR = 1,  // edge sweeps across the picture along direction (dx, dy)
	MASK_RADIAL = 2,  // circle of growing radius around (cx, cy)
	MASK_BLINDS = 3,  // n slats (vertical if |dx| >= |dy|, else horizontal), each revealed from its edge (dx/dy sign = side)
	MASK_CHECKER = 4, // n columns of square cells; even cells reveal first, odd cells second, each left to right
	MASK_DICE = 5,    // n columns of square cells revealed in random order (seed), each from its left edge
	MASK_WINDMILL = 6, // n blades sweeping round (cx, cy); dx < 0 turns the other way
	MASK_LUMA = 7,    // reveal by luminance of the picture itself: dark pixels first (dx < 0: bright first)
	MASK_KIND_MAX = MASK_LUMA,
};

struct MaskParams {
	int kind = MASK_UNIFORM;
	float t = 0.f;     // progress 0..1: t = 0 -> alpha 0 everywhere, t = 1 -> alpha 255 everywhere (exact)
	float soft = 0.2f; // edge softness as a fraction of the sweep length (LINEAR, RADIAL), clamped to >= 0.001
	float dx = 1.f;    // LINEAR: sweep direction (need not be normalised); (1,0) = revealed from the left
	float dy = 0.f;
	float cx = 0.5f;   // RADIAL: centre in 0..1 of the picture
	float cy = 0.5f;
	int n = 8;          // BLINDS/CHECKER/DICE/WINDMILL: number of slats / cell columns / blades (clamped to 1..256)
	uint32_t seed = 0;  // DICE: random order
};

// dst = src with the alpha byte (byte 3 of each BGRA pixel) set from the mask; colour bytes untouched (straight alpha).
// src and dst must have equal size. Alpha is monotonic non-decreasing in t for every pixel.
void bakeMask(const Frame &src, const Frame &dst, const MaskParams &p);

// The mask value itself (0..255) at pixel (x, y) of a w x h picture, for tests.
int maskAlpha(const MaskParams &p, int x, int y, int w, int h);

// MASK_LUMA: alpha for a pixel of luminance `luma` (0..255); other kinds: same as maskAlpha at (0, 0) of a 1x1 picture.
int maskAlphaLuma(const MaskParams &p, int luma);

} // namespace pt

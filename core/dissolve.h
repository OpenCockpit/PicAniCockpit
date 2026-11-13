// Cross-dissolve kernel. Scalar and NEON paths give bit-identical results.
#pragma once
#include "frame.h"

namespace pt {

// out = (a * (256 - p) + b * p + 128) >> 8 per byte, p in [0, 256].
// p == 0 copies a, p == 256 copies b (exact). Renders rows [y0, y1) only.
// a, b, out must have equal size; out may alias neither a nor b.
void dissolveRows(const Frame &a, const Frame &b, const Frame &out, int p, int y0, int y1);
void dissolveRowsScalar(const Frame &a, const Frame &b, const Frame &out, int p, int y0, int y1);

inline void dissolve(const Frame &a, const Frame &b, const Frame &out, int p)
{
	dissolveRows(a, b, out, p, 0, out.h);
}

} // namespace pt

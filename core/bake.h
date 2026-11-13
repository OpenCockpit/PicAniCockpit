// Pre-baking kernels: run during the slideshow hold time, never inside a transition. No enigma2 dependency.
#pragma once
#include "frame.h"

namespace pt {

// dst = src with the alpha byte (byte 3 of each BGRA pixel) set to `alpha`; colour bytes untouched (straight alpha).
void bakeAlpha(const Frame &src, const Frame &dst, uint8_t alpha);

// dst (w/2 x h/2, or ceil for odd sizes) = 2x2 box average of src, all four bytes.
void halve(const Frame &src, const Frame &dst);

// dst (w x h) = copy of src starting at (x, y). The window must lie inside src.
void crop(const Frame &src, const Frame &dst, int x, int y);

} // namespace pt

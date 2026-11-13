#include "bake.h"
#include <cstring>

namespace pt {

void bakeAlpha(const Frame &src, const Frame &dst, uint8_t alpha)
{
	const uint32_t a = (uint32_t)alpha << 24;
	for (int y = 0; y < dst.h; ++y) {
		const uint32_t *s = (const uint32_t *)src.row(y);
		uint32_t *d = (uint32_t *)dst.row(y);
		for (int x = 0; x < dst.w; ++x)
			d[x] = (s[x] & 0x00ffffffu) | a;
	}
}

// per-byte average of two packed pixels, rounding down
static inline uint32_t avg2(uint32_t a, uint32_t b)
{
	return ((a & 0xfefefefeu) >> 1) + ((b & 0xfefefefeu) >> 1) + (a & b & 0x01010101u);
}

void halve(const Frame &src, const Frame &dst)
{
	for (int y = 0; y < dst.h; ++y) {
		const int y0 = 2 * y, y1 = (2 * y + 1 < src.h) ? 2 * y + 1 : y0;
		const uint32_t *r0 = (const uint32_t *)src.row(y0);
		const uint32_t *r1 = (const uint32_t *)src.row(y1);
		uint32_t *d = (uint32_t *)dst.row(y);
		for (int x = 0; x < dst.w; ++x) {
			const int x0 = 2 * x, x1 = (2 * x + 1 < src.w) ? 2 * x + 1 : x0;
			d[x] = avg2(avg2(r0[x0], r0[x1]), avg2(r1[x0], r1[x1]));
		}
	}
}

void crop(const Frame &src, const Frame &dst, int x, int y)
{
	for (int r = 0; r < dst.h; ++r)
		memcpy(dst.row(r), src.row(y + r) + (size_t)x * 4, (size_t)dst.w * 4);
}

} // namespace pt

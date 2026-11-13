#include "dissolve.h"
#include <cstring>
#if defined(__ARM_NEON)
#include <arm_neon.h>
#endif

namespace pt {

static inline void copyRows(const Frame &src, const Frame &out, int y0, int y1)
{
	for (int y = y0; y < y1; ++y)
		memcpy(out.row(y), src.row(y), (size_t)out.w * 4);
}

void dissolveRowsScalar(const Frame &a, const Frame &b, const Frame &out, int p, int y0, int y1)
{
	if (p <= 0) { copyRows(a, out, y0, y1); return; }
	if (p >= 256) { copyRows(b, out, y0, y1); return; }
	const unsigned q = 256 - p;
	const int n = out.w * 4;
	for (int y = y0; y < y1; ++y) {
		const uint8_t *pa = a.row(y), *pb = b.row(y);
		uint8_t *po = out.row(y);
		for (int i = 0; i < n; ++i)
			po[i] = (uint8_t)((pa[i] * q + pb[i] * (unsigned)p + 128) >> 8);
	}
}

void dissolveRows(const Frame &a, const Frame &b, const Frame &out, int p, int y0, int y1)
{
#if defined(__ARM_NEON)
	if (p <= 0) { copyRows(a, out, y0, y1); return; }
	if (p >= 256) { copyRows(b, out, y0, y1); return; }
	// p in 1..255 and 256 - p in 1..255: both weights fit in 8 bits
	const uint8x8_t wb = vdup_n_u8((uint8_t)p);
	const uint8x8_t wa = vdup_n_u8((uint8_t)(256 - p));
	const int n = out.w * 4;
	for (int y = y0; y < y1; ++y) {
		const uint8_t *pa = a.row(y), *pb = b.row(y);
		uint8_t *po = out.row(y);
		int i = 0;
		for (; i + 16 <= n; i += 16) {
			uint8x16_t va = vld1q_u8(pa + i), vb = vld1q_u8(pb + i);
			uint16x8_t lo = vmull_u8(vget_low_u8(va), wa);
			uint16x8_t hi = vmull_u8(vget_high_u8(va), wa);
			lo = vmlal_u8(lo, vget_low_u8(vb), wb);
			hi = vmlal_u8(hi, vget_high_u8(vb), wb);
			vst1q_u8(po + i, vcombine_u8(vrshrn_n_u16(lo, 8), vrshrn_n_u16(hi, 8)));
		}
		const unsigned q = 256 - p;
		for (; i < n; ++i)
			po[i] = (uint8_t)((pa[i] * q + pb[i] * (unsigned)p + 128) >> 8);
	}
#else
	dissolveRowsScalar(a, b, out, p, y0, y1);
#endif
}

} // namespace pt

#include "mask.h"
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <vector>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

namespace pt {

static inline float clamp01(float v) { return v < 0.f ? 0.f : (v > 1.f ? 1.f : v); }

// smooth ramp from the edge function f (<=0 -> 0, >=1 -> 1)
static inline float fract(float v) { return v - std::floor(v); }

// atan2 with ~0.0015 rad error, several times faster than std::atan2 on the dm900 (the windmill evaluates it per pixel and
// every level is baked in one timer tick, which must stay short). The error only shifts blade edges by a fraction of a pixel.
static inline float fastAtan2(float y, float x)
{
	const float ax = std::fabs(x), ay = std::fabs(y);
	const float mx = ax > ay ? ax : ay, mn = ax > ay ? ay : ax;
	if (mx <= 0.f) return 0.f;
	const float a = mn / mx;
	float r = a * ((float)M_PI * 0.25f + 0.273f * (1.f - a));
	if (ay > ax) r = (float)M_PI * 0.5f - r;
	if (x < 0.f) r = (float)M_PI - r;
	return y < 0.f ? -r : r;
}

static inline uint32_t hash3(uint32_t a, uint32_t b, uint32_t c)
{
	uint32_t h = a * 0x9e3779b1u ^ (b + 0x7f4a7c15u) * 0x85ebca6bu ^ (c + 0x165667b1u) * 0xc2b2ae35u;
	h ^= h >> 15; h *= 0x2c1b3c6du; h ^= h >> 12; h *= 0x297a2d39u; h ^= h >> 15;
	return h;
}

static inline int ramp(float f)
{
	if (f <= 0.f) return 0;
	if (f >= 1.f) return 255;
	const float s = f * f * (3.f - 2.f * f);
	return (int)(s * 255.f + 0.5f);
}

namespace {

// Per-pixel evaluation split into a per-column and a per-row part so the inner loop stays cheap.
struct Evaluator {
	MaskParams p;
	int w, h;
	float soft = 0.2f;
	float te = 0.f; // t * (1 + soft)
	float inv_soft = 5.f;
	std::vector<float> colTerm; // LINEAR: contribution of x; RADIAL: (x - cx)^2
	std::vector<float> colDx;   // WINDMILL: x - cx
	float rowScale = 0.f;       // LINEAR: contribution of y per row = rowScale * y + rowBase
	float rowBase = 0.f;
	float cy_px = 0.f, inv_max = 1.f;
	int uniformAlpha = 0;
	int n = 8;
	float cell = 1.f; // CHECKER/DICE: cell side in pixels
	float flip = 1.f; // BLINDS/WINDMILL/LUMA: -1 = other side / direction
	bool vertical = true; // BLINDS: vertical slats

	Evaluator(const MaskParams &mp, int width, int height) : p(mp), w(width), h(height)
	{
		const float t = clamp01(p.t);
		soft = std::max(p.soft, 0.001f);
		inv_soft = 1.f / soft;
		te = t * (1.f + soft);
		uniformAlpha = (int)(t * 255.f + 0.5f);
		n = std::min(256, std::max(1, p.n));
		flip = p.dx < 0.f ? -1.f : 1.f;
		if (p.kind == MASK_BLINDS) {
			vertical = std::fabs(p.dx) >= std::fabs(p.dy);
			if (!vertical) flip = p.dy < 0.f ? -1.f : 1.f;
		} else if (p.kind == MASK_CHECKER || p.kind == MASK_DICE) {
			cell = std::max(1.f, (float)w / (float)n);
		}
		if (p.kind == MASK_LINEAR) {
			float dx = p.dx, dy = p.dy;
			float len = std::fabs(dx) + std::fabs(dy);
			if (len < 1e-6f) { dx = 1.f; dy = 0.f; len = 1.f; }
			// u in [0,1]: position along the sweep (0 = where it starts, 1 = where it ends)
			const float ax = dx / len, ay = dy / len;
			colTerm.resize(w);
			for (int x = 0; x < w; ++x)
				colTerm[x] = ax * ((x + 0.5f) / w - 0.5f);
			rowScale = ay / h;
			rowBase = 0.5f + ay * (0.5f / h - 0.5f);
		} else if (p.kind == MASK_RADIAL || p.kind == MASK_WINDMILL) {
			const float cxp = p.cx * w, cyp = p.cy * h;
			cy_px = cyp;
			colTerm.resize(w);
			colDx.resize(w);
			for (int x = 0; x < w; ++x) {
				const float d = (x + 0.5f) - cxp;
				colTerm[x] = d * d;
				colDx[x] = d;
			}
			// farthest corner from the centre
			const float fx = std::max(cxp, w - cxp), fy = std::max(cyp, h - cyp);
			const float maxd = std::sqrt(fx * fx + fy * fy);
			inv_max = 1.f / std::max(maxd, 1.f);
		}
	}

	// alpha for pixel (x, y); exact 0 at t = 0 and exact 255 at t = 1 for every kind
	inline int at(int x, int y) const
	{
		switch (p.kind) {
		case MASK_LINEAR: {
			const float u = colTerm[x] + rowScale * y + rowBase;
			return ramp((te - u) * inv_soft);
		}
		case MASK_RADIAL: {
			const float dy = (y + 0.5f) - cy_px;
			const float d = std::sqrt(colTerm[x] + dy * dy) * inv_max;
			return ramp((te - d) * inv_soft);
		}
		case MASK_BLINDS: {
			float f = vertical ? fract((x + 0.5f) / w * n) : fract((y + 0.5f) / h * n);
			if (flip < 0.f) f = 1.f - f;
			return ramp((te - f * 0.999f) * inv_soft);
		}
		case MASK_CHECKER: {
			const float fx = (x + 0.5f) / cell, fy = (y + 0.5f) / cell;
			const int i = (int)fx, j = (int)fy;
			const float u = 0.5f * (float)((i + j) & 1) + 0.5f * 0.999f * fract(fx);
			return ramp((te - u) * inv_soft);
		}
		case MASK_DICE: {
			const float fx = (x + 0.5f) / cell, fy = (y + 0.5f) / cell;
			const int i = (int)fx, j = (int)fy;
			const float rnd = (hash3((uint32_t)i, (uint32_t)j, p.seed) >> 8) * (1.f / 16777216.f);
			const float u = 0.8f * rnd + 0.2f * 0.999f * fract(fx);
			return ramp((te - u) * inv_soft);
		}
		case MASK_WINDMILL: {
			const float dy = (y + 0.5f) - cy_px;
			float a = (fastAtan2(dy, colDx[x]) + (float)M_PI) * (0.5f / (float)M_PI);
			if (flip < 0.f) a = 1.f - a;
			const float u = fract(a * n) * 0.999f;
			return ramp((te - u) * inv_soft);
		}
		default:
			return uniformAlpha;
		}
	}

	// MASK_LUMA: u is the luminance (dark first); flip < 0: bright first
	inline int atLuma(int luma) const
	{
		float u = luma * (1.f / 255.f);
		if (flip < 0.f) u = 1.f - u;
		return ramp((te - u * 0.999f) * inv_soft);
	}
};

} // namespace

int maskAlpha(const MaskParams &p, int x, int y, int w, int h)
{
	Evaluator e(p, w, h);
	return p.kind == MASK_LUMA ? e.atLuma(0) : e.at(x, y);
}

int maskAlphaLuma(const MaskParams &p, int luma)
{
	Evaluator e(p, 1, 1);
	return p.kind == MASK_LUMA ? e.atLuma(luma) : e.at(0, 0);
}

void bakeMask(const Frame &src, const Frame &dst, const MaskParams &p)
{
	Evaluator e(p, dst.w, dst.h);
	for (int y = 0; y < dst.h; ++y) {
		const uint32_t *s = (const uint32_t *)src.row(y);
		uint32_t *d = (uint32_t *)dst.row(y);
		if (p.kind == MASK_UNIFORM) {
			const uint32_t a = (uint32_t)e.uniformAlpha << 24;
			for (int x = 0; x < dst.w; ++x)
				d[x] = (s[x] & 0x00ffffffu) | a;
		} else if (p.kind == MASK_LUMA) {
			for (int x = 0; x < dst.w; ++x) {
				const uint32_t v = s[x]; // BGRA in memory = 0xAARRGGBB as a little endian word
				const int luma = (int)((77u * ((v >> 16) & 255u) + 150u * ((v >> 8) & 255u) + 29u * (v & 255u)) >> 8);
				d[x] = (v & 0x00ffffffu) | ((uint32_t)e.atLuma(luma) << 24);
			}
		} else {
			for (int x = 0; x < dst.w; ++x)
				d[x] = (s[x] & 0x00ffffffu) | ((uint32_t)e.at(x, y) << 24);
		}
	}
}

} // namespace pt

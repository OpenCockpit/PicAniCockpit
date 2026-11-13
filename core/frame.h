// Frame: a non-owning view of a 32-bit pixel image. No enigma2 dependency.
#pragma once
#include <cstdint>
#include <cstddef>

namespace pt {

struct Frame {
	uint8_t *data = nullptr;
	int w = 0;
	int h = 0;
	int stride = 0; // bytes per row, >= w * 4

	uint8_t *row(int y) const { return data + (size_t)y * stride; }
	bool valid() const { return data && w > 0 && h > 0 && stride >= w * 4; }
};

} // namespace pt

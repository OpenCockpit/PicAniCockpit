// PicAniCockpit native module (_pictrans): bake kernels for slideshow transitions, operating on enigma2 gPixmaps.
// Only this file touches enigma2 headers; core/ stays dependency free and is unit-tested on the host.
// Everything here runs ahead of a transition (slideshow hold time), never inside it.
#include <Python.h>
#include "../glue/swigpyrun.h"
#include <lib/gdi/gpixmap.h>
#include <chrono>
#include "../core/bake.h"
#include "../core/mask.h"

#define PT_ABI_VERSION 1

static swig_type_info *pixmapType()
{
	static swig_type_info *t = nullptr;
	if (!t)
		t = SWIG_TypeQuery("ePtr< gPixmap > *");
	return t;
}

static double msSince(std::chrono::steady_clock::time_point t0)
{
	return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
}

// Python gPixmapPtr -> raw gPixmap* (borrowed). nullptr + Python error set on failure.
static gPixmap *pixmapFromPy(PyObject *o)
{
	void *p = nullptr;
	swig_type_info *t = pixmapType();
	if (!t) {
		PyErr_SetString(PyExc_RuntimeError, "_pictrans: SWIG type 'ePtr< gPixmap > *' not found (not running inside enigma2?)");
		return nullptr;
	}
	if (!SWIG_IsOK(SWIG_ConvertPtr(o, &p, t, 0)) || !p) {
		PyErr_SetString(PyExc_TypeError, "_pictrans: expected a gPixmapPtr");
		return nullptr;
	}
	gPixmap *pix = (*(ePtr<gPixmap> *)p);
	if (!pix || !pix->surface || !pix->surface->data) {
		PyErr_SetString(PyExc_ValueError, "_pictrans: pixmap has no pixel data");
		return nullptr;
	}
	return pix;
}

// 32 bpp pixmap -> Frame view. Sets a Python error and returns false when the pixmap is unusable.
static bool toFrame(gPixmap *pix, pt::Frame &f)
{
	gUnmanagedSurface *s = pix->surface;
	if (s->bpp != 32 || s->x <= 0 || s->y <= 0 || s->stride < s->x * 4) {
		PyErr_Format(PyExc_ValueError, "_pictrans: need a 32 bpp pixmap (got %dx%d bpp=%d stride=%d)", s->x, s->y, s->bpp, s->stride);
		return false;
	}
	f.data = (uint8_t *)s->data;
	f.w = s->x;
	f.h = s->y;
	f.stride = s->stride;
	return true;
}

// Fresh 32 bpp gPixmapPtr (new reference). *raw receives the borrowed pointer.
static PyObject *newPixmapObject(int w, int h, gPixmap **raw)
{
	if (w <= 0 || h <= 0 || w > 8192 || h > 8192) {
		PyErr_SetString(PyExc_ValueError, "_pictrans: bad size");
		return nullptr;
	}
	swig_type_info *t = pixmapType();
	if (!t) {
		PyErr_SetString(PyExc_RuntimeError, "_pictrans: SWIG type not found");
		return nullptr;
	}
	ePtr<gPixmap> *holder = new ePtr<gPixmap>(new gPixmap(eSize(w, h), 32, gPixmap::accelNever));
	if (!*holder || !(*holder)->surface || !(*holder)->surface->data) {
		delete holder;
		PyErr_SetString(PyExc_MemoryError, "_pictrans: gPixmap allocation failed");
		return nullptr;
	}
	*raw = *holder;
	return SWIG_NewPointerObj(holder, t, SWIG_POINTER_OWN);
}

// new(w, h) -> gPixmapPtr, content undefined
static PyObject *py_new(PyObject *, PyObject *args)
{
	int w, h;
	if (!PyArg_ParseTuple(args, "ii", &w, &h))
		return nullptr;
	gPixmap *raw = nullptr;
	return newPixmapObject(w, h, &raw);
}

// size(pix) -> (w, h)
static PyObject *py_size(PyObject *, PyObject *args)
{
	PyObject *o;
	if (!PyArg_ParseTuple(args, "O", &o))
		return nullptr;
	gPixmap *p = pixmapFromPy(o);
	if (!p)
		return nullptr;
	return Py_BuildValue("(ii)", p->surface->x, p->surface->y);
}

// fill(pix, argb): solid colour, 0xAARRGGBB with alpha 255 = opaque (pixmap data, not the inverted widget colour alpha)
static PyObject *py_fill(PyObject *, PyObject *args)
{
	PyObject *o;
	unsigned int argb;
	if (!PyArg_ParseTuple(args, "OI", &o, &argb))
		return nullptr;
	gPixmap *p = pixmapFromPy(o);
	pt::Frame f;
	if (!p || !toFrame(p, f))
		return nullptr;
	for (int y = 0; y < f.h; ++y) {
		uint32_t *r = (uint32_t *)f.row(y);
		for (int x = 0; x < f.w; ++x)
			r[x] = argb;
	}
	Py_RETURN_NONE;
}

// pixel(pix, x, y) -> 0xAARRGGBB
static PyObject *py_pixel(PyObject *, PyObject *args)
{
	PyObject *o;
	int x, y;
	if (!PyArg_ParseTuple(args, "Oii", &o, &x, &y))
		return nullptr;
	gPixmap *p = pixmapFromPy(o);
	pt::Frame f;
	if (!p || !toFrame(p, f))
		return nullptr;
	if (x < 0 || y < 0 || x >= f.w || y >= f.h) {
		PyErr_SetString(PyExc_IndexError, "pixel outside the picture");
		return nullptr;
	}
	return PyLong_FromUnsignedLong(((uint32_t *)f.row(y))[x]);
}

// selftest() -> (ok, text): creates a pixmap and checks that the surface layout this module was compiled against is the one
// enigma2 actually uses. Any mismatch must disable the module instead of crashing.
static PyObject *py_selftest(PyObject *, PyObject *)
{
	gPixmap *p = nullptr;
	PyObject *o = newPixmapObject(64, 48, &p);
	if (!o) {
		PyObject *et, *ev, *tb;
		PyErr_Fetch(&et, &ev, &tb);
		PyObject *s = ev ? PyObject_Str(ev) : nullptr;
		PyObject *res = Py_BuildValue("(Os)", Py_False, s ? PyUnicode_AsUTF8(s) : "allocation failed");
		Py_XDECREF(s); Py_XDECREF(et); Py_XDECREF(ev); Py_XDECREF(tb);
		return res;
	}
	const gUnmanagedSurface *s = p->surface;
	char msg[160];
	bool ok = s->x == 64 && s->y == 48 && s->bpp == 32 && s->bypp == 4 && s->stride >= 64 * 4 && s->stride <= 64 * 4 + 256;
	if (ok) {
		// touch the first and the last pixel of the buffer through the raw pointer: a wrong stride/offset layout would
		// fault or read back garbage here, in a throw-away 12 KB pixmap, instead of in a real picture
		uint32_t *first = (uint32_t *)s->data;
		uint32_t *last = (uint32_t *)((uint8_t *)s->data + (size_t)(s->y - 1) * s->stride) + (s->x - 1);
		*first = 0x80123456u;
		*last = 0x7f654321u;
		ok = *first == 0x80123456u && *last == 0x7f654321u;
	}
	snprintf(msg, sizeof(msg), "%s: %dx%d bpp=%d bypp=%d stride=%d abi=%d", ok ? "ok" : "LAYOUT MISMATCH", s->x, s->y, s->bpp, s->bypp, s->stride, PT_ABI_VERSION);
	Py_DECREF(o);
	return Py_BuildValue("(Os)", ok ? Py_True : Py_False, msg);
}

// Frame helpers shared by the bake functions: parse a source pixmap and allocate a destination of w x h.
struct BakeIO {
	gPixmap *ps = nullptr, *pd = nullptr;
	pt::Frame fs, fd;
	PyObject *res = nullptr; // new reference to the destination pixmap
	bool open(PyObject *src, int w, int h)
	{
		ps = pixmapFromPy(src);
		if (!ps || !toFrame(ps, fs))
			return false;
		res = newPixmapObject(w, h, &pd);
		if (!res)
			return false;
		if (!toFrame(pd, fd)) {
			Py_CLEAR(res);
			return false;
		}
		return true;
	}
};

// crop(src, x, y, w, h) -> (new pixmap w x h, ms)
static PyObject *py_crop(PyObject *, PyObject *args)
{
	PyObject *o;
	int x, y, w, h;
	if (!PyArg_ParseTuple(args, "Oiiii", &o, &x, &y, &w, &h))
		return nullptr;
	BakeIO io;
	if (!io.open(o, w, h))
		return nullptr;
	if (x < 0 || y < 0 || x + w > io.fs.w || y + h > io.fs.h) {
		Py_DECREF(io.res);
		PyErr_SetString(PyExc_ValueError, "crop window outside the picture");
		return nullptr;
	}
	auto t0 = std::chrono::steady_clock::now();
	pt::crop(io.fs, io.fd, x, y);
	return Py_BuildValue("(Nd)", io.res, msSince(t0));
}

// halve(src) -> (new pixmap of half size, ms)
static PyObject *py_halve(PyObject *, PyObject *args)
{
	PyObject *o;
	if (!PyArg_ParseTuple(args, "O", &o))
		return nullptr;
	gPixmap *ps = pixmapFromPy(o);
	if (!ps)
		return nullptr;
	BakeIO io;
	if (!io.open(o, (ps->surface->x + 1) / 2, (ps->surface->y + 1) / 2))
		return nullptr;
	auto t0 = std::chrono::steady_clock::now();
	pt::halve(io.fs, io.fd);
	return Py_BuildValue("(Nd)", io.res, msSince(t0));
}

// bake_mask(src, kind, t, soft, dx, dy, cx, cy) -> (new pixmap, ms). kind: 0 uniform, 1 linear, 2 radial (see core/mask.h)
static PyObject *py_bake_mask(PyObject *, PyObject *args)
{
	PyObject *o;
	int kind;
	float t, soft, dx, dy, cx, cy;
	int n = 8;
	unsigned int seed = 0;
	if (!PyArg_ParseTuple(args, "Oiffffff|iI", &o, &kind, &t, &soft, &dx, &dy, &cx, &cy, &n, &seed))
		return nullptr;
	if (kind < pt::MASK_UNIFORM || kind > pt::MASK_KIND_MAX) {
		PyErr_SetString(PyExc_ValueError, "bad mask kind");
		return nullptr;
	}
	gPixmap *ps = pixmapFromPy(o);
	if (!ps)
		return nullptr;
	BakeIO io;
	if (!io.open(o, ps->surface->x, ps->surface->y))
		return nullptr;
	pt::MaskParams p;
	p.kind = kind; p.t = t; p.soft = soft; p.dx = dx; p.dy = dy; p.cx = cx; p.cy = cy; p.n = n; p.seed = seed;
	auto t0 = std::chrono::steady_clock::now();
	pt::bakeMask(io.fs, io.fd, p);
	return Py_BuildValue("(Nd)", io.res, msSince(t0));
}

static PyMethodDef methods[] = {
	{"new", py_new, METH_VARARGS, "new(w, h) -> fresh 32 bpp gPixmapPtr"},
	{"size", py_size, METH_VARARGS, "size(pix) -> (w, h)"},
	{"fill", py_fill, METH_VARARGS, "fill(pix, argb)"},
	{"pixel", py_pixel, METH_VARARGS, "pixel(pix, x, y) -> argb"},
	{"selftest", py_selftest, METH_NOARGS, "selftest() -> (ok, text): ABI layout check"},
	{"crop", py_crop, METH_VARARGS, "crop(src, x, y, w, h) -> (pixmap, ms)"},
	{"halve", py_halve, METH_VARARGS, "halve(src) -> (pixmap, ms)"},
	{"bake_mask", py_bake_mask, METH_VARARGS, "bake_mask(src, kind, t, soft, dx, dy, cx, cy[, n, seed]) -> (pixmap, ms)"},
	{nullptr, nullptr, 0, nullptr}};

static struct PyModuleDef moddef = {PyModuleDef_HEAD_INIT, "_pictrans", "PicAniCockpit native bake kernels", -1, methods, nullptr, nullptr, nullptr, nullptr};

PyMODINIT_FUNC PyInit__pictrans(void)
{
	return PyModule_Create(&moddef);
}

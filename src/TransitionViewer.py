# coding=utf-8
# PicAniCockpit - TransitionViewer: a GUI component (one skin widget, e.g. MediaCockpit's "image") that shows slideshow
# pictures and plays transitions between them.
#
# How it works (measured on the dm900, docs/TransitionEngine-design.md sections 7 and 7b):
#   * pictures are decoded by ePicLoad to exactly the widget size (letterboxed, rotated) - one canvas pixmap per picture;
#   * the native module bakes the layers an effect needs (alpha ladder, door halves, ...) in small jobs, one per timer tick,
#     during the slideshow hold time; every texture is uploaded through a 4x2 px "pre" widget BEFORE the transition;
#   * a transition only issues a few widget operations per frame (move/resize/show/set pixmap) at 25 fps, wall-clock paced.
# The widget is a container: all pictures are child ePixmaps, so everything is clipped to the skin widget's area.

import random
import time
import weakref
from enigma import eWidget, ePixmap, ePicLoad, eTimer, ePoint, eSize, gPixmapPtr
from Components.GUIComponent import GUIComponent
from . import native
from . import Effects

FRAME_MS = 40          # 25 fps
PRELOAD_STEP_MS = 40   # one texture upload per step (an upload takes ~45 ms at 1080p, ~15 ms at 540p)
PRELOAD_HALF_STEP_MS = 20
# Ken Burns: 20 steps per second. Every step redraws the whole screen (~35 ms on the render thread), so 25 fps was
# never sustained (measured: avg 48-55 ms); a steady 20 fps looks smoother than an irregular 25
KB_STEP_MS = 50
KB_ZOOM = 0.10         # Ken Burns: the picture grows by this much over the hold time (a pop-free start needs 1.0 at the beginning)
DEFAULT_DURATION_MS = 1200

IDLE, DECODING, BAKING, PRELOADING, READY, RUNNING = "idle", "decoding", "baking", "preloading", "ready", "running"


def log(msg):
    print(f"[PicAniCockpit] {msg}")


class Layer:
    """One child ePixmap. Skips operations that would not change anything: every widget move costs ~0.7 ms of main-thread time."""

    def __init__(self, parent, z, blend=False):
        self.w = ePixmap(parent)
        self.w.setZPosition(z)
        self.w.setScale(1)                      # a pixmap of another size (half-resolution ladder, zoom) is scaled to the widget
        self.w.setAlphatest(2 if blend else 0)  # 2 = alpha blend (baked alpha levels), 0 = opaque
        self.w.hide()
        self.pix = None
        self.g = None
        self.vis = False

    def set(self, pix):
        if pix is not self.pix:
            self.pix = pix
            self.w.setPixmap(pix)

    def geom(self, x, y, w, h):
        x, y, w, h = int(x), int(y), int(w), int(h)
        g = self.g
        if g is None or g[2:] != (w, h):
            self.w.resize(eSize(w, h))
        if g is None or g[:2] != (x, y):
            self.w.move(ePoint(x, y))
        self.g = (x, y, w, h)

    def show(self):
        if not self.vis:
            self.vis = True
            self.w.show()

    def hide(self):
        if self.vis:
            self.vis = False
            self.w.hide()

    def clear(self):
        """Forget the picture so its pixmap and GPU texture can be freed."""
        self.hide()
        if self.pix is not None:
            self.pix = None
            self.w.setPixmap(gPixmapPtr())


class RectLayer:
    """A solid colour over the whole widget with changing opacity (fade through black/white, light leak).

    It is a tiny pixmap with the wanted alpha, stretched over the widget, NOT a widget background colour: gEGLDC draws a
    translucent rectangle with the "ignore destination" alpha formula (out.a = src.a), which punches a hole for the video plane,
    so the TV picture showed through the middle of every fade. Pixmap blits always use the real "over" formula. One small
    pixmap per (colour, opacity step), created on first use and kept until reset()."""
    STEPS = 32

    def __init__(self, parent, z, nat):
        self.layer = Layer(parent, z, blend=True)
        self.native = nat
        self.cache = {}
        self.wanted = False   # show() was called
        self.k = 0            # current opacity step (0 = invisible)

    def geom(self, x, y, w, h):
        self.layer.geom(x, y, w, h)

    def setColor(self, rgb, opacity):
        k = max(0, min(self.STEPS, int(opacity * self.STEPS + 0.5)))
        self.k = k
        if k == 0:
            self.layer.hide()
            return
        key = (rgb, k)
        pix = self.cache.get(key)
        if pix is None:
            pix = self.native.new(16, 9)
            self.native.fill(pix, (min(255, k * 255 // self.STEPS) << 24) | (rgb & 0xffffff))
            self.cache[key] = pix
        self.layer.set(pix)
        if self.wanted:
            self.layer.show()

    def show(self):
        self.wanted = True
        if self.k:
            self.layer.show()

    def hide(self):
        self.wanted = False
        self.layer.hide()

    def reset(self):
        self.hide()
        self.k = 0
        self.layer.clear()
        self.cache = {}


class _Job:
    """One slide on its way to the screen: decode -> bake -> preload -> ready."""

    def __init__(self, path, forward):
        self.path = path
        self.forward = forward
        self.state = DECODING
        self.pix = None
        self.picload = None
        self.effect = None
        self.effect_id = None
        self.todo = []      # baking jobs still to run
        self.pre = []       # pixmaps still to upload
        self.start = False  # a transition (or cut) was requested
        self.cut = False    # show without a transition
        self.t_created = time.monotonic()
        self.t_decoded = self.t_baked = self.t_ready = 0.0  # for the log line of the finished transition
        self.kb_zoomed = False   # the old picture was Ken Burns zoomed when the transition started
        self.frames = 0
        self.late = 0
        self.last_frame = 0.0
        self.iv_sum = 0.0
        self.iv_max = 0.0


class TransitionViewer(GUIComponent):
    GUI_WIDGET = eWidget

    def __init__(self):
        GUIComponent.__init__(self)
        self.native = native()
        self.mode = Effects.EFFECT_CROSSFADE
        self.quality = "normal"
        self.duration_ms = DEFAULT_DURATION_MS
        self.bg = "#00000000"        # ePicLoad background, "#AARRGGBB"
        self.cur_pix = None
        self.cur_path = None
        self.last_effect = None
        self.kenburns = False
        self.W = self.H = 0
        self._job = None
        self._state = IDLE
        self._t0 = 0.0
        self._on_changed = []
        self._timer = eTimer()
        self._timer.callback.append(self._tick)
        self._layers_ready = False
        # ePicLoad objects of finished jobs. They must not die inside their own PictureData callback, so they wait here until a
        # one-shot timer releases them (see _retire).
        self._old_loaders = []
        self._release_timer = eTimer()
        self._release_timer.callback.append(self._releaseLoaders)
        self.kb_ms = 0          # Ken Burns duration (the slide hold time)
        self._kb = None         # running Ken Burns motion: (t0, anchor x, anchor y)
        self._kb_stat = [0, 0.0, 0.0, 0.0, 0.0]
        self._kb_dur = 1000
        self._kb_timer = eTimer()
        self._kb_timer.callback.append(self._kbStep)

    # ---------------------------------------------------------------- Merlin-like API used by the slideshow screen
    def connectImageChanged(self, callback):
        self._on_changed.append(callback)

    def scaleToScreen(self, flag):
        pass  # pictures are always decoded to the widget size

    def setTransitionMode(self, mode):
        self.mode = int(mode)

    def setTransitionDuration(self, ms):
        self.duration_ms = max(200, int(ms))

    def setQuality(self, quality):
        self.quality = quality if quality in Effects.QUALITIES else "normal"

    def setKenBurns(self, flag, duration_ms=0):
        """Slow zoom of every picture towards a random point during the hold time (duration_ms). Effects that need the old
        picture as a plain full-screen picture (barn doors) are replaced by a cross-dissolve while it is on."""
        self.kenburns = bool(flag)
        self.kb_ms = max(0, int(duration_ms))
        if not self.kenburns:
            self._kbStop()

    def setBackground(self, color):
        """color: gRGB or '#AARRGGBB' string; the letterbox colour of decoded pictures."""
        # ePicLoad paints the letterbox with this colour including its alpha byte: force it opaque (enigma2 gRGB alpha 0 = opaque)
        self.bg = color if isinstance(color, str) else f"#ff{color.argb() & 0xffffff:06x}"

    def isAvailable(self):
        return self.native is not None

    def isBusy(self):
        return self._state == RUNNING

    def prepare(self, path, forward=True):
        """Start decoding/baking the next picture now (during the hold time) so the transition can start instantly."""
        if self.native is None or not self._layers_ready or self.W <= 0 or self.H <= 0:
            return
        job = self._job
        if job is not None and job.path == path and job.forward == forward:
            return
        if self._state == RUNNING:
            return  # the running transition owns the layers; the slideshow prepares again after it
        self._dropJob()
        self._job = job = _Job(path, forward)
        self._decode(job)

    def setPicture(self, path, forward=True, fast=False):
        """Show path with a transition. fast=True (manual navigation) cuts as soon as the picture is decoded, unless it is already fully prepared."""
        if self.native is None or not self._layers_ready or self.W <= 0 or self.H <= 0:
            return False
        if self._state == RUNNING:
            self._finish()  # snap the running transition to its end, then continue with the new picture
        job = self._job
        if job is None or job.path != path or job.forward != forward:
            self._dropJob()
            self._job = job = _Job(path, forward)
            self._decode(job)
            if self._job is not job:
                return False  # decoding could not even start: the caller shows the picture itself
        job.start = True
        job.cut = job.cut or (fast and job.state != READY)  # a prepared picture still gets its transition
        self._maybeStart(job)
        return True

    def cancel(self):
        if self._state == RUNNING:
            self._finish()

    def show(self):
        GUIComponent.show(self)

    def hide(self):
        """Hidden = the slideshow is not running: free every picture so the next run starts with a cut."""
        GUIComponent.hide(self)
        self._reset()

    # ---------------------------------------------------------------- widget lifetime
    def applySkin(self, desktop, parent):
        """The widget size is only known after the skin attributes are applied (postWidgetCreate runs before that)."""
        ret = GUIComponent.applySkin(self, desktop, parent)
        sz = self.instance.size()
        self.W, self.H = sz.width(), sz.height()
        log(f"widget size {self.W}x{self.H}")
        return ret

    def postWidgetCreate(self, instance):
        self.s0 = Layer(instance, 1)
        self.s1 = Layer(instance, 2)
        self.s2 = Layer(instance, 3)
        self.ovl = Layer(instance, 4, blend=True)
        self.rect = RectLayer(instance, 5, self.native)
        self.pre = Layer(instance, 6, blend=True)
        self.pre.geom(0, 0, 4, 2)
        self._layers_ready = True

    def preWidgetRemove(self, instance):  # pylint: disable=unused-argument
        self._timer.stop()
        self._release_timer.stop()
        self._kb_timer.stop()
        self._layers_ready = False
        self._reset()
        self._on_changed = []

    # ---------------------------------------------------------------- effect context (see Effects.py)
    @property
    def a(self):
        return self.cur_pix

    @property
    def b(self):
        return self._job.pix if self._job else None

    @property
    def a_geom(self):
        return self.s0.g or (0, 0, self.W, self.H)

    def showOnly(self, pix, keep_rect=False):
        self.s0.set(pix)
        self.s0.geom(0, 0, self.W, self.H)
        self.s0.show()
        self.s1.hide()
        self.s2.hide()
        self.ovl.hide()
        if not keep_rect:
            self.rect.hide()

    # ---------------------------------------------------------------- pipeline
    def _decode(self, job):
        """ePicLoad decodes asynchronously on its own thread and calls back on the main loop."""
        try:
            pl = ePicLoad()
            job.picload = pl
            # The callback must only hold a WEAK reference to the job: picload -> callback -> job -> picload is a reference cycle that
            # runs through C++ (the Python GC cannot see it), so every job, and with it its 8 MB 1080p texture, leaked: one texture
            # per slide, enigma2 died with ION_IOC_ALLOC after ~19 slides.
            pl.PictureData.get().append(lambda info="", r=weakref.ref(job): self._onDecodedRef(r))
            pl.setPara([self.W, self.H, 1, 1, 0, 1, self.bg, True])  # w, h, keep aspect, as, no cache, better resize, background, auto rotate
            if pl.startDecode(job.path):
                raise RuntimeError("startDecode failed")
        except Exception as ex:  # noqa: BLE001
            log(f"decode start failed for {job.path}: {ex!r}")
            self._failed(job)

    def _onDecodedRef(self, ref):
        job = ref()
        if job is not None:
            self._onDecoded(job)

    def _retire(self, job):
        """Hand the job's ePicLoad over to the release timer (it may be the object that is calling us)."""
        if job is not None and job.picload is not None:
            self._old_loaders.append(job.picload)
            job.picload = None
            self._release_timer.start(1000, True)

    def _releaseLoaders(self):
        self._old_loaders = []

    def _onDecoded(self, job):
        if job is not self._job:
            return  # stale callback of a dropped job
        try:
            pix = job.picload.getData()
            if not pix:
                raise RuntimeError("no pixmap")
            job.pix = pix
            job.t_decoded = time.monotonic()
            size = self.native.size(pix)
            plain = size != (self.W, self.H)  # unexpected size (e.g. 8 bpp or odd decoder result): no effect possible
            cur_ok = self.cur_pix is not None and self.native.size(self.cur_pix) == (self.W, self.H)
            if plain or not cur_ok or job.cut or self.mode == Effects.EFFECT_CUT:
                job.state = READY
                job.cut = True
                self._maybeStart(job)
                return
            eid, eff = Effects.create(self.mode, job.forward, self.quality, self.last_effect, self.kenburns)
            job.effect_id, job.effect = eid, eff
            job.todo = list(eff.jobs(self))
            job.state = BAKING
            self._state = BAKING
            self._timer.start(1, True)
        except Exception as ex:  # noqa: BLE001
            log(f"decode result failed for {job.path}: {ex!r}")
            self._failed(job)

    def _failed(self, job):
        """Could not decode (or bake) the picture: leave the current one on screen and let the slideshow carry on."""
        if job is self._job:
            self._job = None
        self._retire(job)
        self._state = IDLE

    def _maybeStart(self, job):
        if job is not self._job or not job.start:
            return
        if job.state == READY:
            if job.cut:
                self._cut(job)
            else:
                self._start(job)

    def _tick(self):
        try:
            st = self._state
            if st == BAKING:
                self._bakeStep()
            elif st == PRELOADING:
                self._preloadStep()
            elif st == RUNNING:
                self._runStep()
        except Exception as ex:  # noqa: BLE001 - never let an exception escape into the enigma2 main loop
            log(f"tick failed in state {self._state}: {ex!r}")
            self._recover()

    def _bakeStep(self):
        job = self._job
        if job is None:
            self._state = IDLE
            return
        if job.todo:
            job.todo.pop(0)()
            self._timer.start(1, True)
            return
        job.t_baked = time.monotonic()
        job.pre = list(job.effect.preload(self))
        job.state = PRELOADING
        self._state = PRELOADING
        self._timer.start(1, True)

    def _preloadStep(self):
        job = self._job
        if job is None:
            self._state = IDLE
            return
        if job.pre:
            pix = job.pre.pop(0)
            w, h = self.native.size(pix)
            self.pre.set(pix)
            self.pre.show()
            # half-resolution levels upload in ~15 ms: a shorter step keeps 20-30 level ladders ready within the hold time
            self._timer.start(PRELOAD_STEP_MS if w * h * 2 > self.W * self.H else PRELOAD_HALF_STEP_MS, True)
            return
        self.pre.hide()
        self.pre.clear()
        job.t_ready = time.monotonic()
        job.state = READY
        self._state = READY
        self._maybeStart(job)

    # ---------------------------------------------------------------- Ken Burns (one widget resize + move per frame)
    def kbFinalGeom(self):
        """Geometry (x, y, w, h) the current picture has at the END of the running Ken Burns zoom, or None when it is not running.
        Effects that cut the old picture into pieces (barn doors) cut it as it will look when the transition starts."""
        if self._kb is None:
            return None
        _, ax, ay = self._kb
        w, h = int(self.W * (1.0 + KB_ZOOM)), int(self.H * (1.0 + KB_ZOOM))
        return (-int((w - self.W) * ax), -int((h - self.H) * ay), w, h)

    def _kbStart(self):
        self._kbStop()
        if not self.kenburns or self.kb_ms <= 0 or not self._layers_ready or self.cur_pix is None:
            return
        # the next transition starts one hold time after the previous one STARTED, i.e. (hold - transition) after this arrival:
        # the zoom is timed to be complete exactly then
        self._kb_dur = max(1000, self.kb_ms - self.duration_ms)
        self._kb = (time.monotonic(), random.random(), random.random())
        self._kb_stat = [0, 0.0, 0.0, 0.0, 0.0]   # steps, sum of intervals, max interval, last step time, sum of step costs
        self._kb_timer.start(KB_STEP_MS, True)

    def _kbStop(self):
        if self._kb is not None and self._kb_stat[0] > 1:
            n, sm, mx, _, cost = self._kb_stat
            log(f"ken burns: {n} steps, interval avg/max {sm / max(1, n - 1):.0f}/{mx:.0f} ms, widget ops avg {cost / n:.1f} ms")
        self._kb = None
        self._kb_timer.stop()

    def _kbStep(self):
        kb = self._kb
        if kb is None or self._state == RUNNING:
            return
        try:
            t0, ax, ay = kb
            now = time.monotonic()
            st = self._kb_stat
            if st[3]:
                iv = (now - st[3]) * 1000.0
                st[1] += iv
                st[2] = max(st[2], iv)
                if iv > KB_STEP_MS * 2.5:
                    # the main loop or the render queue stalled: carry on from where the motion was, do not jump ahead
                    t0 += (iv - KB_STEP_MS) / 1000.0
                    self._kb = (t0, ax, ay)
            st[3] = now
            f = min(1.0, (now - t0) * 1000.0 / self._kb_dur)
            z = 1.0 + KB_ZOOM * f
            w, h = int(self.W * z), int(self.H * z)
            self.s0.geom(-int((w - self.W) * ax), -int((h - self.H) * ay), w, h)
            st[0] += 1
            st[4] += (time.monotonic() - now) * 1000.0
            if f < 1.0:
                self._kb_timer.start(KB_STEP_MS, True)
        except Exception as ex:  # noqa: BLE001
            log(f"ken burns failed: {ex!r}")
            self._kb = None

    def _start(self, job):
        job.kb_zoomed = self.s0.g is not None and self.s0.g[2] > self.W
        self._kbStop()
        self._state = RUNNING
        job.effect.begin(self)
        self._t0 = time.monotonic()
        self._timer.start(1, True)

    def _runStep(self):
        job = self._job
        now = time.monotonic()
        t = (now - self._t0) * 1000.0 / self.duration_ms
        if t >= 1.0:
            self._finish()
            return
        if job.last_frame:
            iv = (now - job.last_frame) * 1000.0
            job.iv_sum += iv
            job.iv_max = max(job.iv_max, iv)
            if iv > FRAME_MS * 1.5:
                job.late += 1
        job.last_frame = now
        job.frames += 1
        job.effect.frame(self, job.effect.easing(Effects.clamp01(t)))
        spent = (time.monotonic() - now) * 1000.0
        self._timer.start(max(1, int(FRAME_MS - spent)), True)

    def _finish(self):
        job = self._job
        self._timer.stop()
        if job is None or job.effect is None:
            self._state = IDLE
            return
        job.effect.end(self)
        n = max(1, job.frames - 1)
        kb = "zoomed" if job.kb_zoomed else "off"
        total = (time.monotonic() - self._t0) * 1000.0
        log(f"{job.effect.name} fwd={job.forward} q={self.quality} kb={kb}: {job.frames} frames in {total:.0f} ms, "
            f"interval avg/max {job.iv_sum / n:.0f}/{job.iv_max:.0f} ms, late {job.late} | "
            f"decode {(job.t_decoded - job.t_created) * 1000.0:.0f} bake {(job.t_baked - job.t_decoded) * 1000.0:.0f} "
            f"preload {(job.t_ready - job.t_baked) * 1000.0:.0f} ms")
        self._arrived(job)

    def _cut(self, job):
        self._kbStop()
        self._timer.stop()
        self.showOnly(job.pix)
        self._arrived(job)

    def _arrived(self, job):
        """job.pix is on screen now: it becomes the current picture; baked layers are released."""
        self.cur_pix, self.cur_path = job.pix, job.path
        if job.effect is not None:
            self.last_effect = job.effect_id
            job.effect.release()
        for lay in (self.s1, self.s2, self.ovl):
            lay.clear()
        self.rect.reset()   # also frees the small colour pixmaps
        self._job = None
        self._retire(job)
        self._state = IDLE
        self._kbStart()
        for cb in list(self._on_changed):
            try:
                cb()
            except Exception as ex:  # noqa: BLE001
                log(f"imageChanged callback failed: {ex!r}")

    def _dropJob(self):
        job = self._job
        self._job = None
        if self._state != RUNNING:
            self._state = IDLE
        self._timer.stop()
        if job is not None and job.effect is not None:
            job.effect.release()
        self._retire(job)
        if self._layers_ready:
            self.pre.hide()
            self.pre.clear()

    def _recover(self):
        """Something went wrong mid-transition: show the target picture (or keep the current one) and carry on."""
        job = self._job
        try:
            if job is not None and job.pix is not None:
                self._cut(job)
                return
        except Exception as ex:  # noqa: BLE001
            log(f"recover failed: {ex!r}")
        self._dropJob()

    def _reset(self):
        self._kbStop()
        self._timer.stop()
        self._dropJob()
        self._state = IDLE
        self.cur_pix = None
        self.cur_path = None
        if self._layers_ready:
            for lay in (self.s0, self.s1, self.s2, self.ovl):
                lay.clear()
            self.rect.reset()

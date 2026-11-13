# coding=utf-8
# PicAniCockpit - effect logic. Pure Python, no enigma imports, so it is unit-tested on the host with fake layers
# (tests/test_effects.py). An effect never renders a frame: it bakes layer pixmaps ahead of time (jobs), and per frame it
# only issues a handful of widget operations on the layers it is given. Measured limits on the dm900: <= ~8 moves or
# ~10 show/hide per frame, every texture must be uploaded before the transition (see docs/TransitionEngine-design.md 7, 7b).
#
# Layer protocol (implemented by TransitionViewer.Layer, by FakeLayer in the tests):
#     set(pixmap)  geom(x, y, w, h)  show()  hide()
# Layers are stacked bottom to top: s0, s1, s2 (opaque pictures), ovl (alpha-blended pictures), rect (solid colour).

import random
from collections import namedtuple

# ---------------------------------------------------------------------------------------------------------------- ids
EFFECT_CROSSFADE = 100
EFFECT_FADE_BLACK = 101
EFFECT_FADE_WHITE = 102
EFFECT_PUSH = 103
EFFECT_ZOOM_IN = 104
EFFECT_ZOOM_OUT = 105
EFFECT_DOORS_H = 106
EFFECT_DOORS_V = 107
EFFECT_WIPE = 108
EFFECT_WIPE_DIAGONAL = 109
EFFECT_IRIS = 110
EFFECT_BLINDS = 111
EFFECT_BLINDS_H = 112
EFFECT_CHECKERBOARD = 113
EFFECT_DICE = 114
EFFECT_WINDMILL = 115
EFFECT_LUMA = 116
EFFECT_LIGHT_LEAK = 117
EFFECT_CUT = 118        # no transition: the next picture replaces the old one at once (handled by the viewer, not an effect class)
EFFECT_RANDOM = 199

MASK_UNIFORM, MASK_LINEAR, MASK_RADIAL, MASK_BLINDS, MASK_CHECKER, MASK_DICE, MASK_WINDMILL, MASK_LUMA = range(8)

QUALITIES = ("low", "normal", "high")
# (scale, number of alpha levels) per quality. "half" = baked at half resolution and scaled up by the widget (4x less
# memory and upload, softer while the picture is revealed); "full" = 1:1.
# All ladders are baked at half resolution (2 MB per level at 1080p): on a real GUI the GPU (ION) memory is already about 180 MB
# full of skin and thumbnail textures and enigma2 segfaults in the v3d driver ("ION_IOC_ALLOC: Cannot allocate memory") at ~260 MB.
# A full-resolution 12 level ladder (100 MB) crashed it; 12 half-resolution levels cost 24 MB.
# 2 MB per level: normal = 40 MB, high = 50-60 MB of GPU memory while a transition is prepared. A transition runs at 25 fps, so
# 30 levels = one new level every frame for ~1.2 s (no visible steps at all).
Level = namedtuple("Level", "scale count")
DISSOLVE_LEVELS = {"low": Level("half", 10), "normal": Level("half", 20), "high": Level("half", 30)}
MASK_LEVELS = {"low": Level("half", 12), "normal": Level("half", 20), "high": Level("half", 30)}
LEVELS = {"dissolve": DISSOLVE_LEVELS, "mask": MASK_LEVELS}


def easeInOut(t):
    return t * t * (3.0 - 2.0 * t)


def clamp01(t):
    return 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)


class Transition:
    """Base class. The viewer calls, in order: jobs() (one per timer tick), preload(), begin(), frame(t) ..., end()."""
    name = ""
    easing = staticmethod(lambda t: t)
    supports_kenburns = True  # False: needs A as a plain full-screen picture (the viewer substitutes a cross-dissolve)

    def __init__(self, forward=True, quality="normal"):
        self.forward = forward
        self.quality = quality if quality in QUALITIES else "normal"

    def jobs(self, ctx):  # pylint: disable=unused-argument
        """List of zero-argument callables that bake the layers (each should take well under ~40 ms)."""
        return []

    def preload(self, ctx):
        """Pixmaps that must be uploaded to the GPU before begin(), in order. The new picture goes first: end() shows it."""
        return [ctx.b]

    def begin(self, ctx):
        """Set up the layers for t = 0. Must look identical to the picture currently shown."""

    def frame(self, ctx, t):
        """Apply progress t (already eased, 0 < t < 1)."""

    def end(self, ctx):
        """Final state: the new picture alone, full screen, on layer s0. B was preloaded, so there is no upload hitch."""
        ctx.showOnly(ctx.b)

    def release(self):
        """Drop baked pixmaps (the viewer calls this after end())."""


# ----------------------------------------------------------------------------------------------- alpha-ladder family
class Ladder(Transition):
    """K pre-baked copies of picture B with increasing per-pixel alpha (mask of progress), laid over A on layer ovl.
    Per frame: swap one pixmap. Cross-dissolve, wipe and iris are the same effect with a different mask."""
    cls = "mask"
    kind = MASK_UNIFORM
    soft = 0.2
    dx, dy, cx, cy = 1.0, 0.0, 0.5, 0.5
    n = 8        # slats / cell columns / blades of the mask kinds that have them
    seed = 0

    def __init__(self, forward=True, quality="normal"):
        Transition.__init__(self, forward, quality)
        self.scale, self.count = LEVELS[self.cls][self.quality]
        if self.kind == MASK_DICE:
            self.seed = random.randrange(1 << 31)
        self.levels = []
        self.base = None
        self.level_idx = 0

    def direction(self):
        return (self.dx, self.dy) if self.forward else (-self.dx, -self.dy)

    def jobs(self, ctx):
        n = ctx.native
        jobs = []
        if self.scale == "half":
            def halve():
                self.base = n.halve(ctx.b)[0]
            jobs.append(halve)
        else:
            self.base = ctx.b
        dx, dy = self.direction()
        for k in range(1, self.count + 1):
            def bake(k=k):
                self.levels.append(n.bake_mask(self.base, self.kind, k / float(self.count), self.soft, dx, dy, self.cx, self.cy, self.n, self.seed)[0])
            jobs.append(bake)
        return jobs

    def preload(self, ctx):
        return [ctx.b] + self.levels

    def begin(self, ctx):
        self.level_idx = 0
        ctx.ovl.geom(0, 0, ctx.W, ctx.H)
        ctx.ovl.hide()

    def frame(self, ctx, t):
        idx = int(t * self.count + 0.5)
        if idx != self.level_idx:
            self.level_idx = idx
            if idx <= 0:
                ctx.ovl.hide()
            else:
                ctx.ovl.set(self.levels[min(idx, self.count) - 1])
                ctx.ovl.show()

    def release(self):
        self.levels = []
        self.base = None


class Crossfade(Ladder):
    name = "Cross-dissolve"
    cls = "dissolve"
    kind = MASK_UNIFORM


class Wipe(Ladder):
    name = "Wipe"
    kind = MASK_LINEAR
    soft = 0.15


class WipeDiagonal(Wipe):
    name = "Wipe diagonal"
    dx, dy = 1.0, 1.0


class Iris(Ladder):
    name = "Iris"
    kind = MASK_RADIAL
    soft = 0.2
    supports_kenburns = True


class Blinds(Ladder):
    """Vertical slats, each opening from its left edge (right edge when navigating backwards)."""
    name = "Blinds"
    kind = MASK_BLINDS
    soft = 0.3
    n = 10


class BlindsHorizontal(Blinds):
    name = "Blinds horizontal"
    dx, dy = 0.0, 1.0
    n = 8


class Checkerboard(Ladder):
    name = "Checkerboard"
    kind = MASK_CHECKER
    soft = 0.25
    n = 12


class Dice(Ladder):
    name = "Dice"
    kind = MASK_DICE
    soft = 0.15
    n = 16


class Windmill(Ladder):
    name = "Windmill"
    kind = MASK_WINDMILL
    soft = 0.25
    n = 4


class LumaFade(Ladder):
    """B appears where it is darkest first (brightest first when navigating backwards): the picture itself is the mask."""
    name = "Luma fade"
    kind = MASK_LUMA
    soft = 0.3


# ------------------------------------------------------------------------------------------------ geometry family
class Push(Transition):
    """B pushes A out; direction follows the navigation direction. Two widgets moved per frame."""
    name = "Push"
    easing = staticmethod(easeInOut)

    def begin(self, ctx):
        self.ax, self.ay, self.aw, self.ah = ctx.a_geom
        ctx.s1.set(ctx.b)
        ctx.s1.geom(ctx.W if self.forward else -ctx.W, 0, ctx.W, ctx.H)  # parked outside the clipped container
        ctx.s1.show()

    def frame(self, ctx, t):
        off = int(round(ctx.W * t))
        if self.forward:
            ctx.s0.geom(self.ax - off, self.ay, self.aw, self.ah)
            ctx.s1.geom(ctx.W - off, 0, ctx.W, ctx.H)
        else:
            ctx.s0.geom(self.ax + off, self.ay, self.aw, self.ah)
            ctx.s1.geom(off - ctx.W, 0, ctx.W, ctx.H)


class ZoomIn(Transition):
    """B grows from the centre over A."""
    name = "Zoom in"
    easing = staticmethod(easeInOut)
    start = 0.02  # B grows from (almost) nothing, so nothing pops into view at t = 0

    def begin(self, ctx):
        ctx.s1.set(ctx.b)
        self.place(ctx, 0.0)  # geometry only: s1 stays hidden until the first frame, A is still alone on screen

    def place(self, ctx, t):
        f = self.start + (1.0 - self.start) * t
        w, h = max(8, int(ctx.W * f)), max(4, int(ctx.H * f))
        ctx.s1.geom((ctx.W - w) // 2, (ctx.H - h) // 2, w, h)

    def frame(self, ctx, t):
        self.place(ctx, t)
        ctx.s1.show()


class ZoomOut(Transition):
    """A shrinks to the centre and disappears, revealing B."""
    name = "Zoom out"
    easing = staticmethod(easeInOut)
    end_scale = 0.02
    supports_kenburns = True

    def begin(self, ctx):
        self.ax, self.ay, self.aw, self.ah = ctx.a_geom
        ctx.s1.set(ctx.a)       # A moves to the upper layer, B takes over the bottom one (same texture of A: no upload)
        ctx.s1.geom(self.ax, self.ay, self.aw, self.ah)
        ctx.s1.show()
        ctx.s0.set(ctx.b)
        ctx.s0.geom(0, 0, ctx.W, ctx.H)

    def frame(self, ctx, t):
        f = 1.0 + (self.end_scale - 1.0) * t
        w, h = max(8, int(self.aw * f)), max(4, int(self.ah * f))
        cx, cy = self.ax + self.aw // 2, self.ay + self.ah // 2
        ctx.s1.geom(cx - w // 2, cy - h // 2, w, h)


class Doors(Transition):
    """A splits in two halves that slide apart and reveal B (barn doors). The halves are crops of A. With Ken Burns they are cut
    from A as it looks at the END of the zoom (viewer.kbFinalGeom(): the zoom is timed to end when the transition starts), so they
    match what is on screen; if the transition starts early (manual navigation) the picture jumps to the final zoom."""
    name = "Barn doors"
    easing = staticmethod(easeInOut)
    vertical = False

    def __init__(self, forward=True, quality="normal"):
        Transition.__init__(self, forward, quality)
        self.halves = []
        self.src_geom = None    # geometry of A on screen the halves were cut for (None = plain, full screen)

    def screenRects(self, W, H):
        return [(0, 0, W, H // 2), (0, H // 2, W, H - H // 2)] if self.vertical else [(0, 0, W // 2, H), (W // 2, 0, W - W // 2, H)]

    @staticmethod
    def sourceRect(r, fg, W, H):
        """The part of picture A (a W x H canvas) that is visible in screen rectangle r while A is drawn at geometry fg."""
        if fg is None:
            return r
        gx, gy, gw, _ = fg
        z = gw / float(W)
        x0 = max(0, min(W - 1, int(round((r[0] - gx) / z))))
        y0 = max(0, min(H - 1, int(round((r[1] - gy) / z))))
        x1 = max(x0 + 1, min(W, int(round((r[0] + r[2] - gx) / z))))
        y1 = max(y0 + 1, min(H, int(round((r[1] + r[3] - gy) / z))))
        return (x0, y0, x1 - x0, y1 - y0)

    def jobs(self, ctx):
        W, H = ctx.W, ctx.H
        fg = ctx.kbFinalGeom() if hasattr(ctx, "kbFinalGeom") else None
        self.src_geom = fg
        return [(lambda r=r: self.halves.append(ctx.native.crop(ctx.a, *self.sourceRect(r, fg, W, H))[0])) for r in self.screenRects(W, H)]

    def preload(self, ctx):
        return [ctx.b] + self.halves

    def begin(self, ctx):
        W, H = ctx.W, ctx.H
        ctx.s1.set(self.halves[0])
        ctx.s2.set(self.halves[1])
        self.r = self.screenRects(W, H)
        ctx.s1.geom(*self.r[0])
        ctx.s2.geom(*self.r[1])
        ctx.s1.show()
        ctx.s2.show()
        ctx.s0.set(ctx.b)        # B takes over the bottom layer in the same paint as the halves appear: no visible change
        ctx.s0.geom(0, 0, W, H)

    def frame(self, ctx, t):
        if self.vertical:
            d = int(round(ctx.H / 2.0 * t))
            ctx.s1.geom(0, -d, ctx.W, self.r[0][3])
            ctx.s2.geom(0, self.r[1][1] + d, ctx.W, self.r[1][3])
        else:
            d = int(round(ctx.W / 2.0 * t))
            ctx.s1.geom(-d, 0, self.r[0][2], ctx.H)
            ctx.s2.geom(self.r[1][0] + d, 0, self.r[1][2], ctx.H)

    def release(self):
        self.halves = []


class DoorsVertical(Doors):
    name = "Barn doors vertical"
    vertical = True


class Dip(Transition):
    """Fade through a solid colour: A -> colour -> B. One rectangle changes its alpha per frame."""
    name = "Fade"
    color = 0x000000
    peak = 1.0       # opacity of the colour rectangle at the middle of the transition
    swapped = False

    def begin(self, ctx):
        self.swapped = False
        ctx.rect.geom(0, 0, ctx.W, ctx.H)
        ctx.rect.setColor(self.color, 0.0)
        ctx.rect.show()

    def frame(self, ctx, t):
        if t < 0.5:
            ctx.rect.setColor(self.color, t * 2.0 * self.peak)
        else:
            if not self.swapped:
                self.swapped = True
                ctx.showOnly(ctx.b, keep_rect=True)
            ctx.rect.setColor(self.color, (1.0 - t) * 2.0 * self.peak)

    def end(self, ctx):
        ctx.showOnly(ctx.b)


class FadeBlack(Dip):
    name = "Fade to black"
    color = 0x000000


class FadeWhite(Dip):
    name = "Fade to white"
    color = 0xffffff


class LightLeak(Dip):
    """Film burn, approximated: e2 has no additive blending, so a warm translucent rectangle swells and fades around the cut."""
    name = "Light leak"
    color = 0xffa24a
    peak = 0.85


# --------------------------------------------------------------------------------------------------------- registry
EFFECTS = [
    (EFFECT_CROSSFADE, Crossfade),
    (EFFECT_FADE_BLACK, FadeBlack),
    (EFFECT_FADE_WHITE, FadeWhite),
    (EFFECT_PUSH, Push),
    (EFFECT_ZOOM_IN, ZoomIn),
    (EFFECT_ZOOM_OUT, ZoomOut),
    (EFFECT_DOORS_H, Doors),
    (EFFECT_DOORS_V, DoorsVertical),
    (EFFECT_WIPE, Wipe),
    (EFFECT_WIPE_DIAGONAL, WipeDiagonal),
    (EFFECT_IRIS, Iris),
    (EFFECT_BLINDS, Blinds),
    (EFFECT_BLINDS_H, BlindsHorizontal),
    (EFFECT_CHECKERBOARD, Checkerboard),
    (EFFECT_DICE, Dice),
    (EFFECT_WINDMILL, Windmill),
    (EFFECT_LUMA, LumaFade),
    (EFFECT_LIGHT_LEAK, LightLeak),
]
BY_ID = dict(EFFECTS)


def effectChoices():
    """[(id as str, title)] for a ConfigSelection, including the random entry."""
    return [(str(i), c.name) for i, c in EFFECTS] + [(str(EFFECT_RANDOM), "random (new effects)"), (str(EFFECT_CUT), "No transition (cut)")]


def isNativeEffect(effect_id):
    try:
        return int(effect_id) in (EFFECT_RANDOM, EFFECT_CUT) or int(effect_id) in BY_ID
    except (TypeError, ValueError):
        return False


def create(effect_id, forward=True, quality="normal", last_id=None, kenburns=False, rnd=random):
    """Build the transition for effect_id. Random never repeats the last effect. When Ken Burns is on, effects that need A as a
    plain full-screen picture are replaced by a cross-dissolve. Returns (effect_id, Transition)."""
    effect_id = int(effect_id)
    if effect_id == EFFECT_RANDOM or effect_id not in BY_ID:
        pool = [i for i, c in EFFECTS if i != last_id and (not kenburns or c.supports_kenburns)]
        effect_id = rnd.choice(pool or [EFFECT_CROSSFADE])
    if kenburns and not BY_ID[effect_id].supports_kenburns:
        effect_id = EFFECT_CROSSFADE
    return effect_id, BY_ID[effect_id](forward, quality)

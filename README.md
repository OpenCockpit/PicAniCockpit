# PicAniCockpit

Slideshow transition engine for MediaCockpit (dm900 / OpenViX, plugin-only). Pictures and the layers an effect needs are
pre-baked in C++ during the slideshow hold time; a transition itself only moves widgets on the GPU (25 fps, wall-clock paced).
Design and measurements: `MediaCockpit/docs/TransitionEngine-design.md`.

## Layout

| Dir | Content |
|---|---|
| `core/` | pure C++17 kernels, no enigma2 dependency (`Frame`, `bake`, `mask`, `dissolve`) |
| `glue/` | `swigpyrun.h` (generated, SWIG 4.4.1 external runtime, matches enigma's `swig_runtime_data5`) |
| `module/` | Python extension source of the production `_pictrans.so` (`pictrans.cpp`) |
| `src/` | the shipped system plugin: `Effects.py` (effect logic), `TransitionViewer.py` (GUI component), `__init__.py` (`available()`) |
| `tools/PicTransDemo/` | on-box demo / test screen (NOT production, not in the ipk) |
| `tools/PicTransSpike/` | on-box measurement plugin of Spike 0/1 with its own `MDCSpike.cpp` and build/deploy scripts (NOT production, kept as the record of the design measurements) |
| `tests/` | host unit tests (C++ kernels, effect logic with fake layers) |
| `CONTROL/`, `Makefile.am`, `src/Makefile.am`, `scripts/package.sh` | ipk packaging; the Makefile.am files list exactly the files of the ipk (checked with a real `make install` against the ipk contents) |
| `scripts/` | build / deploy helpers (run in WSL Ubuntu-24.04 unless noted) |

## Use from MediaCockpit

MediaCockpit imports `Plugins.SystemPlugins.PicAniCockpit` in a try/except. When `available()` is true (module loads and the
ABI self-test passes) the slideshow uses it; the order is PicAniCockpit > Merlin > ePixmap animation. Setup: slideshow
animation (IDs 100-117, 199 = random), transition duration, transition quality, Ken Burns (slow zoom while a picture is shown).

`scripts/xenv.sh` runs a command inside the cross environment taken from the newest bitbake `run.do_compile.*` of the enigma2 recipe.
Files are written with LF line endings (WSL runs them).

## Limitations
- Tested on OpenViX and OpenATV with DM900.

## Installation
The plugin will be installed as a dependency, no explicit installation required.

# coding=utf-8
# PicAniCockpit - slideshow transitions for enigma2 (OpenViX dm900). MediaCockpit imports this in a try/except, so it keeps
# working without the package. See docs/TransitionEngine-design.md in the MediaCockpit tree.

_state = {"checked": False, "module": None, "text": "not checked"}


HANG_LOG = "/media/usb/pictransitions/hang.log"   # on USB, never /tmp (RAM)


def _installHangDump():
    """Diagnostics for a frozen GUI: `kill -USR2 $(pidof enigma2)` makes faulthandler write the Python stack of every thread to
    HANG_LOG, even when the main thread is stuck inside a C call (GL / ION driver). Does nothing if the folder does not exist."""
    try:
        import faulthandler
        import os
        import signal
        if os.path.isdir(os.path.dirname(HANG_LOG)):
            _state["hangfile"] = open(HANG_LOG, "a", encoding="utf-8")  # pylint: disable=consider-using-with  # must stay open for faulthandler
            faulthandler.register(signal.SIGUSR2, file=_state["hangfile"], all_threads=True)
    except Exception as ex:  # noqa: BLE001
        print(f"[PicAniCockpit] hang dump not installed: {ex!r}")


def native():
    """The native bake module, or None when it is missing or its ABI self-test fails (never raises)."""
    if not _state["checked"]:
        _state["checked"] = True
        _installHangDump()
        try:
            from . import _pictrans  # pylint: disable=import-self
            ok, text = _pictrans.selftest()
            _state["text"] = text
            _state["module"] = _pictrans if ok else None
        except Exception as ex:  # noqa: BLE001 - any failure must just disable the module
            _state["text"] = f"import failed: {ex!r}"
            _state["module"] = None
        print(f"[PicAniCockpit] native module: {'ok' if _state['module'] else 'DISABLED'} ({_state['text']})")
    return _state["module"]


def available():
    return native() is not None


def status():
    native()
    return _state["text"]

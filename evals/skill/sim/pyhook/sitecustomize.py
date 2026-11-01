"""Loaded through PYTHONPATH by the simulated Mac only (never on a real one).

arc2dia2zen's Dia reader refuses to run unless ``sys.platform == "darwin"``. This hook gives the
``arc2dia2zen.dia`` module, and nothing else, a ``sys`` whose ``platform`` is "darwin", so ``verify``
calls ``osascript`` and reaches the sandbox's osascript shim. The rest of Python is untouched.
"""

import importlib.abc
import sys


class _DarwinSys:
    def __init__(self, real):
        object.__setattr__(self, "_real", real)

    def __getattr__(self, name):
        if name == "platform":
            return "darwin"
        return getattr(object.__getattribute__(self, "_real"), name)


class _Loader(importlib.abc.Loader):
    def __init__(self, inner):
        self._inner = inner

    def create_module(self, spec):
        create = getattr(self._inner, "create_module", None)
        return create(spec) if create else None

    def exec_module(self, module):
        if hasattr(self._inner, "exec_module"):
            self._inner.exec_module(module)
        else:  # Python 3.9 zipimporter
            exec(self._inner.get_code(module.__name__), module.__dict__)
        module.sys = _DarwinSys(sys)

    def __getattr__(self, name):
        return getattr(self.__dict__["_inner"], name)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname != "arc2dia2zen.dia":
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(fullname, path, target)
            if spec is not None:
                if spec.loader is not None:
                    spec.loader = _Loader(spec.loader)
                return spec
        return None


sys.meta_path.insert(0, _Finder())

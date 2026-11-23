"""The engine's own source, loaded without a ComfyUI.

The workbench talks to the engine through its nodes and its routes.
Both are declared in ``chain_nodes.py`` - but that file imports
ComfyUI, which is not installed here (and must not be needed to test
the workbench).

So the ComfyUI modules are stubbed and the engine's own file is
imported as it is. Nothing about the engine is re-implemented: the node
definitions and the route table the test checks are the ones the engine
registers.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import pathlib
import sys
import types

#: The repository root: the engine's own package.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The ComfyUI modules the engine's node file imports.
STUB_ROOTS = ("comfy", "comfy_execution", "comfy_api", "node_helpers",
              "folder_paths", "server", "latent_preview", "mathutils")

#: The routes the engine registered while it was imported.
ROUTES: list = []


class _Stub(types.ModuleType):
    """A module whose every attribute is another stub."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Stub("%s.%s" % (self.__name__, name))

    def __call__(self, *args, **kwargs):
        return _Stub(self.__name__ + "()")


class _Routes:
    """The engine's route table, recorded instead of served."""

    def get(self, path):
        ROUTES.append(("GET", path))
        return lambda handler: handler

    def post(self, path):
        ROUTES.append(("POST", path))
        return lambda handler: handler


class _PromptServer:
    instance = types.SimpleNamespace(routes=_Routes())


class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Hands out a stub for every ComfyUI module."""

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] not in STUB_ROOTS:
            return None
        return importlib.machinery.ModuleSpec(name, self)

    def create_module(self, spec):
        module = _Stub(spec.name)
        module.__path__ = []
        return module

    def exec_module(self, module):
        return None


def load_engine():
    """The engine's node module, and the routes it registered.

    The engine is imported under a package name of its own so that its
    relative imports resolve; the files it loads are the ones in this
    repository.
    """
    if "h3_engine_under_test" in sys.modules:
        return sys.modules["h3_engine_under_test.chain_nodes"]

    server = types.ModuleType("server")
    server.PromptServer = _PromptServer
    sys.modules["server"] = server
    sys.meta_path.insert(0, _Finder())

    package = types.ModuleType("h3_engine_under_test")
    package.__path__ = [str(REPO_ROOT)]
    sys.modules["h3_engine_under_test"] = package
    module = importlib.import_module("h3_engine_under_test.chain_nodes")
    return module


def node_class(module, class_type: str):
    """The engine's own node class."""
    mapping = module.CHAIN_NODE_CLASS_MAPPINGS
    assert class_type in mapping, class_type
    return mapping[class_type]


def node_types(module, class_type: str) -> dict:
    """The engine's own input types of one node."""
    return node_class(module, class_type)().INPUT_TYPES()


def known_inputs(types: dict) -> dict:
    """``{name: spec}`` of every input a node accepts."""
    known = {}
    for group in ("required", "optional"):
        for name, spec in (types.get(group) or {}).items():
            known[name] = spec
    return known


def allowed_values(spec) -> list:
    """The values a combo input accepts ([] when it is not a combo)."""
    kind = spec[0] if isinstance(spec, tuple) else spec
    if isinstance(kind, list):
        return list(kind)
    return []

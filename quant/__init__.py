"""Causal, reproducible research tools for the Quant market dataset."""

from importlib import import_module

__version__ = "0.3.0"
__all__ = (
    "data",
    "math",
    "ts",
    "indicators",
    "stats",
    "cs",
    "volatility",
    "microstructure",
    "signal",
    "portfolio",
    "risk",
    "simulator",
    "evaluate",
    "performance",
    "validation",
    "optimize",
    "experiment",
    "features",
    "registry",
    "requests",
    "research",
    "strategy",
    "trading",
)


def __getattr__(name):
    if name in __all__:
        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(name)

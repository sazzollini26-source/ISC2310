"""Autonomous quantitative trading agent.

A dependency-free (standard library only) research and paper-trading stack:

    data -> indicators -> sentiment -> risk -> broker -> journal

Live order routing is deliberately not implemented. See ``broker.LiveBroker``.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]

"""Secure multi-user notes REST service.

The package is organised as a modular monolith following Domain-Driven Design
and Hexagonal (Ports & Adapters) architecture. Dependency direction is strictly
inward::

    presentation -> application -> domain
    infrastructure -> application -> domain
    bootstrap -> presentation + infrastructure + application

``domain`` and ``application`` never import framework, ORM, or transport code.
"""

__all__ = ["__version__"]

__version__ = "1.0.0"

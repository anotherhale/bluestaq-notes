"""Abstract ports (interfaces) required by the application layer.

Each port names a *substitution boundary*: a place where infrastructure can be
swapped without touching domain or application logic. Ports are expressed as
:class:`typing.Protocol` classes so adapters need not inherit from them.
"""

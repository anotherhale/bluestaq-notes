"""Application layer: use cases and the ports they depend on.

The application layer orchestrates domain objects to fulfil use cases. It
depends only on the domain and on abstract ports; concrete adapters are
provided by the infrastructure layer at the composition root.
"""

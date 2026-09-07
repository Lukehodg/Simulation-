"""A local-first personal health data warehouse.

Raw payloads land immutably on disk; everything else is derived from them and
can be rebuilt with `health replay`.
"""

__version__ = "0.1.0"

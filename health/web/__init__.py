"""The local web interface.

Serves one page on 127.0.0.1 from the same database the CLI reads. No
framework, no build step, no network exposure: this is a view onto a DuckDB
file that happens to render in a browser.
"""

from .server import serve

__all__ = ["serve"]

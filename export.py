"""Compatibility command: write Excel/CV ZIP using the v1 archive exporter."""
from zip_export import run
import asyncio

if __name__ == "__main__":
    asyncio.run(run())

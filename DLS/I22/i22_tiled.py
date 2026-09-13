"""Notebook-owned, read-only Tiled service for the I22 transport example."""

from __future__ import annotations

from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from typing import Any

from i22_helpers import DETECTOR_DATASETS, sample_aligned_paths


def _nested_adapter(mapping: dict[str, Any]):
    from tiled.adapters.mapping import MapAdapter

    return MapAdapter({key: _nested_adapter(value) if isinstance(value, dict) else value for key, value in mapping.items()})


def _measurement_adapter(source_path: Path):
    from tiled.adapters.hdf5 import HDF5Adapter

    paths = {"/modacor/calibration/absolute_intensity_factor"}
    for detector in DETECTOR_DATASETS:
        paths.update(sample_aligned_paths(detector))
    tree: dict[str, Any] = {}
    for data_path in sorted(paths):
        cursor = tree
        parts = data_path.strip("/").split("/")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = HDF5Adapter.from_uris(source_path.resolve().as_uri(), dataset=data_path)
    return _nested_adapter(tree)


class LocalI22TiledServer:
    """Expose selected HDF5 leaves through a loopback-only Tiled server."""

    def __init__(self, source_paths, *, host: str = "127.0.0.1", port: int = 8910, startup_timeout: float = 45):
        self.source_paths = tuple(Path(path) for path in source_paths)
        self.host = host
        self.port = int(port)
        self.startup_timeout = float(startup_timeout)
        self.url = f"http://{host}:{port}"
        self.server = None
        self.thread = None

    def start(self) -> str:
        try:
            import uvicorn
            from tiled.adapters.mapping import MapAdapter
            from tiled.client import from_uri
            from tiled.config import Authentication
            from tiled.server.app import build_app
        except ImportError as exc:
            raise RuntimeError("This notebook requires the MoDaCor tiled-tests extra.") from exc

        samples = {path.stem: _measurement_adapter(path) for path in self.source_paths}
        tree = MapAdapter({"samples": MapAdapter(samples)})
        app = build_app(tree, authentication=Authentication(allow_anonymous_access=True))
        self.server = uvicorn.Server(
            uvicorn.Config(app, host=self.host, port=self.port, log_level="warning", access_log=False)
        )
        self.thread = Thread(target=self.server.run, name="i22-notebook-tiled", daemon=True)
        self.thread.start()
        deadline = monotonic() + self.startup_timeout
        while monotonic() < deadline:
            if self.server.started:
                from_uri(self.url)
                return self.url
            if not self.thread.is_alive():
                raise RuntimeError("The notebook-owned Tiled server exited during startup.")
            sleep(0.25)
        self.stop()
        raise TimeoutError(f"Tiled did not start within {self.startup_timeout:g} seconds.")

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=10)
        self.server = None
        self.thread = None

    def __enter__(self) -> str:
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()

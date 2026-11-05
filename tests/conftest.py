from pathlib import Path

import pytest

from orbit.config import Settings
from orbit.pipeline.run import run_pipeline

LIVE = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="session")
def pipeline_dir(tmp_path_factory):
    """A complete pipeline run into an isolated directory (simulated M5 data, ~15 s)."""
    d = tmp_path_factory.mktemp("orbit_data")
    s = Settings(data_dir=d)
    summary = run_pipeline(s)
    return s, summary


@pytest.fixture(scope="session")
def live_settings():
    """The built warehouse + trained models under ./data (requires the build to have been run)."""
    if not (LIVE / "orbit.db").exists():
        pytest.skip("data/orbit.db not built; run the pipeline and training first")
    return Settings(data_dir=LIVE)

import os
import time

import pytest

from disk_sweep.scanner import Candidate


@pytest.fixture
def make_file(tmp_path):
    def create(name="old.tmp", content=b"junk", days=30):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        modified = time.time() - days * 86400
        os.utime(path, (modified, modified))
        return path

    return create


@pytest.fixture
def candidate(tmp_path):
    def create(path):
        return Candidate.from_stat(
            path, tmp_path.resolve(), path.lstat(), "Temporary", "test fixture", time.time()
        )

    return create

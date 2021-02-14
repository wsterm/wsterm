# -*- coding: utf-8 -*-

import tempfile
import time

from wsterm import workspace


async def test_file():
    file_path = tempfile.mkstemp()[1]
    with open(file_path, "wb") as fp:
        fp.write(b"1234567890")
    timestamp = time.time()
    file = workspace.File(file_path)
    assert abs(file.last_modify_time - timestamp) < 0.1
    assert file.size == 10
    assert file.hash == "e807f1fcf82d132f9bb018ca6738a19f"

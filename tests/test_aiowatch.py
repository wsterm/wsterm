# -*- coding: utf-8 -*-

import asyncio
import os
import shutil
import sys
import tempfile

from wsterm import aiowatch


class WatchHandler(object):
    def __init__(self):
        self._log = []

    def get_log_list(self):
        return self._log

    def on_directory_created(self, target):
        self._log.append(("on_directory_created", target))

    def on_directory_removed(self, target):
        self._log.append(("on_directory_removed", target))

    def on_file_created(self, target):
        self._log.append(("on_file_created", target))

    def on_file_modified(self, target):
        self._log.append(("on_file_modified", target))

    def on_file_removed(self, target):
        self._log.append(("on_file_removed", target))

    def on_item_moved(self, src_path, dst_path):
        self._log.append(("on_item_moved", src_path, dst_path))


async def test_aiowatch_simple():
    root_path = tempfile.mkdtemp()
    handler = WatchHandler()
    watcher = aiowatch.AIOWatcher(root_path, handler)
    asyncio.ensure_future(watcher.start())

    await asyncio.sleep(0.1)
    os.mkdir(os.path.join(root_path, "123"))
    await asyncio.sleep(0.1)
    os.rmdir(os.path.join(root_path, "123"))
    await asyncio.sleep(0.1)
    with open(os.path.join(root_path, "xxx.txt"), "w") as fp:
        fp.write("test")
    await asyncio.sleep(0.1)
    os.remove(os.path.join(root_path, "xxx.txt"))
    await asyncio.sleep(0.1)
    log_list = handler.get_log_list()
    assert log_list[0] == ("on_directory_created", "123")
    assert log_list[1] == ("on_directory_removed", "123")
    assert log_list[2] == ("on_file_created", "xxx.txt")
    assert log_list[3] == ("on_file_modified", "xxx.txt")
    assert log_list[4] == ("on_file_removed", "xxx.txt")


async def test_aiowatch_rename():
    root_path = tempfile.mkdtemp()
    handler = WatchHandler()
    watcher = aiowatch.AIOWatcher(root_path, handler)
    asyncio.ensure_future(watcher.start())

    await asyncio.sleep(0.1)
    os.mkdir(os.path.join(root_path, "123"))
    await asyncio.sleep(0.1)
    os.rename(os.path.join(root_path, "123"), os.path.join(root_path, "456"))
    await asyncio.sleep(0.1)
    with open(os.path.join(root_path, "xxx.txt"), "w") as fp:
        fp.write("test")
    await asyncio.sleep(0.1)
    os.rename(os.path.join(root_path, "xxx.txt"), os.path.join(root_path, "yyy.txt"))
    await asyncio.sleep(0.1)

    log_list = handler.get_log_list()
    assert log_list[0] == ("on_directory_created", "123")
    if sys.platform in ("linux", "win32"):
        assert log_list[1] == ("on_item_moved", "123", "456")
        assert log_list[2] == ("on_file_created", "xxx.txt")
        assert log_list[3] == ("on_file_modified", "xxx.txt")
        assert log_list[4] == ("on_item_moved", "xxx.txt", "yyy.txt")
    else:
        assert log_list[1] == ("on_directory_removed", "123")
        assert log_list[2] == ("on_directory_created", "456")
        assert log_list[3] == ("on_file_created", "xxx.txt")
        assert log_list[4] == ("on_file_modified", "xxx.txt")
        assert log_list[5] == ("on_file_removed", "xxx.txt")
        assert log_list[6] == ("on_file_created", "yyy.txt")
        assert log_list[7] == ("on_file_modified", "yyy.txt")


async def test_aiowatch_complex():
    root_path = tempfile.mkdtemp()
    handler = WatchHandler()
    watcher = aiowatch.AIOWatcher(root_path, handler)
    asyncio.ensure_future(watcher.start())

    await asyncio.sleep(0.1)
    os.makedirs(os.path.join(root_path, "123", "456", "789"))
    await asyncio.sleep(0.1)
    with open(os.path.join(root_path, "123", "456", "789", "xxx.txt"), "w") as fp:
        fp.write("test")
    await asyncio.sleep(0.1)
    shutil.rmtree(os.path.join(root_path, "123"))
    await asyncio.sleep(0.1)
    log_list = handler.get_log_list()
    assert log_list[0] == ("on_directory_created", "123")
    assert log_list[1] == ("on_directory_created", "123/456".replace("/", os.path.sep))
    assert log_list[2] == (
        "on_directory_created",
        "123/456/789".replace("/", os.path.sep),
    )
    assert log_list[3] == (
        "on_file_created",
        "123/456/789/xxx.txt".replace("/", os.path.sep),
    )
    assert log_list[4] == (
        "on_file_modified",
        "123/456/789/xxx.txt".replace("/", os.path.sep),
    )
    assert log_list[5] == (
        "on_file_removed",
        "123/456/789/xxx.txt".replace("/", os.path.sep),
    )
    assert log_list[6] == (
        "on_directory_removed",
        "123/456/789".replace("/", os.path.sep),
    )
    assert log_list[7] == ("on_directory_removed", "123/456".replace("/", os.path.sep))
    assert log_list[8] == ("on_directory_removed", "123")

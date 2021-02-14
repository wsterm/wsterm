# -*- coding: utf-8 -*-

"""Watch File System Asynchronously
"""

import asyncio
import sys


class WatcherBackendBase(object):
    def __init__(self, loop=None):
        self._loop = loop or asyncio.get_event_loop()
        self._event_queue = asyncio.Queue()

    def add_dir_watch(self, path):
        raise NotImplementedError()

    def add_watch(self, path):
        raise NotImplementedError()

    async def read_event(self):
        raise NotImplementedError()


class WatchEvent(object):
    DIRECTORY_CREATED = 0
    DIRECTORY_REMOVED = 1
    FILE_CREATED = 2
    FILE_MODIFIED = 3
    FILE_REMOVED = 4
    ITEM_MOVED = 5

    def __init__(self, event, target):
        self._event = event
        self._target = target

    def __str__(self):
        return "<%s object event=%s target=%s at 0x%x>" % (
            self.__class__.__name__,
            self._event,
            self._target,
            id(self),
        )

    @property
    def event(self):
        return self._event

    @property
    def target(self):
        return self._target


class AIOWatcher(object):
    def __init__(self, root_path, handler):
        self._root_path = root_path
        self._backend = self._get_backend()
        self._handler = handler
        self._running = True

    def _get_backend(self):
        if sys.platform == "linux":
            from . import inotify

            return inotify.INotifyWatcher()
        elif sys.platform == "win32":
            from . import win32

            return win32.Win32Watcher()
        elif sys.platform == "darwin":
            from . import kevents

            return kevents.KEventsWatcher()
        else:
            raise NotImplementedError(sys.platform)

    def add_dir_watch(self, path):
        self._backend.add_dir_watch(path)

    def add_watch(self, path):
        self._backend.add_watch(path)

    async def start(self):
        self.add_dir_watch(self._root_path)
        while self._running:
            event = await self._backend.read_event()
            if isinstance(event.target, str):
                target = event.target[len(self._root_path) + 1 :]
            else:
                target = (
                    event.target[0][len(self._root_path) + 1 :],
                    event.target[1][len(self._root_path) + 1 :],
                )

            if event.event == WatchEvent.DIRECTORY_CREATED:
                self._handler.on_directory_created(target)
            elif event.event == WatchEvent.DIRECTORY_REMOVED:
                self._handler.on_directory_removed(target)
            elif event.event == WatchEvent.FILE_CREATED:
                self._handler.on_file_created(target)
            elif event.event == WatchEvent.FILE_MODIFIED:
                self._handler.on_file_modified(target)
            elif event.event == WatchEvent.FILE_REMOVED:
                self._handler.on_file_removed(target)
            elif event.event == WatchEvent.ITEM_MOVED:
                self._handler.on_item_moved(*target)
            else:
                raise NotImplementedError(event.event)

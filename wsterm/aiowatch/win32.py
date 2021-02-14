# -*- coding: utf-8 -*-

import asyncio
import ctypes
import os

import pywintypes
import win32con
import win32event
import win32file

from . import WatcherBackendBase, WatchEvent

FILE_LIST_DIRECTORY = 1
FILE_ACTION_ADDED = 1
FILE_ACTION_REMOVED = 2
FILE_ACTION_MODIFIED = 3
FILE_ACTION_RENAMED_OLD_NAME = 4
FILE_ACTION_RENAMED_NEW_NAME = 5


class EnumWatchType(object):

    WATCH_DIRECTORY = 1
    WATCH_FILE = 2


class Win32Watcher(WatcherBackendBase):
    def __init__(self, loop=None):
        super(Win32Watcher, self).__init__(loop)
        self._watch_list = []
        asyncio.ensure_future(self.polling_task())
        self._dir_tree = {}

    def _get_dir_node(self, path, auto_create=False):
        path = path.replace(":\\\\", "\\")
        items = path.split("\\")
        node = self._dir_tree
        for it in items:
            if it not in node:
                if auto_create:
                    node[it] = {}
                else:
                    return None
            node = node[it]
        return node

    def _snapshot(self, path):
        assert os.path.isdir(path)
        node = self._get_dir_node(path, True)
        items = os.listdir(path)
        for it in items:
            if it not in node:
                subpath = os.path.join(path, it)
                if os.path.isdir(subpath):
                    node[it] = {}
                    self._snapshot(subpath)
                elif os.path.isfile(subpath):
                    node[it] = None

    def _create_watch_handle(self, path):
        return win32file.CreateFile(
            path,
            FILE_LIST_DIRECTORY,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
            None,
            win32con.OPEN_EXISTING,
            win32con.FILE_FLAG_BACKUP_SEMANTICS | win32con.FILE_FLAG_OVERLAPPED,
            None,
        )

    def _add_file_watch(self, handle, buffer, ov):
        win32file.ReadDirectoryChangesW(
            handle,
            buffer,
            True,
            win32con.FILE_NOTIFY_CHANGE_FILE_NAME
            | win32con.FILE_NOTIFY_CHANGE_ATTRIBUTES
            | win32con.FILE_NOTIFY_CHANGE_SIZE
            | win32con.FILE_NOTIFY_CHANGE_LAST_WRITE
            | win32con.FILE_NOTIFY_CHANGE_SECURITY,
            ov,
        )

    def _add_dir_watch(self, handle, buffer, ov):
        win32file.ReadDirectoryChangesW(
            handle, buffer, True, win32con.FILE_NOTIFY_CHANGE_DIR_NAME, ov,
        )

    def add_dir_watch(self, path):
        # Add directory watch
        self._snapshot(path)
        handle_dir = self._create_watch_handle(path)
        ov_dir = pywintypes.OVERLAPPED()
        ov_dir.hEvent = win32event.CreateEvent(None, 0, 0, None)
        buffer_dir = win32file.AllocateReadBuffer(8192)
        self._add_dir_watch(handle_dir, buffer_dir, ov_dir)
        self._watch_list.append(
            (EnumWatchType.WATCH_DIRECTORY, path, handle_dir, ov_dir, buffer_dir)
        )

        # Add file watch
        handle_file = self._create_watch_handle(path)
        ov_file = pywintypes.OVERLAPPED()
        ov_file.hEvent = win32event.CreateEvent(None, 0, 0, None)
        buffer_file = win32file.AllocateReadBuffer(8192)
        self._add_file_watch(handle_file, buffer_file, ov_file)
        self._watch_list.append(
            (EnumWatchType.WATCH_FILE, path, handle_file, ov_file, buffer_file)
        )

    async def polling_task(self):
        while True:
            for item in self._watch_list:
                try:
                    length = win32file.GetOverlappedResult(item[2], item[3], 0)
                except pywintypes.error as e:
                    if e.args[0] == 996:
                        # Overlapped I/O event is not in a signaled state
                        continue
                    else:
                        raise e
                else:
                    for action, name in win32file.FILE_NOTIFY_INFORMATION(
                        item[-1], length
                    ):
                        target = os.path.join(item[1], name)
                        if action == FILE_ACTION_MODIFIED and not os.path.isfile(
                            target
                        ):
                            # Ignore directory modify event
                            continue
                        elif action == FILE_ACTION_MODIFIED and os.path.isfile(target):
                            dir_node = self._get_dir_node(item[1])
                            if name not in dir_node:
                                # Auto insert file create event
                                self._event_queue.put_nowait(
                                    (target, item[0], FILE_ACTION_ADDED)
                                )
                                dir_node[name] = None
                        elif action == FILE_ACTION_REMOVED:
                            path = target
                            remove_root = target
                            while not os.path.exists(path):
                                # Get remove root
                                remove_root = path
                                path = os.path.dirname(path)

                            if self._get_dir_node(remove_root):
                                # Insert remove sub dirs/files event
                                def _handle_sub_dir(path):
                                    dir_node = self._get_dir_node(path)
                                    for it in dir_node:
                                        if isinstance(dir_node[it], dict):
                                            if dir_node[it]:
                                                _handle_sub_dir(os.path.join(path, it))
                                            self._event_queue.put_nowait(
                                                (
                                                    os.path.join(path, it),
                                                    EnumWatchType.WATCH_DIRECTORY,
                                                    action,
                                                )
                                            )
                                        else:
                                            self._event_queue.put_nowait(
                                                (
                                                    os.path.join(path, it),
                                                    EnumWatchType.WATCH_FILE,
                                                    action,
                                                )
                                            )

                                _handle_sub_dir(remove_root)
                                self._event_queue.put_nowait(
                                    (
                                        remove_root,
                                        EnumWatchType.WATCH_DIRECTORY,
                                        action,
                                    )
                                )
                                parent_dir_node = self._get_dir_node(
                                    os.path.dirname(remove_root)
                                )
                                parent_dir_node.pop(os.path.split(remove_root)[-1])
                                continue

                        self._event_queue.put_nowait((target, item[0], action))
                        if action == FILE_ACTION_ADDED and os.path.isdir(target):
                            # Handle mkdir -p
                            def _handle_sub_dir(path):
                                dir_node = self._get_dir_node(path, True)
                                for it in os.listdir(path):
                                    if it in dir_node:
                                        continue
                                    subpath = os.path.join(path, it)
                                    if os.path.isdir(subpath):
                                        watch_type = EnumWatchType.WATCH_DIRECTORY
                                        dir_node[it] = {}
                                    else:
                                        watch_type = EnumWatchType.WATCH_FILE
                                        dir_node[it] = None

                                    self._event_queue.put_nowait(
                                        (subpath, watch_type, action)
                                    )

                                    if os.path.isdir(subpath):
                                        _handle_sub_dir(subpath)

                            dir_node = self._get_dir_node(item[1])
                            dir_node[name] = {}
                            _handle_sub_dir(target)
                        elif action == FILE_ACTION_ADDED and os.path.isfile(target):
                            # Auto fire modify event
                            self._event_queue.put_nowait(
                                (target, item[0], FILE_ACTION_MODIFIED)
                            )
                            dir_node = self._get_dir_node(os.path.dirname(target))
                            dir_node[os.path.split(target)[-1]] = None

                    # Continue to listen
                    self._add_dir_watch(item[2], item[4], item[3])
                    self._add_file_watch(item[2], item[4], item[3])

            await asyncio.sleep(0.005)

    async def read_event(self):
        move_from = None
        while True:
            target, watch_type, action = await self._event_queue.get()
            isdir = watch_type == EnumWatchType.WATCH_DIRECTORY
            if action == FILE_ACTION_ADDED:
                if isdir:
                    return WatchEvent(WatchEvent.DIRECTORY_CREATED, target)
                else:
                    return WatchEvent(WatchEvent.FILE_CREATED, target)
            elif action == FILE_ACTION_MODIFIED:
                if not isdir:
                    return WatchEvent(WatchEvent.FILE_MODIFIED, target)
            elif action == FILE_ACTION_REMOVED:
                if isdir:
                    return WatchEvent(WatchEvent.DIRECTORY_REMOVED, target)
                else:
                    return WatchEvent(WatchEvent.FILE_REMOVED, target)
            elif action == FILE_ACTION_RENAMED_OLD_NAME:
                move_from = target
            elif action == FILE_ACTION_RENAMED_NEW_NAME:
                return WatchEvent(WatchEvent.ITEM_MOVED, (move_from, target))

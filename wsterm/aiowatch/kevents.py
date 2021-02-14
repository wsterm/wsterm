# -*- coding: utf-8 -*-

import asyncio
import os
import select

from . import WatcherBackendBase, WatchEvent


O_EVTONLY = 0x8000


class KEventsWatcher(WatcherBackendBase):
    def __init__(self, loop=None):
        super(KEventsWatcher, self).__init__(loop)
        self._kq = select.kqueue()
        self._watch_list = {}
        self._dir_tree = {}
        asyncio.ensure_future(self.polling_task())

    def _get_dir_node(self, path, auto_create=False):
        if path[0] == "/":
            path = path[1:]
        items = path.split("/")
        node = self._dir_tree
        for it in items:
            if it not in node:
                if auto_create:
                    node[it] = {}
                else:
                    return None
            node = node[it]
        return node

    def _get_dir_new_item(self, path):
        result = []
        node = self._get_dir_node(path, True)
        items = os.listdir(path)
        for it in items:
            if it not in node:
                subpath = os.path.join(path, it)
                if os.path.isdir(subpath):
                    node[it] = {}
                    result.append(WatchEvent(WatchEvent.DIRECTORY_CREATED, subpath))
                    result.extend(self._get_dir_new_item(subpath))
                elif os.path.isfile(subpath):
                    node[it] = None
                    result.append(WatchEvent(WatchEvent.FILE_CREATED, subpath))

        return result

    def add_dir_watch(self, path):
        assert os.path.isdir(path)
        self.add_watch(path)
        node = self._get_dir_node(path, True)
        for it in os.listdir(path):
            subpath = os.path.join(path, it)
            if os.path.isdir(subpath):
                node[it] = {}
                self.add_dir_watch(subpath)
            elif os.path.isfile(subpath):
                node[it] = None
                self.add_watch(subpath)

    def add_watch(self, path):
        fd = os.open(path, O_EVTONLY)
        event = select.kevent(
            fd,
            filter=select.KQ_FILTER_VNODE,
            flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_CLEAR,
            fflags=select.KQ_NOTE_DELETE
            | select.KQ_NOTE_WRITE
            | select.KQ_NOTE_EXTEND
            | select.KQ_NOTE_ATTRIB
            | select.KQ_NOTE_LINK
            | select.KQ_NOTE_RENAME
            | select.KQ_NOTE_REVOKE,
        )
        self._watch_list[event.ident] = (path, event, fd)

    def remove_watch(self, path):
        for evt_id in self._watch_list:
            watch_path, _, fd = self._watch_list[evt_id]
            if path == watch_path:
                event = select.kevent(
                    fd,
                    filter=select.KQ_FILTER_VNODE,
                    flags=select.KQ_EV_DELETE,
                )
                # self._kq.control((event,), 0)
                self._watch_list.pop(evt_id)
                return

    async def polling_task(self):
        while True:
            events = [event for _, event, _ in self._watch_list.values()]
            events = list(self._kq.control(events, 4096, 0))
            if not events:
                await asyncio.sleep(0.005)
                continue
            events.sort(
                key=lambda event: len(self._watch_list[event.ident][0].split("/")),
                reverse=True,
            )  # Ensure remove inner items first when remove directory

            for event in events:
                target, _, _ = self._watch_list[event.ident]
                if not os.path.exists(target):
                    # Item removed
                    path = target
                    remove_root = target
                    while not os.path.exists(path):
                        # Get remove root
                        remove_root = path
                        path = os.path.dirname(path)

                    if self._get_dir_node(remove_root) is not None:
                        # Insert remove sub dirs/files event
                        def _handle_sub_dir(path):
                            dir_node = self._get_dir_node(path)
                            for it in dir_node:
                                if isinstance(dir_node[it], dict):
                                    if dir_node[it]:
                                        _handle_sub_dir(os.path.join(path, it))
                                    self._event_queue.put_nowait(
                                        WatchEvent(
                                            WatchEvent.DIRECTORY_REMOVED,
                                            os.path.join(path, it),
                                        )
                                    )
                                else:
                                    self._event_queue.put_nowait(
                                        WatchEvent(
                                            WatchEvent.FILE_REMOVED,
                                            os.path.join(path, it),
                                        )
                                    )

                        _handle_sub_dir(remove_root)
                        self._event_queue.put_nowait(
                            WatchEvent(WatchEvent.DIRECTORY_REMOVED, remove_root)
                        )
                    else:
                        self._event_queue.put_nowait(
                            WatchEvent(WatchEvent.FILE_REMOVED, remove_root)
                        )

                    parent = self._get_dir_node(os.path.dirname(target))
                    parent.pop(os.path.split(target)[-1])
                    self.remove_watch(target)
                    continue
                elif os.path.isdir(target):
                    new_events = self._get_dir_new_item(target)
                    for event in new_events:
                        self._event_queue.put_nowait(event)
                        if event.event == WatchEvent.FILE_CREATED:
                            self.add_watch(event.target)
                            self._event_queue.put_nowait(
                                WatchEvent(WatchEvent.FILE_MODIFIED, event.target)
                            )
                        elif event.event == WatchEvent.DIRECTORY_CREATED:
                            self.add_dir_watch(event.target)
                    continue
                else:
                    event = WatchEvent(WatchEvent.FILE_MODIFIED, target)

                self._event_queue.put_nowait(event)

    async def read_event(self):
        return await self._event_queue.get()

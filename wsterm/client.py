# -*- coding: utf-8 -*-

"""Websocket Terminal Client
"""

import asyncio
import os
import socket
import sys
import time

import tornado.httputil
import tornado.websocket

from . import proto, utils, workspace


class WSTerminalConnection(tornado.websocket.WebSocketClientConnection):
    def __init__(self, url, headers=None, timeout=15, handler=None):
        self._url = url
        self.__timeout = timeout
        self._handler = handler
        self._connected = None
        self._closed = False
        compression_options = None
        if isinstance(headers, dict):
            headers = tornado.httputil.HTTPHeaders(headers)
        request = tornado.httpclient.HTTPRequest(
            self._url, headers=headers, connect_timeout=timeout, request_timeout=timeout
        )
        request = tornado.httpclient._RequestProxy(
            request, tornado.httpclient.HTTPRequest._DEFAULTS
        )
        super(WSTerminalConnection, self).__init__(
            request,
            on_message_callback=self.on_message,
            compression_options=compression_options,
        )
        self._buffer = b""
        self._queue = asyncio.Queue()
        self._rsp_map = {}
        self._read_event = asyncio.Event()
        self._sequence = 0

    async def headers_received(self, start_line, headers):
        await super(WSTerminalConnection, self).headers_received(start_line, headers)
        if start_line.code != 101:
            utils.logger.error(
                "[%s] Connect %s return %d"
                % (self.__class__.__name__, self._url, start_line.code)
            )
            self._connected = False
        else:
            self._connected = True

    async def wait_for_connecting(self):
        time0 = time.time()
        while time.time() - time0 < self.__timeout:
            if self._connected == None:
                await asyncio.sleep(0.005)
                continue
            asyncio.ensure_future(self.polling_packet_task())
            return self._connected
        else:
            utils.logger.warn(
                "[%s] Connect %s timeout" % (self.__class__.__name__, self._url)
            )
            return False

    def on_message(self, message):
        if not message:
            self._closed = True
        else:
            self._buffer += message
            packet, self._buffer = proto.TransportPacket.deserialize(self._buffer)
            if packet:
                self._queue.put_nowait(packet.message)

    async def polling_packet_task(self):
        while not self._closed:
            packet = await self._queue.get()
            if packet["type"] == proto.EnumPacketType.REQUEST:
                await self.handle_request(packet)
            elif packet["type"] == proto.EnumPacketType.RESPONSE:
                await self.handle_response(packet)

    async def handle_request(self, request):
        if request["command"] == proto.EnumCommand.WRITE_STDOUT:
            sys.stdout.buffer.write(request["buffer"])
            sys.stdout.flush()
        elif request["command"] == proto.EnumCommand.SHELL_EXIT:
            if self._handler:
                self._handler.on_shell_exit()
        else:
            raise NotImplementedError(request["command"])

    async def handle_response(self, response):
        self._rsp_map[response["id"]] = response

    async def read_response(self, request, timeout=None):
        time0 = time.time()
        while not timeout or time.time() - time0 < timeout:
            if request["id"] in self._rsp_map:
                return self._rsp_map.pop(request["id"])
            await asyncio.sleep(0.005)
        return None

    async def send_request(self, command, **kwargs):
        self._sequence += 1
        data = {
            "command": command,
            "type": proto.EnumPacketType.REQUEST,
            "id": self._sequence,
        }
        data.update(kwargs)
        packet = proto.TransportPacket(data)
        await self.write_message(packet.serialize(), True)
        return data

    async def send_response(self, request, **kwargs):
        data = {
            "command": request["command"],
            "type": proto.EnumPacketType.RESPONSE,
            "id": request["id"],
        }
        data.update(kwargs)
        packet = proto.TransportPacket(data)
        return await self.write_message(packet.serialize(), True)


class WSTerminalClient(object):
    """Websocket Teminal Client"""

    def __init__(self, url, token=None, timeout=15, loop=None):
        headers = {}
        if token:
            headers["Authorization"] = "Token %s" % token
        self._conn = WSTerminalConnection(url, headers, timeout, handler=self)
        self._loop = loop or asyncio.get_event_loop()
        self._workspace = None
        self._shell_stdin = None
        self._running = True

    async def connect(self):
        if not await self._conn.wait_for_connecting():
            raise utils.ConnectWebsocketServerFailed()

    async def create_directory(self, dir_path):
        await self._conn.send_request(proto.EnumCommand.CREATE_DIR, path=dir_path)

    async def remove_directory(self, dir_path):
        await self._conn.send_request(proto.EnumCommand.REMOVE_DIR, path=dir_path)

    async def update_workspace(self, dir_tree, root):
        assert "dirs" in dir_tree or "files" in dir_tree
        for name in dir_tree.get("dirs", {}):
            path = (root + "/" + name) if root else name
            if not dir_tree["dirs"][name]:
                # Blank directory
                await self.create_directory(path)
            elif dir_tree["dirs"][name] == "-":
                await self.remove_directory(path)
            else:
                await self.update_workspace(
                    dir_tree["dirs"][name], path,
                )
        for name in dir_tree.get("files", {}):
            path = (root + "/" + name) if root else name
            utils.logger.debug("[%s] Update file %s" % (self.__class__.__name__, path))
            if dir_tree["files"][name] == "-":
                # Remove file
                await self.remove_file(path)
            else:
                await self.write_file(path)

    async def write_file(self, file_path):
        with open(self._workspace.join_path(file_path), "rb") as fp:
            data = fp.read()
            await self._conn.send_request(
                proto.EnumCommand.WRITE_FILE, path=file_path, data=data
            )

    async def remove_file(self, file_path):
        await self._conn.send_request(proto.EnumCommand.REMOVE_FILE, path=file_path)

    async def on_directory_created(self, path):
        utils.logger.info("[%s] Create directory %s" % (self.__class__.__name__, path))
        await self.create_directory(path)

    async def on_directory_removed(self, path):
        utils.logger.info("[%s] Remove directory %s" % (self.__class__.__name__, path))
        await self.remove_directory(path)

    async def on_file_created(self, path):
        pass

    async def on_file_modified(self, path):
        utils.logger.info("[%s] Write file %s" % (self.__class__.__name__, path))
        await self.write_file(path)

    async def on_file_removed(self, path):
        utils.logger.info("[%s] Remove file %s" % (self.__class__.__name__, path))
        await self.remove_file(path)

    async def sync_workspace(self, workspace_path):
        if not os.path.isdir(workspace_path):
            raise RuntimeError("Workspace %s not exist" % workspace_path)
        self._workspace = workspace.Workspace(workspace_path)
        workspace_hash = utils.make_short_hash(
            "%s%s" % (socket.gethostname(), workspace_path)
        )
        request = await self._conn.send_request(
            proto.EnumCommand.SYNC_WORKSPACE, workspace=workspace_hash,
        )
        response = await self._conn.read_response(request)
        diff_result = self._workspace.make_diff(response["data"])
        if diff_result:
            await self.update_workspace(diff_result, "")
        self._workspace.register_handler(self)
        asyncio.ensure_future(self._workspace.watch())

    async def write_shell_stdin(self, buffer):
        await self._conn.send_request(proto.EnumCommand.WRITE_STDIN, buffer=buffer)

    def on_shell_exit(self):
        if self._shell_stdin:
            self._loop.remove_reader(self._shell_stdin)
            self._shell_stdin = None
            self._loop.stop()

    async def create_shell(self, size):
        request = await self._conn.send_request(
            proto.EnumCommand.CREATE_SHELL, size=size,
        )
        response = await self._conn.read_response(request)
        if response["code"]:
            raise RuntimeError("Create shell failed: %s" % response["message"])
        if sys.platform != "win32":
            self._shell_stdin = utils.StdIn()

            def on_input():
                char = self._shell_stdin.read(1)
                asyncio.ensure_future(self.write_shell_stdin(char))

            self._loop.add_reader(self._shell_stdin, on_input)
        else:
            import msvcrt

            while self._running:
                if msvcrt.kbhit():
                    char = msvcrt.getch()
                    if char == b"\xe0":
                        char = msvcrt.getch()
                        if char == b"H":
                            char = b"\x1b[A"
                        elif char == b"P":
                            char = b"\x1b[B"
                        elif char == b"K":
                            char = b"\x1b[D"
                        elif char == b"M":
                            char = b"\x1b[C"
                        else:
                            utils.logger.warn(
                                r"[%s] Unknown input key \xe0%s"
                                % (self.__class__.__name__, char)
                            )
                    # if char == b"\r":
                    #     char += b"\n"
                    asyncio.ensure_future(self.write_shell_stdin(char))
                else:
                    await asyncio.sleep(0.005)


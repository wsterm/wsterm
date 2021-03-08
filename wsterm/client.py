# -*- coding: utf-8 -*-

"""Websocket Terminal Client
"""

import asyncio
import os
import shutil
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
            if self._connected:
                asyncio.ensure_future(self.polling_packet_task())
                return True
            else:
                return False
        raise utils.ConnectWebsocketServerFailed("Connect %s timeout" % self._url)

    def on_message(self, message):
        if not message:
            self._closed = True
        else:
            self._buffer += message
            packet, self._buffer = proto.TransportPacket.deserialize(self._buffer)
            if packet:
                self._queue.put_nowait(packet.message)

    def on_connection_close(self):
        self._closed = True
        if self._handler:
            self._handler.on_connection_close()

    async def polling_packet_task(self):
        while not self._closed:
            if self._queue.empty():
                # Ensure the task exit in time
                await asyncio.sleep(0.005)
                continue
            packet = await self._queue.get()
            if packet["type"] == proto.EnumPacketType.REQUEST:
                await self.handle_request(packet)
            elif packet["type"] == proto.EnumPacketType.RESPONSE:
                await self.handle_response(packet)

    async def handle_request(self, request):
        if request["command"] == proto.EnumCommand.WRITE_STDOUT:
            if self._handler:
                await self._handler.on_shell_stdout(request["buffer"])
        elif request["command"] == proto.EnumCommand.EXIT_SHELL:
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

    session_timeout = 10 * 60
    file_fragment_size = 4 * 1024 * 1024

    def __init__(self, url, token=None, timeout=15, loop=None, auto_reconnect=False):
        self._url = url
        self._headers = {"Proxy-Connection": "Keep-Alive"}
        if token:
            self._headers["Authorization"] = "Token %s" % token
        self._timeout = timeout
        self._conn = WSTerminalConnection(
            self._url, self._headers, timeout, handler=self
        )
        self._loop = loop or asyncio.get_event_loop()
        self._auto_reconnect = auto_reconnect
        self._session_id = None
        self._workspace = None
        self._running = False
        self._download_file = {}

    @property
    def auto_reconnect(self):
        return self._auto_reconnect

    def on_connection_close(self):
        utils.logger.warn("[%s] Websocket connection closed" % self.__class__.__name__)
        self._running = False
        if not self._auto_reconnect:
            self.on_shell_exit()
        else:
            self._conn = WSTerminalConnection(
                self._url, self._headers, self._timeout, handler=self
            )

    async def connect(self):
        self._running = True
        return await self._conn.wait_for_connecting()

    async def create_directory(self, dir_path):
        utils.logger.info(
            "[%s] Create directory %s" % (self.__class__.__name__, dir_path)
        )
        await self._conn.send_request(
            proto.EnumCommand.CREATE_DIR, path=dir_path.replace(os.path.sep, "/")
        )

    async def remove_directory(self, dir_path):
        utils.logger.info(
            "[%s] Remove directory %s" % (self.__class__.__name__, dir_path)
        )
        await self._conn.send_request(
            proto.EnumCommand.REMOVE_DIR, path=dir_path.replace(os.path.sep, "/")
        )

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
            if dir_tree["files"][name] == "-":
                # Remove file
                await self.remove_file(path)
            else:
                await self.write_file(path)

    async def write_file(self, file_path):
        utils.logger.debug("[%s] Write file %s" % (self.__class__.__name__, file_path))
        offset = 0
        with open(self._workspace.join_path(file_path), "rb") as fp:
            while True:
                data = fp.read(self.file_fragment_size)
                if not data:
                    break
                await self._conn.send_request(
                    proto.EnumCommand.WRITE_FILE,
                    path=file_path.replace(os.path.sep, "/"),
                    data=data,
                    overwrite=offset == 0,
                )
                offset += len(data)

    async def remove_file(self, file_path):
        utils.logger.info("[%s] Remove file %s" % (self.__class__.__name__, file_path))
        await self._conn.send_request(
            proto.EnumCommand.REMOVE_FILE, path=file_path.replace(os.path.sep, "/")
        )

    async def move_item(self, src_path, dst_path):
        utils.logger.info(
            "[%s] Move item %s to %s" % (self.__class__.__name__, src_path, dst_path)
        )
        await self._conn.send_request(
            proto.EnumCommand.MOVE_ITEM,
            src_path=src_path.replace(os.path.sep, "/"),
            dst_path=dst_path.replace(os.path.sep, "/"),
        )

    async def on_directory_created(self, path):
        await self.create_directory(path)

    async def on_directory_removed(self, path):
        await self.remove_directory(path)

    async def on_file_created(self, path):
        pass

    async def on_file_modified(self, path):
        await self.write_file(path)

    async def on_file_removed(self, path):
        await self.remove_file(path)

    async def on_item_moved(self, src_path, dst_path):
        await self.move_item(src_path, dst_path)

    async def sync_workspace(self, workspace_path):
        if not os.path.isdir(workspace_path):
            raise RuntimeError("Workspace %s not exist" % workspace_path)
        self._workspace = workspace.Workspace(workspace_path)
        workspace_hash = utils.make_short_hash(
            "%s%s" % (socket.gethostname(), workspace_path)
        )
        workspace_name = "%s-%s@%s" % (
            os.path.split(workspace_path)[-1],
            workspace_hash,
            socket.gethostname(),
        )
        request = await self._conn.send_request(
            proto.EnumCommand.SYNC_WORKSPACE, workspace=workspace_name,
        )
        response = await self._conn.read_response(request)
        diff_result = self._workspace.make_diff(response["data"])
        if diff_result:
            await self.update_workspace(diff_result, "")
        self._workspace.register_handler(self)
        asyncio.ensure_future(self._workspace.watch())

    async def write_shell_stdin(self, buffer):
        try:
            await self._conn.send_request(proto.EnumCommand.WRITE_STDIN, buffer=buffer)
        except tornado.websocket.WebSocketClosedError:
            utils.logger.error(
                "[%s] Websocket connection lost" % self.__class__.__name__
            )
            self.on_connection_close()

    async def on_shell_stdout(self, buffer):
        if buffer.endswith(b"**\x18B00000000000000\r\x8a\x11"):
            sys.stdout.buffer.write(
                b"Starting zmodem transfer.  Press Ctrl+C to cancel.\n"
            )
            sys.stdout.flush()
            buffer = b"**\x18B01000000039a32\n\n"
            await self._conn.send_request(proto.EnumCommand.WRITE_STDIN, buffer=buffer)
        elif buffer.startswith(b"*\x18A\x04\x00\x00\x00\x00\x89\x06"):
            pos = buffer.find(b"\x00", 10)
            self._download_file["name"] = buffer[10:pos].decode()
            pos2 = buffer.find(b" ", pos)
            self._download_file["size"] = int(buffer[pos + 1 : pos2])
            self._download_file["buffer"] = b""
            sys.stdout.buffer.write(
                b"Transferring %s...\n" % self._download_file["name"].encode()
            )
            buffer = b"**\x18B0900000000a87c\n\n"
            await self._conn.send_request(proto.EnumCommand.WRITE_STDIN, buffer=buffer)
        elif self._download_file.get("name"):
            # download file

            if buffer.startswith(b"*\x18A\n\x00\x00\x00\x00F\xae"):
                pos = buffer.find(b"\x18h", 10)
                if pos > 0:
                    buffer = buffer[10 : pos + 1]
                else:
                    buffer = buffer[10:]
                self._download_file["buffer"] += buffer
            else:
                self._download_file["buffer"] += buffer

            sys.stdout.buffer.write(
                b"\r%d/%d"
                % (len(self._download_file["buffer"]), self._download_file["size"])
            )
            if (
                len(self._download_file["buffer"]) >= self._download_file["size"]
                and b"\x18h" in buffer
            ):
                # last frame received
                pos = buffer.rfind(b"\x18h")
                if pos > 0:
                    self._download_file["buffer"] = self._download_file["buffer"][
                        : pos - len(buffer)
                    ]
                offset = 0
                buffer = b""
                while offset < len(self._download_file["buffer"]):
                    pos = self._download_file["buffer"].find(b"\x18i", offset)
                    if pos > 0:
                        buff = self._download_file["buffer"][offset:pos]
                    else:
                        buff = self._download_file["buffer"][offset:]

                    buffer += buff
                    offset += len(buff)
                    offset += 2  # \x18i
                    index = 0
                    while index < 2 and offset + index < len(
                        self._download_file["buffer"]
                    ):
                        # ignore \x18 char
                        if self._download_file["buffer"][offset + index] == 0x18:
                            offset += 1
                        else:
                            index += 1
                    offset += 2

                mapping_table = {
                    b"\x18\x4d": b"\x0d",
                    b"\x18\x50": b"\x10",
                    b"\x18\x51": b"\x11",
                    b"\x18\x53": b"\x13",
                    b"\x18\xcd": b"\x8d",
                    b"\x18\xd0": b"\x90",
                    b"\x18\xd1": b"\x91",
                    b"\x18\xd3": b"\x93",
                    b"\x18\x58": b"\x18",
                }
                for key in mapping_table:
                    buffer = buffer.replace(key, mapping_table[key])

                with open(self._download_file["name"], "wb") as fp:
                    fp.write(buffer)

                self._download_file = {}
                buffer = b"**\x18B0800000000022d\n\n"
                await self._conn.send_request(
                    proto.EnumCommand.WRITE_STDIN, buffer=buffer
                )
        else:
            sys.stdout.buffer.write(buffer)
            sys.stdout.flush()

    def on_shell_exit(self):
        self._running = False
        self._auto_reconnect = False

        async def exit_loop():
            await asyncio.sleep(0.1)
            self._loop.stop()

        asyncio.ensure_future(exit_loop())

    async def adjust_window_size(self, size):
        if not hasattr(self, "_last_check_time"):
            self._last_check_time = 0
        now = time.time()
        if now - self._last_check_time < 0.5:
            return size
        current_size = shutil.get_terminal_size(size)
        if current_size != size:
            await self.resize_shell(current_size)
        self._last_check_time = now
        return current_size

    async def resize_shell(self, size):
        request = await self._conn.send_request(
            proto.EnumCommand.RESIZE_SHELL, size=size,
        )
        await self._conn.read_response(request)

    async def create_shell(self, size):
        params = {}
        if self._auto_reconnect:
            params["timeout"] = self.session_timeout
        if self._session_id:
            params["session"] = self._session_id
        request = await self._conn.send_request(
            proto.EnumCommand.CREATE_SHELL, size=size, **params
        )
        response = await self._conn.read_response(request)
        if response["code"]:
            raise RuntimeError("Create shell failed: %s" % response["message"])
        server_platform = response["platform"]
        if not self._session_id and self._auto_reconnect:
            self._session_id = response["session"]
        if sys.platform != "win32":
            with utils.UnixStdIn() as shell_stdin:

                def on_input():
                    char = shell_stdin.read(1)
                    if char == b"\n":
                        char = b"\r"
                    if server_platform == "win32":
                        if char == b"\x1b":
                            chars = shell_stdin.read(2)
                            char += chars  # Must send together

                    utils.safe_ensure_future(self.write_shell_stdin(char))

                self._loop.add_reader(shell_stdin, on_input)

                while self._running:
                    size = await self.adjust_window_size(size)
                    await asyncio.sleep(0.005)
                self._loop.remove_reader(shell_stdin)
        else:
            import msvcrt

            while self._running:
                if msvcrt.kbhit():
                    char = msvcrt.getch()
                    if char == b"\xe0":
                        char = msvcrt.getch()
                        if char == b"H":
                            char = b"\x1bOA"
                        elif char == b"P":
                            char = b"\x1bOB"
                        elif char == b"K":
                            char = b"\x1bOD"
                        elif char == b"M":
                            char = b"\x1bOC"
                        else:
                            utils.logger.warn(
                                r"[%s] Unknown input key \xe0%s"
                                % (self.__class__.__name__, char)
                            )
                    elif char == b"\x1d":
                        # Ctrl + ]
                        char = b"\x03"
                    if server_platform == "win32":
                        char = char.replace(b"\x1bO", b"\x1b[")
                    asyncio.ensure_future(self.write_shell_stdin(char))
                else:
                    size = await self.adjust_window_size(size)
                    await asyncio.sleep(0.02)

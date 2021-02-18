# -*- coding: utf-8 -*-

import asyncio
import os
import sys

import tornado.web
import tornado.websocket

from . import proto, shell, utils, workspace


class WebSocketProtocol(tornado.websocket.WebSocketProtocol13):
    async def accept_connection(self, handler):
        if self.handler.check_permission():
            await super(WebSocketProtocol, self).accept_connection(handler)
        else:
            handler.set_status(403)
            log_msg = "Authorization Failed"
            handler.finish(log_msg)


class WSTerminalServerHandler(tornado.websocket.WebSocketHandler):
    """Websocket Terminal Server Handler"""

    token = None

    def __init__(self, *args, **kwargs):
        super(WSTerminalServerHandler, self).__init__(*args, **kwargs)
        self._buffer = b""
        self._workspace = None
        self._shell = None
        self._sequence = 0x10000

    def check_permission(self):
        if self.token:
            auth = self.request.headers.get("Authorization", "")
            if auth.startswith("Token "):
                return auth.split()[-1].strip() == self.token
            else:
                return False
        return True

    def get_websocket_protocol(self):
        """Override to connect target server"""
        websocket_version = self.request.headers.get("Sec-WebSocket-Version")
        if websocket_version in ("7", "8", "13"):
            params = tornado.websocket._WebSocketParams(
                ping_interval=self.ping_interval,
                ping_timeout=self.ping_timeout,
                max_message_size=self.max_message_size,
                compression_options=self.get_compression_options(),
            )
            return WebSocketProtocol(self, mask_outgoing=True, params=params)

    async def on_message(self, message):
        self._buffer += message
        packet, self._buffer = proto.TransportPacket.deserialize(self._buffer)
        if packet:
            await self.handle_request(packet.message)

    async def send_request(self, command, **kwargs):
        self._sequence += 1
        data = {
            "command": command,
            "type": proto.EnumPacketType.REQUEST,
            "id": self._sequence,
        }
        data.update(kwargs)
        packet = proto.TransportPacket(data)
        return await self.write_message(packet.serialize(), True)

    async def send_response(self, request, code=0, message=None, **kwargs):
        data = {
            "command": request["command"],
            "type": proto.EnumPacketType.RESPONSE,
            "id": request["id"],
            "code": code,
            "message": message or "",
        }
        data.update(kwargs)
        packet = proto.TransportPacket(data)
        return await self.write_message(packet.serialize(), True)

    async def handle_request(self, request):
        utils.logger.debug(
            "[%s][Request][%d][%s] %s"
            % (
                self.__class__.__name__,
                request.get("id", 0),
                request.get("command"),
                str(request)[:200],
            )
        )
        if request["command"] == proto.EnumCommand.SYNC_WORKSPACE:
            worksapce_id = request["workspace"]
            workspace_path = os.path.join(
                os.environ.get("WSTERM_WORKSPACE", os.environ.get("TEMP", "/tmp")),
                worksapce_id,
            )
            self._workspace = workspace.Workspace(workspace_path)
            data = self._workspace.snapshot()
            await self.send_response(
                request,
                data=data,
            )
        elif self._workspace and request["command"] == proto.EnumCommand.WRITE_FILE:
            utils.logger.info(
                "[%s] Update file %s" % (self.__class__.__name__, request["path"])
            )
            self._workspace.write_file(
                request["path"], request["data"], request["overwrite"]
            )
        elif self._workspace and request["command"] == proto.EnumCommand.REMOVE_FILE:
            utils.logger.info(
                "[%s] Remove file %s" % (self.__class__.__name__, request["path"])
            )
            self._workspace.remove_file(request["path"])
        elif self._workspace and request["command"] == proto.EnumCommand.CREATE_DIR:
            utils.logger.info(
                "[%s] Create directory %s" % (self.__class__.__name__, request["path"])
            )
            self._workspace.create_directory(request["path"])
        elif self._workspace and request["command"] == proto.EnumCommand.REMOVE_DIR:
            utils.logger.info(
                "[%s] Remove directory %s" % (self.__class__.__name__, request["path"])
            )
            self._workspace.remove_directory(request["path"])
        elif self._workspace and request["command"] == proto.EnumCommand.MOVE_ITEM:
            utils.logger.info(
                "[%s] Move item %s to %s"
                % (self.__class__.__name__, request["src_path"], request["dst_path"])
            )
            self._workspace.move_item(request["src_path"], request["dst_path"])
        elif request["command"] == proto.EnumCommand.CREATE_SHELL:
            utils.logger.info(
                "[%s] Create shell (%d, %d)"
                % (self.__class__.__name__, *request["size"])
            )
            if self._shell:
                await self.send_response(request, code=-1, message="Shell is created")
            else:
                await self.send_response(request, platform=sys.platform)
                shell_workspace = os.getcwd()
                if self._workspace:
                    shell_workspace = self._workspace.path
                asyncio.ensure_future(
                    self.spawn_shell(shell_workspace, request["size"])
                )
        elif request["command"] == proto.EnumCommand.WRITE_STDIN:
            if not self._shell:
                await self.send_response(request, code=-1, message="Shell not create")
            else:
                utils.logger.debug(
                    "[%s] Input %s" % (self.__class__.__name__, request["buffer"])
                )
                self._shell.write(request["buffer"])
        elif request["command"] == proto.EnumCommand.RESIZE_SHELL:
            if not self._shell:
                await self.send_response(request, code=-1, message="Shell not create")
            else:
                utils.logger.info(
                    "[%s] Resize shell to %d,%d"
                    % (self.__class__.__name__, request["size"][0], request["size"][1])
                )
                self._shell.resize(request["size"])
                await self.send_response(request)

    async def write_shell_stdout(self, buffer):
        utils.logger.debug("[%s] Output %s" % (self.__class__.__name__, buffer))
        await self.send_request(proto.EnumCommand.WRITE_STDOUT, buffer=buffer)

    async def spawn_shell(self, workspace, size):
        utils.logger.info(
            "[%s] Spawn new shell (%d, %d)"
            % (self.__class__.__name__, size[0], size[1])
        )
        self._shell = shell.Shell(workspace, size)
        proc, _, _, _ = await self._shell.create()
        tasks = [None]
        if self._shell.stderr:
            tasks.append(None)
        while proc.returncode is None:
            if tasks[0] is None:
                tasks[0] = utils.safe_ensure_future(self._shell.stdout.read(4096))
            if self._shell.stderr and tasks[1] is None:
                tasks[1] = utils.safe_ensure_future(self._shell.stderr.read(4096))

            done_tasks, _ = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )

            for task in done_tasks:
                index = tasks.index(task)
                assert index >= 0
                tasks[index] = None
                buffer = task.result()
                if not buffer:
                    await asyncio.sleep(0.01)
                    break

                await self.write_shell_stdout(buffer)
        utils.logger.warn("[%s] Shell process exit" % self.__class__.__name__)
        await self.send_request(proto.EnumCommand.EXIT_SHELL)
        self._shell = None

    def on_connection_close(self):
        pass


def start_server(listen_address, path, token=None):
    utils.logger.info("Websocket server listening at %s:%d" % listen_address)
    WSTerminalServerHandler.token = token
    handlers = [
        (path, WSTerminalServerHandler),
    ]
    app = tornado.web.Application(handlers)
    app.listen(listen_address[1], listen_address[0])

# -*- coding: utf-8 -*-

import asyncio
import os

import tornado.web
import tornado.websocket

from . import proto, shell, utils, workspace


class WSTerminalServerHandler(tornado.websocket.WebSocketHandler):
    """Websocket Terminal Server Handler"""

    def __init__(self, *args, **kwargs):
        super(WSTerminalServerHandler, self).__init__(*args, **kwargs)
        self._buffer = b""
        self._workspace = None
        self._shell = None
        self._sequence = 0x10000

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
        utils.logger.debug("[%s] %s" % (self.__class__.__name__, request))
        if request["command"] == proto.EnumCommand.SYNC_WORKSPACE:
            worksapce_id = request["workspace"]
            workspace_path = os.path.join(
                os.environ.get("WORKSPACE", os.environ.get("TEMP", "/tmp")),
                worksapce_id,
            )
            self._workspace = workspace.Workspace(workspace_path)
            data = self._workspace.snapshot()
            await self.send_response(
                request, data=data,
            )
        elif self._workspace and request["command"] == proto.EnumCommand.WRITE_FILE:
            utils.logger.info(
                "[%s] Update file %s" % (self.__class__.__name__, request["path"])
            )
            self._workspace.write_file(request["path"], request["data"])
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
        elif request["command"] == proto.EnumCommand.CREATE_SHELL:
            utils.logger.info(
                "[%s] Create shell (%d, %d)"
                % (self.__class__.__name__, *request["size"])
            )
            if self._shell:
                await self.send_response(request, code=-1, message="Shell is created")
            else:
                await self.send_response(request)
                shell_workspace = os.getcwd()
                if self._workspace:
                    shell_workspace = self._workspace.path
                asyncio.ensure_future(self.spawn_shell(shell_workspace, request["size"]))
        elif request["command"] == proto.EnumCommand.WRITE_STDIN:
            if not self._shell:
                await self.send_response(request, code=-1, message="Shell not create")
            else:
                utils.logger.debug(
                    "[%s] Input %s" % (self.__class__.__name__, request["buffer"])
                )
                self._shell.write(request["buffer"])

    async def write_shell_stdout(self, buffer):
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
        await self.send_request(proto.EnumCommand.SHELL_EXIT)
        self._shell = None

    def on_connection_close(self):
        pass


def start_server(listen_address, path):
    utils.logger.info("Websocket server listening at %s:%d" % listen_address)
    handlers = [
        (path, WSTerminalServerHandler),
    ]
    app = tornado.web.Application(handlers)
    app.listen(listen_address[1], listen_address[0])

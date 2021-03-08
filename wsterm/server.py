# -*- coding: utf-8 -*-

import asyncio
import os
import sys
import time
import uuid

import tornado.web
import tornado.websocket

from . import proto, shell, utils, workspace


@utils.Singleton
class ShellSessionManager(object):
    def __init__(self):
        self._sessions = {}
        asyncio.ensure_future(self.check_session_task())

    async def check_session_task(self):
        while True:
            for session in self._sessions:
                timeout, shell, timestamp = self._sessions[session]
                if timestamp and time.time() >= timestamp + timeout:
                    # Clean session
                    shell.exit()
                    self._sessions.pop(session)
                    break
            await asyncio.sleep(1)

    def create_session(self, shell, timeout):
        session_id = str(uuid.uuid4())
        self._sessions[session_id] = [timeout, shell, 0]
        return session_id

    def get_session(self, session_id):
        session = self._sessions.get(session_id)
        if session:
            return session[1]
        return None

    def update_session_time(self, session_id, timestamp):
        session = self._sessions.get(session_id)
        assert session != None
        session[2] = timestamp


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
        self._session_id = None
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
            try:
                await self.handle_request(packet.message)
            except Exception as ex:
                utils.logger.exception("Handle request %s failed" % packet.message)
                await self.send_response(packet.message, -1, str(ex))

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
                request, data=data,
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
            session_id = request.get("session")
            session_timeout = request.get("timeout")
            utils.logger.info(
                "[%s] Create shell (%d, %d)"
                % (self.__class__.__name__, *request["size"])
            )

            ssm = ShellSessionManager()
            if self._shell:
                await self.send_response(request, code=-1, message="Shell is created")
            else:
                if session_id:
                    shell = ssm.get_session(session_id)
                    if not shell:
                        await self.send_response(
                            request,
                            code=-1,
                            message="Shell session %s not found" % session_id,
                        )
                        return
                    utils.logger.info(
                        "[%s] Use Cached shell session %s"
                        % (self.__class__.__name__, session_id)
                    )
                    self._session_id = session_id
                    self._shell = shell
                    ssm.update_session_time(session_id, 0)  # Avoid cleaned
                    asyncio.ensure_future(self.forward_shell())
                    await self.send_response(request, platform=sys.platform)
                else:
                    shell_workspace = os.getcwd()
                    if self._workspace:
                        shell_workspace = self._workspace.path
                    asyncio.ensure_future(
                        self.spawn_shell(shell_workspace, request["size"])
                    )
                    time0 = time.time()
                    while time.time() - time0 < 5:
                        if self._shell:
                            break
                        await asyncio.sleep(0.005)
                    else:
                        await self.send_response(
                            request, code=-1, message="Spawn shell timeout"
                        )
                        return
                    if session_timeout:
                        self._session_id = ssm.create_session(
                            self._shell, session_timeout
                        )
                        await self.send_response(
                            request, platform=sys.platform, session=self._session_id
                        )
                    else:
                        await self.send_response(request, platform=sys.platform)
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
        self._shell = await shell.Shell.create(workspace, size)
        await self.forward_shell()

    async def forward_shell(self):
        tasks = [None]
        if self._shell.stderr:
            tasks.append(None)
        while self._shell and self._shell.process.returncode is None:
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

                if self._shell:
                    await self.write_shell_stdout(buffer)
        utils.logger.warn("[%s] Shell process exit" % self.__class__.__name__)
        if self._shell:
            await self.send_request(proto.EnumCommand.EXIT_SHELL)
            self._shell = None

    def on_connection_close(self):
        utils.logger.warn("[%s] Connection closed" % self.__class__.__name__)
        if self._shell:
            if not self._session_id:
                # Do not keep session
                self._shell.exit()
            else:
                # Wait foe client reconnect
                ShellSessionManager().update_session_time(self._session_id, time.time())
            self._shell = None


class MainHandler(tornado.web.RequestHandler):
    def get(self):
        self.write(
            "<h1>Hello WSTerm</h1><script>location.href='https://github.com/wsterm/wsterm/';</script>"
        )


def start_server(listen_address, path, token=None):
    utils.logger.info("Websocket server listening at %s:%d" % listen_address)
    WSTerminalServerHandler.token = token
    handlers = [(path, WSTerminalServerHandler), ("/", MainHandler)]
    app = tornado.web.Application(handlers, websocket_ping_interval=30)
    app.listen(listen_address[1], listen_address[0])

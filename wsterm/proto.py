# -*- coding: utf-8 -*-

"""Protocol
"""

import struct

import msgpack


class EnumPacketType(object):
    REQUEST = 1
    RESPONSE = 2


class EnumCommand(object):
    SYNC_WORKSPACE = "sync-workspace"
    LIST_DIR = "list-dir"
    CREATE_DIR = "create-dir"
    REMOVE_DIR = "remove-dir"
    WRITE_FILE = "write-file"
    REMOVE_FILE = "remove-file"
    MOVE_ITEM = "move-item"

    CREATE_SHELL = "create-shell"
    WRITE_STDIN = "write-stdin"
    WRITE_STDOUT = "write-stdout"
    WRITE_STDERR = "write-stderr"
    RESIZE_SHELL = "resize-shell"
    EXIT_SHELL = "exit-shell"


class TransportPacket(object):
    """Transport packet serialize/deserialize"""

    def __init__(self, message):
        self._message = message

    def __str__(self):
        return "<%s object command=%s type=%d at 0x%x>" % (
            self.__class__.__name__,
            self._message.get("command"),
            self._message.get("type"),
            id(self),
        )

    @property
    def message(self):
        return self._message

    def serialize(self):
        buffer = msgpack.dumps(self._message)
        return struct.pack("!I", len(buffer)) + buffer

    @staticmethod
    def deserialize(buffer):
        if len(buffer) < 4:
            return None, buffer
        buffer_size = struct.unpack("!I", buffer[:4])[0]
        if len(buffer) - 4 < buffer_size:
            return None, buffer
        message = msgpack.loads(buffer[4 : 4 + buffer_size])
        buffer = buffer[4 + buffer_size :]
        return TransportPacket(message), buffer

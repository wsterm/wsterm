# -*- coding: utf-8 -*-

import asyncio
import hashlib
import os
import pathlib
import shutil

import gitignore_parser

from . import aiowatch, utils


class EnumEvent(object):
    """Workspace event"""

    # BEFORE_SNAPSHOT = "before_snapshot"
    # AFTER_SNAPSHOT = "after_snapshot"
    ON_DIRECTORY_CREATED = "on_directory_created"
    ON_DIRECTORY_REMOVED = "on_directory_removed"
    ON_FILE_CREATED = "on_file_created"
    ON_FILE_REMOVED = "on_file_removed"
    ON_FILE_MODIFIED = "on_file_modified"
    ON_ITEM_MOVED = "on_item_moved"


class Directory(object):
    def __init__(self, dir_path):
        self._dir_path = dir_path

    @property
    def name(self):
        return os.path.split(self._dir_path)[-1]

    @property
    def path(self):
        return self._dir_path

    def get_dirs(self):
        dir_list = []
        for it in os.listdir(self._dir_path):
            path = os.path.join(self._dir_path, it)
            if os.path.isdir(path):
                dir_list.append(Directory(path))
        return dir_list

    def get_files(self):
        file_list = []
        for it in os.listdir(self._dir_path):
            path = os.path.join(self._dir_path, it)
            if os.path.isfile(path):
                file_list.append(File(path))
        return file_list


class File(object):
    def __init__(self, file_path):
        self._file_path = file_path

    @property
    def name(self):
        return os.path.split(self._file_path)[-1]

    @property
    def path(self):
        return self._file_path

    @property
    def last_modify_time(self):
        return os.stat(self._file_path).st_mtime

    @property
    def size(self):
        return os.path.getsize(self._file_path)

    @property
    def hash(self):
        m = hashlib.md5()
        with open(self._file_path, "rb") as fp:
            m.update(fp.read())
            return m.hexdigest()


class Workspace(object):
    def __init__(self, root_path):
        self._root_path = os.path.realpath(root_path)
        if not os.path.isdir(self._root_path):
            os.makedirs(self._root_path)
        self._handlers = []
        self._ignore_rules = []
        self._build_ignore_rules()
        self._watcher = aiowatch.AIOWatcher(self._root_path, self)
        self._running = True

    @property
    def path(self):
        return self._root_path

    def _build_ignore_rules(self):
        gitignore_path = os.path.join(self._root_path, ".gitignore")
        ignore_text = ".git/\n.env2/\n.env3/\n*.pyc\n"
        if os.path.isfile(gitignore_path):
            with open(gitignore_path) as fp:
                ignore_text += fp.read()

        for line in ignore_text.splitlines():
            if not line.strip():
                continue

            rule = gitignore_parser.rule_from_pattern(
                line, base_path=pathlib.Path(self._root_path), source=None
            )
            if rule:
                self._ignore_rules.append(rule)

    def on_watch_filter(self, path):
        path = os.path.join(self._root_path, path)
        if os.path.islink(path):
            path = os.readlink(path)
        if not os.path.exists(path):
            utils.logger.info(
                "[%s] Path %s not exist" % (self.__class__.__name__, path)
            )
            return True
        if not path.startswith(self._root_path):
            utils.logger.info(
                "[%s] Path %s not start with %s"
                % (self.__class__.__name__, path, self._root_path)
            )
            return True
        if self.path_should_ignored(path):
            return True
        return False

    def join_path(self, path):
        return os.path.join(self._root_path, path.replace("/", os.path.sep))

    def create_directory(self, path):
        dir_path = os.path.join(self._root_path, path.replace("/", os.path.sep))
        if not os.path.isdir(dir_path):
            os.makedirs(dir_path)

    def remove_directory(self, path):
        dir_path = os.path.join(self._root_path, path.replace("/", os.path.sep))
        if os.path.isdir(dir_path):
            shutil.rmtree(dir_path)

    def write_file(self, path, data, overwrite=True):
        file_path = os.path.join(self._root_path, path.replace("/", os.path.sep))
        dir_path = os.path.dirname(file_path)
        if not os.path.isdir(dir_path):
            os.makedirs(dir_path)
        flag = "wb" if overwrite else "ab"
        with open(file_path, flag) as fp:
            fp.write(data)

    def remove_file(self, path):
        file_path = os.path.join(self._root_path, path.replace("/", os.path.sep))
        if os.path.isfile(file_path):
            os.remove(file_path)

    def move_item(self, src_path, dst_path):
        src_path = os.path.join(self._root_path, src_path.replace("/", os.path.sep))
        dst_path = os.path.join(self._root_path, dst_path.replace("/", os.path.sep))
        if os.path.exists(src_path):
            os.rename(src_path, dst_path)
        else:
            utils.logger.warning("[%s] Path %s not exist" % (self.__class__.__name__, src_path))

    def set_perm(self, path, perm):
        path = os.path.join(self._root_path, path.replace("/", os.path.sep))
        if os.path.exists(path):
            os.chmod(path, perm)

    def register_handler(self, handler):
        self._handlers.append(handler)

    def on_event(self, event_name, **kwargs):
        path = kwargs.get("path", kwargs.get("src_path"))
        if path and ".git" in path.split(os.path.sep):
            return
        for handler in self._handlers:
            func = getattr(handler, event_name)
            if func:
                asyncio.ensure_future(func(**kwargs))

    def on_directory_created(self, path):
        self.on_event(EnumEvent.ON_DIRECTORY_CREATED, path=path)

    def on_directory_removed(self, path):
        self.on_event(EnumEvent.ON_DIRECTORY_REMOVED, path=path)

    def on_file_created(self, path):
        self.on_event(EnumEvent.ON_FILE_CREATED, path=path)

    def on_file_modified(self, path):
        self.on_event(EnumEvent.ON_FILE_MODIFIED, path=path)

    def on_file_removed(self, path):
        self.on_event(EnumEvent.ON_FILE_REMOVED, path=path)

    def on_item_moved(self, src_path, dst_path):
        self.on_event(EnumEvent.ON_ITEM_MOVED, src_path=src_path, dst_path=dst_path)

    def path_should_ignored(self, path):
        if os.path.islink(path):
            utils.logger.info(
                "[%s] Link path %s is ignored" % (self.__class__.__name__, path)
            )
            return True
        for rule in self._ignore_rules:
            if rule.match(path):
                utils.logger.info(
                    "[%s] Path %s ignored due to rule %s"
                    % (self.__class__.__name__, path, rule)
                )
                return True
        return False

    def snapshot(self, root=None):
        result = {"dirs": {}, "files": {}}
        root = root or self._root_path
        if os.path.isdir(root) and os.path.split(root)[-1] == ".git":
            # Auto ignore .git directory
            return

        if self.path_should_ignored(root):
            return

        root_dir = Directory(root)
        for subdir in root_dir.get_dirs():
            if self.path_should_ignored(subdir.path):
                continue
            res = self.snapshot(subdir.path)
            if res:
                result["dirs"][subdir.name] = res
        for file in root_dir.get_files():
            if self.path_should_ignored(file.path):
                # Ignore current file
                continue
            result["files"][file.name] = file.hash
        return result

    async def watch(self):
        await self._watcher.start()

    def make_diff(self, other_snapshot):
        cache_data = self.snapshot()
        return utils.diff(cache_data, other_snapshot)

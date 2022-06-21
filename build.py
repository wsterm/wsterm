# -*- coding: utf-8 -*-

import argparse
import datetime
import os
import sys


FILE_VERSION_RESOURCE = r"""# UTF-8
#
# For more details about fixed file info 'ffi' see:
# http://msdn.microsoft.com/en-us/library/ms646997.aspx
VSVersionInfo(
  ffi=FixedFileInfo(
    # filevers and prodvers should be always a tuple with four items: (1, 2, 3, 4)
    # Set not needed items to zero 0.
    filevers=(%(main_ver)d, %(sub_ver)d, %(min_ver)d, %(build_num)d),
    prodvers=(%(main_ver)d, %(sub_ver)d, %(min_ver)d, %(build_num)d),
    # Contains a bitmask that specifies the valid bits 'flags'r
    mask=0x17,
    # Contains a bitmask that specifies the Boolean attributes of the file.
    flags=0x0,
    # The operating system for which this file was designed.
    # 0x4 - NT and there is no need to change it.
    OS=0x4,
    # The general type of file.
    # 0x1 - the file is an application.
    fileType=0x1,
    # The function of the file.
    # 0x0 - the function is not defined for this fileType
    subtype=0x0,
    # Creation date and time stamp.
    date=(0, 0)
    ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u'080404b0',
        [StringStruct(u'CompanyName', u'drunkdream.com'),
        StringStruct(u'FileDescription', u'wsterm'),
        StringStruct(u'FileVersion', u'%(main_ver)d.%(sub_ver)d.%(min_ver)d'),
        StringStruct(u'InternalName', u'wsterm.exe'),
        StringStruct(u'LegalCopyright', u'Copyright (C) 2020-%(year)d drunkdream.com. All Rights Reserved'),
        StringStruct(u'OriginalFilename', u'wsterm.exe'),
        StringStruct(u'ProductName', u'wsterm'),
        StringStruct(u'ProductVersion', u'%(main_ver)d.%(sub_ver)d.%(min_ver)d')])
      ]), 
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
  ]
)
"""


def build_by_pyinstaller(platform, version):
    os.system("python -m pip install pyinstaller")
    version_items = version.split(".")
    for i in range(len(version_items)):
        version_items[i] = int(version_items[i])

    with open("version.py", "w") as fp:
        fp.write('version_info=u"%s"' % version)

    main_file = "main.py"
    if not os.path.isfile(main_file):
        with open(main_file, "w") as fp:
            fp.write(
                r"""# -*- coding: utf-8 -*-
import sys
from wsterm.__main__ import main
if __name__ == "__main__":
    sys.exit(main())
"""
            )

    if sys.platform == "win32":
        version_file = "version_file.txt"
        text = FILE_VERSION_RESOURCE % {
            "main_ver": version_items[0],
            "sub_ver": version_items[1],
            "min_ver": version_items[2],
            "build_num": version_items[3] if len(version_items) > 3 else 0,
            "year": datetime.datetime.today().year,
        }
        with open(version_file, "w") as fp:
            fp.write(text)
        cmdline = "python -m PyInstaller -F -c %s -n wsterm --version-file %s" % (
            main_file,
            version_file,
        )
    else:
        cmdline = "python -m PyInstaller -F -w %s -n wsterm" % main_file

    os.system(cmdline)


def build(backend):
    os.system("python -m pip install -r requirements.txt")
    import wsterm

    platform = "win32"
    if sys.platform == "linux2":
        platform = "linux"
    elif sys.platform == "darwin":
        platform = "macos"
    version = wsterm.VERSION

    if backend == "pyinstaller":
        return build_by_pyinstaller(platform, version)
    else:
        raise NotImplementedError(backend)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="build-wsterm", description="Build wsterm tool."
    )
    parser.add_argument(
        "--backend",
        help="build backend",
        choices=("pyinstaller", "py2exe"),
        default="pyinstaller",
    )
    args = parser.parse_args()

    sys.exit(build(args.backend))

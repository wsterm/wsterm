# WSTerm

使用Websocket协议实现的远程终端，可以用于远程调试（支持自动同步本地工作区文件到远程机器）

## 支持环境

操作系统：

* Linux
* Windows
* MacOS

Python `3.5`以上版本

## 功能特性

* 远程终端
* 自动监听、同步工作区文件

## 为什么要使用WSTerm？

相比于SSH，Websocket协议具有更好的可访问性，很多场景下SSH服务不会被暴露出来，但是一般会暴露出Websocket服务。

SSH服务通常只支持Linux和MacOS，Windows需要安装独立的应用；而WSTerm可以直接运行在以上三种系统，使用更加方便。

自动同步本地工作区文件，可以实现本地修改代码，直接远程执行的特性，大大降低远程编写、执行代码的复杂性。

## 使用方式

### 安装方法

```bash
$ pip install wsterm
```

### 服务端

```bash
$ wsterm --url ws://0.0.0.0:8080/terminal/ --server
```

此时，服务端会监听在本地的`0.0.0.0:8080`地址；如果只想监听在回环地址，可以将`0.0.0.0`替换成`127.0.0.1`。

服务端还支持以下可选参数：

`--token`: 指定鉴权使用的Token

`--log-level`: 日志级别，默认为`info`

`--log-file`: 日志保存文件路径

`-d/--daemon`: 是否以daemon方式启动，默认为`False`

### 客户端

```bash
$ wsterm --url ws://1.2.3.4:8080/terminal/
```

客户端支持的可选参数：

`--workspace`: 需要同步的工作区目录

`--token`、`--log-level`、`--log-file`等参数与服务端相同

> 服务端与客户端需要指定相同的`Token`才能正常连接


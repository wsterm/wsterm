FROM python:3.7

ENV SHELL=/bin/bash
ENV WSTERM_WORKSPACE=/data/workspace
ENV DEBIAN_MIRROR=https://mirrors.tencent.com
ENV PYPI_URL=https://mirrors.tencent.com/pypi/simple/

ADD wsterm /data/wsterm/wsterm
ADD setup.py /data/wsterm/setup.py
ADD requirements.txt /data/wsterm/requirements.txt
ADD README.md /data/wsterm/README.md

RUN sed -i "s#http://deb.debian.org#$DEBIAN_MIRROR#g" /etc/apt/sources.list \
    && apt update && apt install net-tools \
    && pip3 install virtualenv -i $PYPI_URL \
    && pip3 install -e /data/wsterm -i $PYPI_URL \
    && mkdir $WSTERM_WORKSPACE

ENTRYPOINT ["wsterm", "--url", "ws://0.0.0.0/terminal/", "--server"]

EXPOSE 80

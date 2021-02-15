FROM python:3.6.9

ENV SHELL=/bin/bash
ENV WSTERM_WORKSPACE=/data

RUN mkdir $WSTERM_WORKSPACE \
    && pip3 install wsterm

ENTRYPOINT ["wsterm", "--url", "ws://0.0.0.0/terminal/", "--server"]

EXPOSE 80

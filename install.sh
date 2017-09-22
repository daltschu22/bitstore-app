#!/bin/bash

GID=$( id -g )

TTY=
if [ "${TERM}" != 'dumb' ] ; then
    TTY='-it'
fi

# update any submodules
git submodule update --init --recursive

# install python requirements into lib/
sudo docker run ${TTY} --rm \
    -v "$(pwd)":/usr/src \
    -w /usr/src \
    python:2.7 bash -c \
        "pip install -t lib/ -r requirements.txt --upgrade && \
        find lib/ -type d -print0 | xargs -0 chmod 0755; \
        find lib/ -print0 | xargs -0 chown -h ${EUID}:${GID}"

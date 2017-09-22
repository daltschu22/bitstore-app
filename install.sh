#!/bin/sh

TTY=
if [ "${TERM}" != 'dumb' ] ; then
    TTY='-it'
fi

# update any submodules
git submodule update --init --recursive

# install python requirements into lib/
docker run ${TTY} --rm -v "$(pwd)":/usr/src -w /usr/src python:2.7 \
  pip install -t lib/ -r requirements.txt --upgrade

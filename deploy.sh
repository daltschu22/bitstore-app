#!/bin/bash

APP="broad-bitstore-app"
if [ "$1" == 'dev' ]; then
  APP="broad-bitstore-app-dev"
fi

# set project
gcloud config set project ${APP}

# deploy app
gcloud -q app deploy

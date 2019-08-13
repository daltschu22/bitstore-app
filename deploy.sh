#!/bin/bash

APP="broad-bitstore-app"
if [ "$1" == 'dev' ]; then
  APP="broad-bitstore-app-dev"
fi

# deploy app
gcloud -q app deploy --project ${APP}

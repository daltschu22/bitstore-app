#!/bin/sh

# set project
gcloud config set project broad-bitstore-app

# deploy app
gcloud -q app deploy

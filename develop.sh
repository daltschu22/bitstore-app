#!/bin/sh

docker run -it --rm \
  -v /etc/localtime:/etc/localtime:ro \
  -v /opt/google-cloud-sdk:/opt/google-cloud-sdk \
  -v "$(pwd):/usr/src" \
  -v /local/datastore/broad-bitstore-app:/local/datastore/broad-bitstore-app:rw \
  --expose 8000 \
  --expose 8080 \
  -p 8000:8000 \
  -p 8080:8080 \
  -w /usr/src python:2.7 \
    /opt/google-cloud-sdk/platform/google_appengine/dev_appserver.py \
      -A broad-bitstore-app \
      --admin_host 0.0.0.0 \
      --admin_port 8000 \
      --api_port 8082 \
      --datastore_path /local/datastore/broad-bitstore-app \
      --host 0.0.0.0 \
      --port 8080 \
      --skip_sdk_update_check=True \
      --use_mtime_file_watcher true \
      app.yaml
      # --clear_datastore

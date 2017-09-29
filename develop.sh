#!/bin/sh

APP='broad-bitstore-app'

docker run -it --rm \
  -v /etc/localtime:/etc/localtime:ro \
  -v /opt/google-cloud-sdk:/opt/google-cloud-sdk \
  -v "$(pwd):/usr/src" \
  -v /local/datastore/${APP}:/local/datastore/${APP}:rw \
  --expose 8000 \
  --expose 8080 \
  -p 8000:8000 \
  -p 8080:8080 \
  -w /usr/src python:2.7 \
    /opt/google-cloud-sdk/bin/dev_appserver.py \
      -A ${APP} \
      --admin_host 0.0.0.0 \
      --admin_port 8000 \
      --appidentity_email_address=${APP}@appspot.gserviceaccount.com \
      --appidentity_private_key_path=service_account.pem \
      --datastore_path /local/datastore/${APP} \
      --host 0.0.0.0 \
      --port 8080 \
      --skip_sdk_update_check=True \
      --use_mtime_file_watcher true \
      app.yaml
      # --clear_datastore

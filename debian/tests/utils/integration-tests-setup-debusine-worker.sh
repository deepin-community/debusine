#!/bin/sh

set -e

# Set up debusine-worker: creates config.ini and writes the token (disabled
# at the moment) to stdout

debusine_worker_config_directory=/etc/debusine/worker

sed "s,api-url = .*,api-url = https://$(hostname -f)/api," \
	/usr/share/doc/debusine-worker/examples/config.ini \
	>"$debusine_worker_config_directory/config.ini"
chown debusine-worker:debusine-worker "$debusine_worker_config_directory"

systemctl restart debusine-worker

debusine_worker_token_file="$debusine_worker_config_directory/token"

# Wait up to 15 seconds for the token file to appear
count=0
while [ ! -f "$debusine_worker_token_file" ] && [ $count -lt 15 ]
do
  sleep 1
  count=$((count + 1))
done

cat "$debusine_worker_token_file"

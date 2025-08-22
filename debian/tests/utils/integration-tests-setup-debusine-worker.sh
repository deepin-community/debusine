#!/bin/sh

set -e

# Set up debusine-worker: creates config.ini, creates the worker in the
# server's database with an activation token, and configures the worker
# using that activation token.

debusine_worker_config_directory=/etc/debusine/worker

sed "s,api-url = .*,api-url = https://$(hostname -f)/api," \
	/usr/share/doc/debusine-worker/examples/config.ini \
	>"$debusine_worker_config_directory/config.ini"
chown debusine-worker:debusine-worker "$debusine_worker_config_directory"

token_file="$debusine_worker_config_directory/activation-token"
sudo -u debusine-server debusine-admin worker create "$(hostname -f)" | \
	sudo -u debusine-worker tee "$token_file" >/dev/null
sudo -u debusine-worker chmod 600 "$token_file"

systemctl restart debusine-worker

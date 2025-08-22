#!/bin/sh

set -e

# Set up PostgreSQL database and user
sudo -u postgres createuser debusine-signing || true
sudo -u postgres createdb --owner debusine-signing debusine-signing || true

debusine_signing_config_directory=/etc/debusine/signing

# Create configuration file
sed "s,api-url = .*,api-url = https://$(hostname -f)/api," \
	/usr/share/doc/debusine-signing/examples/config.ini \
	>"$debusine_signing_config_directory/config.ini"

# Create the worker in the server's database with an activation token, and
# configure the worker using that activation token.
token_file="$debusine_signing_config_directory/activation-token"
sudo -u debusine-server debusine-admin worker create --worker-type signing "$(hostname -f)" | \
	sudo -u debusine-signing tee "$token_file" >/dev/null
sudo -u debusine-signing chmod 600 "$token_file"

# Generate a private key to be used to encrypt other private keys
sudo -u debusine-signing debusine-signing generate_service_key "$debusine_signing_config_directory/0.key"

systemctl reset-failed debusine-signing-migrate
systemctl restart debusine-signing-migrate
systemctl restart debusine-signing

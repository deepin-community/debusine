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

# Generate a private key to be used to encrypt other private keys
sudo -u debusine-signing debusine-signing generate_service_key "$debusine_signing_config_directory/0.key"

systemctl reset-failed debusine-signing-migrate
systemctl restart debusine-signing-migrate
systemctl restart debusine-signing

debusine_signing_token_file="$debusine_signing_config_directory/token"

# Wait up to 15 seconds for the token file to appear
count=0
while [ ! -f "$debusine_signing_token_file" ] && [ $count -lt 15 ]
do
  sleep 1
  count=$((count + 1))
done

cat "$debusine_signing_token_file"

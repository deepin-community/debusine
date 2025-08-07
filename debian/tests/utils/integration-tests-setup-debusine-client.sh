#!/bin/sh

set -e

# Create ~/.config/debusine/client/config.ini to connect to debusine-server
# available in localhost
# Create a new token and writes the config.ini

debusine_client_config_directory=~/.config/debusine/client
mkdir --parents "$debusine_client_config_directory"

user="test-user"
# shellcheck disable=SC2024
sudo -u debusine-server debusine-admin create_user "$user" email@example.com \
  > "$AUTOPKGTEST_TMP"/test-password.txt
token_client=$(sudo -u debusine-server debusine-admin create_token "$user")

cat << EOF > "$debusine_client_config_directory/config.ini"
[General]
default-server = integration-test

[server:integration-test]
api-url = https://$(hostname -f)/api
scope = debusine
token = $token_client
EOF

# Give owner permission on the default workspace to the test user
sudo -u debusine-server debusine-admin group create debusine/Admins
sudo -u debusine-server debusine-admin workspace grant_role \
  debusine/System owner Admins
sudo -u debusine-server debusine-admin group members debusine/Admins \
  --add "$user"

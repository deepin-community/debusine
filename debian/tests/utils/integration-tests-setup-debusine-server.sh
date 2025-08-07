#!/bin/sh

set -e

# Django requires non-IP/localhost URLs to contain at least one dot, so
# adjust the testbed's FQDN and regenerate the self-signed certificate if
# necessary
if ! hostname -f | grep -Fq .; then
	(grep -v '^127\.0\.1\.1[[:space:]]' /etc/hosts; \
	 echo "127.0.1.1 $(hostname).localdomain $(hostname)") >/etc/hosts.new
	mv /etc/hosts.new /etc/hosts
	make-ssl-cert -f generate-default-snakeoil
fi

# Setup debusine-server with similar steps as an admin would do:
# Creates the database, migrate, set up nginx, allowed-hosts
#
# It assumes that postgres is already running

# Setup postgresql database and user
sudo -u postgres createuser debusine-server || true
sudo -u postgres createdb --owner debusine-server debusine || true

# Set up nginx
sed "s/server_name .*;/server_name $(hostname -f);/" \
	/usr/share/doc/debusine-server/examples/nginx-vhost.conf \
	>/etc/nginx/sites-available/debusine.example.net

ln -sf /etc/nginx/sites-available/debusine.example.net \
	/etc/nginx/sites-enabled

rm -f /etc/nginx/sites-enabled/default

# Trust self-signed certificate
cp /etc/ssl/certs/ssl-cert-snakeoil.pem \
	"/usr/local/share/ca-certificates/$(hostname -f).crt"
update-ca-certificates

systemctl reset-failed debusine-server-migrate
systemctl restart debusine-server-migrate
systemctl restart debusine-server
systemctl restart debusine-server-celery
systemctl restart debusine-server-periodic-tasks
systemctl restart debusine-server-provisioner
systemctl restart debusine-server-scheduler
systemctl restart nginx

# Create a debian:environments collection for use by tests
sudo -u debusine-server debusine-admin create_collection \
	debian debian:environments </dev/null

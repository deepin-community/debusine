# shellcheck shell=bash
# Source this file from any script.
# On exit of the script (success or error): it copies /var/log/debusine
# to $AUTOPKGTEST_ARTIFACTS/logs/name_of_script making it available
# as artifact.

function copy_logs ()
{
	SUBDIRECTORY=$(basename "$0")
	DESTINATION="$AUTOPKGTEST_ARTIFACTS/logs/$SUBDIRECTORY"

	echo "Copying debusine logs to $DESTINATION"
	mkdir -p "$DESTINATION"
	cp -rv /var/log/debusine "$DESTINATION"

	journalctl -u debusine-server-celery.service > "$DESTINATION/debusine-server-celery.log"
	journalctl -u debusine-server-provisioner.service > "$DESTINATION/debusine-server-provisioner.log"
	journalctl -u debusine-server-scheduler.service > "$DESTINATION/debusine-server-scheduler.log"
}

trap copy_logs EXIT

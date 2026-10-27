#!/bin/bash
set -euo pipefail
cd /home/container
# Wings supplies its configured startup command through STARTUP. Expand only
# the built-in command; do not eval arbitrary shell text or template values.
exec python /opt/endstone-fork/manager.py start --root /home/container

#!/bin/bash
set -euo pipefail
# Wings passes its installer as Docker CMD, leaving the image ENTRYPOINT in
# place. Execute only that fixed mounted script in installation containers.
if [[ $# -eq 2 && ( "$1" == bash || "$1" == /bin/bash ) && "$2" == /mnt/install/install.sh ]]; then
    test -f /mnt/install/install.sh
    exec /bin/bash /mnt/install/install.sh
fi
cd /home/container
# Wings supplies its configured startup command through STARTUP. Expand only
# the built-in command; do not eval arbitrary shell text or template values.
exec python /opt/endstone-fork/manager.py start --root /home/container

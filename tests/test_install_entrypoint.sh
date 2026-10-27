#!/bin/bash
# Reproduce Wings' installation-container command with the image ENTRYPOINT.
set -euo pipefail
image=${1:-endstone-wings:installer-test}
fixture=$(mktemp -d)
trap 'sudo rm -rf -- "$fixture"' EXIT
mkdir "$fixture/install" "$fixture/server"
printf '#!/bin/bash\nset -eu\ntest "$(id -u)" = 0\nprintf installer-ok > /mnt/server/marker\n' > "$fixture/install/install.sh"
# Reproduce the node's distinct UID and root-owned installer. No --user override:
# a runtime image with USER container must fail this test, like real Wings.
chmod 700 "$fixture/install"
chmod 600 "$fixture/install/install.sh"
chmod 700 "$fixture/server"
sudo chown 985:985 "$fixture/server"
docker run --rm --network none \
    -v "$fixture/install:/mnt/install:ro" -v "$fixture/server:/mnt/server" \
    "$image" bash /mnt/install/install.sh
test "$(sudo cat "$fixture/server/marker")" = installer-ok
printf 'Wings installer ENTRYPOINT: passed\n'

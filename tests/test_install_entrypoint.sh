#!/bin/bash
# Reproduce Wings' installation-container command with the image ENTRYPOINT.
set -euo pipefail
image=${1:-endstone-wings:test}
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
mkdir "$fixture/install" "$fixture/server"
printf '#!/bin/bash\nset -eu\nprintf installer-ok > /mnt/server/marker\n' > "$fixture/install/install.sh"
docker run --rm --network none --user 0 \
    -v "$fixture/install:/mnt/install:ro" -v "$fixture/server:/mnt/server" \
    "$image" bash /mnt/install/install.sh
test "$(cat "$fixture/server/marker")" = installer-ok
printf 'Wings installer ENTRYPOINT: passed\n'

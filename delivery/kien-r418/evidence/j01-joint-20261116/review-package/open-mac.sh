#!/bin/bash
set -eu
profile="$(mktemp -d "${TMPDIR:-/tmp}/j01-chrome.XXXXXX")"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"  --user-data-dir="$profile" --no-proxy-server  --host-resolver-rules="MAP localhost 192.168.2.61:18976, MAP idp-da2093353a73.test 192.168.2.61:18977"  --ignore-certificate-errors-spki-list="TTvQtft4kK8YE6XttcgmOXm+evMSTke8ZPwzqk7tNNc=,Wx2vLQWz4TimyTKv6ReCIWqiDdV1Fh3DEGDm9GJ4yAA="  'https://localhost:18776/'

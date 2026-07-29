#!/bin/sh
# The session that gets recorded. Run it through demo/record.sh, which starts
# the stub tracker and exports the environment this expects.
#
# asciinema records a scripted command with nobody at the keyboard, so there is
# no typing to capture: each command is echoed as a prompt line before it runs,
# which is what makes the gif read as a session rather than as raw output.
set -eu

: "${TINA:=tina}"

prompt() {
    printf '\033[1;32m$\033[0m %s\n' "$1"
    sleep 0.7
}

pause() {
    printf '\n'
    sleep 1.4
}

prompt 'tina --version'
$TINA --version
pause

prompt 'tina dispatch --workflow bug --limit 3 --dry-run'
$TINA dispatch --workflow bug --limit 3 --dry-run
pause

prompt 'tina run --workflow bug --item 4821'
$TINA run --workflow bug --item 4821
pause

prompt 'cat outcome.json'
cat outcome.json
sleep 2.5

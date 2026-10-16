# Opt in by sourcing this file from an interactive Bash startup file.
if [[ $- == *i* && ${TERM:-} == xterm-kitty && -z ${TMUX:-} && -z ${STY:-} ]] && command -v retrofetch >/dev/null 2>&1; then
    retrofetch --startup
fi

# Opt in by sourcing this file from an interactive Zsh startup file.
if [[ -o interactive && ${TERM:-} == xterm-kitty && -z ${TMUX:-} && -z ${STY:-} ]] && command -v retrofetch >/dev/null 2>&1; then
    retrofetch --startup
fi

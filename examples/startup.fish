# Opt in by sourcing this file from an interactive Fish startup file.
if status is-interactive; and test "$TERM" = xterm-kitty; and test -z "$TMUX"; and test -z "$STY"; and command -q retrofetch
    retrofetch --startup
end

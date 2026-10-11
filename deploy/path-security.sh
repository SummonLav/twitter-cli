#!/bin/sh
# Modified in the SummonLav fork; see FORK.md.
# Sourced only after install.sh has verified this file and its parents.
# Uses the installer's owner_uid, mode_bits and die helpers.

# Resolve a path without running the requested executable. Check each directory
# before following a child, and check every link and every target path. Checking
# only realpath(path) would miss a replaceable alias pointing at a trusted binary.
resolve_root_owned_path() (
    case "$1" in
        /*) pending=${1#/} ;;
        *) die "expected an absolute path: $1" ;;
    esac
    resolved=/
    links=0
    require_root_owned /
    while [ -n "$pending" ]; do
        component=${pending%%/*}
        case "$pending" in
            */*) pending=${pending#*/} ;;
            *) pending= ;;
        esac
        case "$component" in
            ''|.) continue ;;
            ..) resolved=$(dirname "$resolved"); continue ;;
        esac
        candidate=${resolved%/}/$component
        [ -e "$candidate" ] || [ -L "$candidate" ] || die "missing: $candidate"
        [ "$(owner_uid "$candidate")" = 0 ] || die "$candidate must be owned by root"
        if [ -L "$candidate" ]; then
            links=$((links + 1))
            [ "$links" -le 40 ] || die "too many symlinks in $1"
            target=$(readlink "$candidate") || die "cannot read symlink: $candidate"
            case "$target" in
                /*) resolved=/; target=${target#/} ;;
            esac
            pending=$target${pending:+/$pending}
        else
            bits=$(mode_bits "$candidate")
            [ $((0$bits & 022)) -eq 0 ] || die "$candidate is writable by group or others"
            resolved=$candidate
            [ -z "$pending" ] || [ -d "$resolved" ] || die "not a directory: $resolved"
        fi
    done
    printf '%s\n' "$resolved"
)

# SPDX-License-Identifier: GPL-3.0-or-later
# Source after setting root to the Eden port. Prints the folder that holds the three driver
# checkouts tools/deps.json names (mihawk-vulkan-review, mihawk-mesa-review, mihawk-sdk-review):
# EDEN_RADV_REFS when set, else the first line of .local/radv-refs (relative to the port), else
# the port's parent folder, where every worktree of this repository shares them.
#
# A worktree that builds another driver revision than the others keeps its own checkouts (and the
# RADV built inside them) this way, so moving its pin does not break the others' builds.
# tools/radv_refs.py follows the same rule for the Python tools.
eden_radv_refs() {
    local refs=${EDEN_RADV_REFS:-}
    if [[ -z $refs && -f "$root/.local/radv-refs" ]]; then
        refs=$(head -n 1 "$root/.local/radv-refs")
    fi
    if [[ -z $refs ]]; then
        dirname "$root"
        return
    fi
    [[ $refs == /* ]] || refs="$root/$refs"
    (cd -- "$refs" && pwd)
}

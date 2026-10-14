# Shared by the hooks: scan stdin against the private denylist.
# The denylist lives outside the repository on purpose (see
# scripts/denylist_scan.py); a missing or empty one blocks the operation.

denylist_path() {
    printf '%s' "${LECTURE_STUDY_DENYLIST:-$HOME/.config/lecture-to-study-pipeline/denylist.txt}"
}

scan_file_against_denylist() {
    python3 -I "$(git rev-parse --show-toplevel)/scripts/denylist_scan.py" \
        "$(denylist_path)" < "$1"
}

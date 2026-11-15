#!/usr/bin/env bats
# The deployment record and the install-wide lock (installer/deployments.sh, issue #110, stages A/B).
#
# Every update, build and setup records which backend and frontend images run, by immutable image ID, as the
# current generation, and the one before as the previous generation. A tag is not identity: a same-tag rebuild or
# a one-service build is a new generation. Writes are atomic and private. An operation in progress is recorded as
# the candidate until the new images run, so an interrupted operation is visible to the next one. Only one
# deployment-changing operation runs at a time. Nothing is deleted at these stages.

LIB="$BATS_TEST_DIRNAME/../../installer/deployments.sh"

setup() {
    WORK="$(mktemp -d)"; export WORK
    export SCRIPT_DIR="$WORK"
    export FAKE_IDS="$WORK/ids"
    mkdir -p "$FAKE_IDS"
    # docker compose ps -q <svc> prints a container id; docker inspect prints its image id (or fails).
    cat > "$WORK/fake-dc" <<'STUB'
#!/usr/bin/env bash
case "$*" in
    *"ps -q backend"*) [ -s "$FAKE_IDS/backend" ] && echo "cid-backend" ;;
    *"ps -q frontend"*) [ -s "$FAKE_IDS/frontend" ] && echo "cid-frontend" ;;
esac
exit 0
STUB
    cat > "$WORK/fake-docker" <<'STUB'
#!/usr/bin/env bash
[ "$1" = inspect ] || exit 1
svc="${*: -1}"; svc="${svc#cid-}"
[ -s "$FAKE_IDS/$svc" ] || exit 1
cat "$FAKE_IDS/$svc"
STUB
    chmod +x "$WORK/fake-dc" "$WORK/fake-docker"
    export DC="$WORK/fake-dc" DEPLOY_DOCKER="$WORK/fake-docker"
}

teardown() {
    [ -n "${HOLDER:-}" ] && { pkill -P "$HOLDER" 2>/dev/null; kill "$HOLDER" 2>/dev/null; } || true
    rm -rf "$WORK"
}

running() { echo "sha256:$1" > "$FAKE_IDS/backend"; echo "sha256:$2" > "$FAKE_IDS/frontend"; }
state() { python3 -c "import json,sys; d=json.load(open('$WORK/host-state/deployments.json')); print(eval(sys.argv[1]))" "$1"; }
lib() { bash -c "source '$LIB'; $*"; }
mode() { python3 -c 'import os, sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777)[2:])' "$1"; }

@test "the first record makes the running pair the current generation, privately" {
    running b1 f1
    run lib deploy_record_running v1.0.0
    [ "$status" -eq 0 ]
    [ "$(state "d['current']['backend']")" = "sha256:b1" ]
    [ "$(state "d['current']['frontend']")" = "sha256:f1" ]
    [ "$(state "d['current']['tag']")" = "v1.0.0" ]
    [ "$(state "d['previous']")" = "None" ]
    [ "$(mode "$WORK/host-state")" = 700 ]
    [ "$(mode "$WORK/host-state/deployments.json")" = 600 ]
}

@test "a new pair becomes current and the old current becomes previous" {
    running b1 f1; lib deploy_record_running v1.0.0
    running b2 f2; lib deploy_record_running v1.1.0
    [ "$(state "d['current']['backend']")" = "sha256:b2" ]
    [ "$(state "d['previous']['backend']")" = "sha256:b1" ]
    [ "$(state "d['previous']['tag']")" = "v1.0.0" ]
}

@test "recording the same pair again changes nothing" {
    running b1 f1; lib deploy_record_running v1.0.0
    running b2 f2; lib deploy_record_running v1.1.0
    before="$(cat "$WORK/host-state/deployments.json")"
    lib deploy_record_running v1.1.0
    [ "$(cat "$WORK/host-state/deployments.json")" = "$before" ]
}

@test "a same-tag rebuild is a new generation; the tag is not its identity" {
    running b1 f1; lib deploy_record_running v1.0.0
    running b1x f1; lib deploy_record_running v1.0.0
    [ "$(state "d['current']['backend']")" = "sha256:b1x" ]
    [ "$(state "d['previous']['backend']")" = "sha256:b1" ]
    [ "$(state "d['current']['frontend']")" = "sha256:f1" ]
}

@test "a missing running service records nothing" {
    running b1 f1; lib deploy_record_running v1.0.0
    rm "$FAKE_IDS/frontend"
    before="$(cat "$WORK/host-state/deployments.json")"
    run lib deploy_record_running v2.0.0
    [ "$status" -ne 0 ]
    [ "$(cat "$WORK/host-state/deployments.json")" = "$before" ]
}

@test "a candidate stays recorded until its images run, so an interruption is visible" {
    running b1 f1; lib deploy_record_running v1.0.0
    lib deploy_mark_candidate update v1.1.0
    [ "$(state "d['candidate']['tag']")" = "v1.1.0" ]
    [ "$(state "d['candidate']['operation']")" = "update" ]
    run lib deploy_interrupted
    [ "$status" -eq 0 ]
    [[ "$output" == *"update to v1.1.0"* ]]
    running b2 f2; lib deploy_record_running v1.1.0
    [ "$(state "d['candidate']")" = "None" ]
    run lib deploy_interrupted
    [ "$status" -ne 0 ]
}

@test "a damaged record is never overwritten as if it were empty" {
    mkdir -m 700 "$WORK/host-state"
    echo "{not json" > "$WORK/host-state/deployments.json"
    running b1 f1
    run lib deploy_record_running v1.0.0
    [ "$status" -ne 0 ]
    [ "$(cat "$WORK/host-state/deployments.json")" = "{not json" ]
}

@test "a second deployment operation is refused while one holds the lock" {
    mkdir -m 700 "$WORK/host-state"
    bash -c "source '$LIB'; deploy_lock && sleep 30" &
    HOLDER=$!
    for _ in $(seq 1 50); do
        [ -e "$WORK/host-state/deployment.lock" ] && break
        sleep 0.1
    done
    sleep 0.5
    run lib deploy_lock
    [ "$status" -eq 75 ]
    pkill -P "$HOLDER" 2>/dev/null || true; kill "$HOLDER" 2>/dev/null || true; wait "$HOLDER" 2>/dev/null || true
    HOLDER=""
    run lib deploy_lock
    [ "$status" -eq 0 ]
}

# --- stage C: retention (owner-approved budgets) ------------------------------------------------------------

# A docker stand-in for retention: images listed by label from $FAKE_IDS/images ("<id> <ref>" lines), images in
# use by any container from $FAKE_IDS/in-use, removals recorded in $WORK/removed, builder prunes in $WORK/pruned.
retention_docker() {
    cat > "$WORK/fake-docker" <<'STUB'
#!/usr/bin/env bash
case "$1" in
    inspect)
        last="${*: -1}"
        case "$last" in img:*) echo "${last#img:}"; exit 0 ;; esac
        case "$last" in ghcr.io/bioaf/*) grep -F " $last" "$FAKE_IDS/images" | cut -d' ' -f1; exit 0 ;; esac
        svc="${last#cid-}"
        [ -s "$FAKE_IDS/$svc" ] || exit 1
        cat "$FAKE_IDS/$svc" ;;
    images)
        [[ "$*" == *"label=org.opencontainers.image.source=https://github.com/bioAF/bioAF"* ]] || exit 1
        cut -d' ' -f1 "$FAKE_IDS/images" | sort -u ;;
    ps) sed 's/^/img:/' "$FAKE_IDS/in-use" 2>/dev/null ;;
    rmi) echo "$2" >> "$WORK/removed" ;;
    builder)
        shift
        case "$1" in
            prune) echo "$*" >> "$WORK/pruned" ;;
            du) echo "Total: $(cat "$FAKE_IDS/cache-bytes" 2>/dev/null || echo 0)" ;;
        esac ;;
    system)
        [ "$2" = df ] && echo "$(cat "$FAKE_IDS/cache-bytes" 2>/dev/null || echo 0)" ;;
esac
exit 0
STUB
    chmod +x "$WORK/fake-docker"
    : > "$FAKE_IDS/images"
}

@test "pruning removes only bioAF images that no generation or container uses" {
    retention_docker
    running b3 f3
    printf '%s\n' "sha256:b1 old" "sha256:b2 prev" "sha256:f2 prev" "sha256:b3 cur" "sha256:f3 cur" "sha256:bx stopped" > "$FAKE_IDS/images"
    mkdir -m 700 "$WORK/host-state"
    running b2 f2; lib deploy_record_running v1.0.0
    running b3 f3; lib deploy_record_running v1.1.0
    echo "sha256:bx" > "$FAKE_IDS/in-use"    # a stopped container's image is never removed
    run lib deploy_prune_images
    [ "$status" -eq 0 ]
    [ "$(cat "$WORK/removed")" = "sha256:b1" ]
}

@test "pruning removes nothing when the record cannot be read" {
    retention_docker
    printf '%s\n' "sha256:b1 old" > "$FAKE_IDS/images"
    mkdir -m 700 "$WORK/host-state"
    echo "{not json" > "$WORK/host-state/deployments.json"
    run lib deploy_prune_images
    [ ! -e "$WORK/removed" ]
    [[ "$output" == *"left in place"* ]]
}

@test "a build cache over its budget is reported, never pruned (review finding 6)" {
    retention_docker
    echo 7516192768 > "$FAKE_IDS/cache-bytes"   # 7 GiB, over the 5 GiB budget
    run lib deploy_report_build_cache
    [ "$status" -eq 0 ]
    [ ! -e "$WORK/pruned" ]
    [[ "$output" == *"7.0 GiB"* ]] && [[ "$output" == *"docker builder prune"* ]]
    echo 1073741824 > "$FAKE_IDS/cache-bytes"
    run lib deploy_report_build_cache
    [ -z "$output" ] && [ ! -e "$WORK/pruned" ]
}

@test "an update needs room for the new images and a backup above the floor" {
    gib=$((1024 * 1024 * 1024))
    # 100 GiB disk: floor 10 GiB. Needs 3.5 GiB + 1.5 x 2 GiB = 6.5 GiB.
    run bash -c "source '$LIB'; _deploy_disk_usage() { echo $((100 * gib)) $((17 * gib)); }; deploy_require_space $((2 * gib))"
    [ "$status" -eq 0 ]
    run bash -c "source '$LIB'; _deploy_disk_usage() { echo $((100 * gib)) $((16 * gib)); }; deploy_require_space $((2 * gib))"
    [ "$status" -ne 0 ]
    [[ "$output" == *"free"* ]]
}

@test "only the two newest eligible updater dump sets are kept, never a manual backup" {
    b="$WORK/backups"; mkdir -p "$b"
    mkdir -m 700 "$WORK/host-state"
    running b1 f1; lib deploy_record_running v1.0.0   # a record exists, with no recovery sets yet
    n=0
    for stamp in 20261101 20261102 20261103; do
        n=$((n + 1))
        touch -t "2026110${n}0000" "$b/bioaf_pre_update_1_to_2_${stamp}.sql.gz" \
            "$b/bioaf_pre_upgrade_a_to_b_${stamp}.dump" "$b/bioaf_pre_upgrade_a_to_b_${stamp}.json"
    done
    touch -t 202610010000 "$b/bioaf_20261001-000000.sql.gz" "$b/notes.txt"
    ln -s /etc/hosts "$b/bioaf_pre_update_link.sql.gz"
    run lib deploy_rotate_dumps
    [ "$status" -eq 0 ]
    [ ! -e "$b/bioaf_pre_update_1_to_2_20261101.sql.gz" ]
    [ ! -e "$b/bioaf_pre_upgrade_a_to_b_20261101.dump" ] && [ ! -e "$b/bioaf_pre_upgrade_a_to_b_20261101.json" ]
    [ -e "$b/bioaf_pre_update_1_to_2_20261103.sql.gz" ] && [ -e "$b/bioaf_pre_update_1_to_2_20261102.sql.gz" ]
    [ -e "$b/bioaf_pre_upgrade_a_to_b_20261102.dump" ] && [ -e "$b/bioaf_pre_upgrade_a_to_b_20261102.json" ]
    [ -e "$b/bioaf_20261001-000000.sql.gz" ] && [ -e "$b/notes.txt" ] && [ -L "$b/bioaf_pre_update_link.sql.gz" ]
}

@test "images already tagged with the candidate's tag are kept for it" {
    retention_docker
    printf '%s\n' "sha256:b9 ghcr.io/bioaf/bioaf-backend:v2.0.0" "sha256:f9 ghcr.io/bioaf/bioaf-frontend:v2.0.0" "sha256:b1 old" > "$FAKE_IDS/images"
    mkdir -m 700 "$WORK/host-state"
    lib deploy_mark_candidate update v2.0.0
    run lib deploy_prune_images
    [ "$(cat "$WORK/removed")" = "sha256:b1" ]
}

@test "with no record yet, nothing is pruned (the generations are not known)" {
    retention_docker
    printf '%s\n' "sha256:b1 old" > "$FAKE_IDS/images"
    run lib deploy_prune_images
    [ ! -e "$WORK/removed" ]
    [[ "$output" == *"left in place"* ]]
}

# --- recovery sets (review finding 3) ---------------------------------------------------------------------------

@test "an update's backups are recorded as its generation's recovery set" {
    b="$WORK/backups"; mkdir -p "$b"
    mkdir -m 700 "$WORK/host-state"
    running b1 f1; lib deploy_record_running v1.0.0
    touch -t 202610010000 "$b/bioaf_pre_update_0_to_1_old.sql.gz"
    lib deploy_mark_candidate update v2.0.0
    touch "$b/bioaf_pre_update_1_to_2_now.sql.gz" "$b/bioaf_pre_upgrade_r1_to_r2_now.dump" "$b/bioaf_pre_upgrade_r1_to_r2_now.json"
    running b2 f2; lib deploy_record_running v2.0.0
    [ "$(state "sorted(d['current']['recovery'])")" = "['bioaf_pre_update_1_to_2_now.sql.gz', 'bioaf_pre_upgrade_r1_to_r2_now.dump', 'bioaf_pre_upgrade_r1_to_r2_now.json']" ]
}

@test "the backups needed to roll back survive newer failed attempts" {
    b="$WORK/backups"; mkdir -p "$b"
    mkdir -m 700 "$WORK/host-state"
    running b1 f1; lib deploy_record_running v1.0.0
    lib deploy_mark_candidate update v2.0.0
    touch "$b/bioaf_pre_update_1_to_2_needed.sql.gz" "$b/bioaf_pre_upgrade_r1_to_r2_needed.dump" "$b/bioaf_pre_upgrade_r1_to_r2_needed.json"
    running b2 f2; lib deploy_record_running v2.0.0
    touch -t 202601010000 "$b/bioaf_pre_update_1_to_2_needed.sql.gz" "$b/bioaf_pre_upgrade_r1_to_r2_needed.dump"
    for attempt in a1 a2 a3; do   # later attempts toward v3 that failed: newer than the needed set
        touch "$b/bioaf_pre_update_2_to_3_${attempt}.sql.gz" "$b/bioaf_pre_upgrade_r2_to_r3_${attempt}.dump" "$b/bioaf_pre_upgrade_r2_to_r3_${attempt}.json"
        sleep 1
    done
    run lib deploy_rotate_dumps
    [ "$status" -eq 0 ]
    [ -e "$b/bioaf_pre_update_1_to_2_needed.sql.gz" ]
    [ -e "$b/bioaf_pre_upgrade_r1_to_r2_needed.dump" ] && [ -e "$b/bioaf_pre_upgrade_r1_to_r2_needed.json" ]
    [ ! -e "$b/bioaf_pre_update_2_to_3_a1.sql.gz" ] && [ -e "$b/bioaf_pre_update_2_to_3_a3.sql.gz" ]
}

@test "without a readable record no updater backup is removed" {
    b="$WORK/backups"; mkdir -p "$b"
    for i in 1 2 3; do touch -t "2026110${i}0000" "$b/bioaf_pre_update_1_to_2_${i}.sql.gz"; done
    run lib deploy_rotate_dumps
    [ "$(ls "$b" | wc -l | tr -d ' ')" = 3 ]
    [[ "$output" == *"left in place"* ]]
}

@test "a retried update keeps its first start, so the first attempt's backup stays in its recovery set" {
    mkdir -m 700 "$WORK/host-state"
    lib deploy_mark_candidate update v2.0.0
    first="$(state "d['candidate']['started_at']")"
    sleep 1
    lib deploy_mark_candidate update v2.0.0
    [ "$(state "d['candidate']['started_at']")" = "$first" ]
    lib deploy_mark_candidate update v3.0.0
    [ "$(state "d['candidate']['started_at']")" != "$first" ]
}

@test "a pending update's backups are protected from rotation through failed retries (review follow-up 2)" {
    b="$WORK/backups"; mkdir -p "$b"
    mkdir -m 700 "$WORK/host-state"
    running b1 f1; lib deploy_record_running v1.0.0
    lib deploy_mark_candidate update v2.0.0
    for attempt in a1 a2 a3; do   # three attempts that each backed up and then failed
        touch "$b/bioaf_pre_update_1_to_2_${attempt}.sql.gz" "$b/bioaf_pre_upgrade_r1_to_r2_${attempt}.dump" "$b/bioaf_pre_upgrade_r1_to_r2_${attempt}.json"
        sleep 1
        lib deploy_mark_candidate update v2.0.0
    done
    run lib deploy_rotate_dumps
    [ "$status" -eq 0 ]
    [ "$(ls "$b" | wc -l | tr -d ' ')" = 9 ]
}

@test "a replaced unfinished update keeps its backups protected until a generation activates" {
    b="$WORK/backups"; mkdir -p "$b"
    mkdir -m 700 "$WORK/host-state"
    running b1 f1; lib deploy_record_running v1.0.0
    lib deploy_mark_candidate update v2.0.0
    touch "$b/bioaf_pre_update_1_to_2_first.sql.gz"; sleep 1
    lib deploy_mark_candidate update v3.0.0          # a different update replaces the unfinished one
    for n in 1 2 3; do touch "$b/bioaf_pre_update_1_to_3_${n}.sql.gz"; sleep 1; done
    lib deploy_rotate_dumps
    [ -e "$b/bioaf_pre_update_1_to_2_first.sql.gz" ]
    running b3 f3; lib deploy_record_running v3.0.0
    [[ "$(state "d['current']['recovery']")" == *"bioaf_pre_update_1_to_2_first.sql.gz"* ]]
}

# --- generation tags: rollback images survive a same-tag rebuild (owner review, 2026-11-15) ---------------------
#
# Docker's containerd image store deletes an image as soon as its last tag moves, even while a container runs it,
# so an image ID in the record does not keep the rollback images. Each recorded generation's images carry their
# own local tag (gen-<backend12>-<frontend12>), which also names them for a rollback.

# A docker stand-in with tags: $FAKE_IDS/images holds "<id> <ref>" lines ("<id> -" for an untagged image); an
# image exists while it has a line. tag moves a ref, rmi <ref> removes one (and the image with its last ref).
tag_docker() {
    cat > "$WORK/fake-docker" <<'STUB'
#!/usr/bin/env bash
img="$FAKE_IDS/images"
case "$1" in
    inspect)
        last="${*: -1}"
        case "$last" in cid-*)
            svc="${last#cid-}"; [ -s "$FAKE_IDS/$svc" ] || exit 1; cat "$FAKE_IDS/$svc"; exit 0 ;;
        esac
        grep -q "^$last " "$img" || exit 1; echo "$last" ;;
    image)
        [ "$2" = inspect ] || exit 1
        grep -q "^${*: -1} " "$img" ;;
    tag)
        grep -q "^$2 " "$img" || exit 1
        grep -v " $3\$" "$img" > "$img.new"; echo "$2 $3" >> "$img.new"; mv "$img.new" "$img" ;;
    rmi)
        echo "$2" >> "$WORK/removed"
        grep -v " $2\$" "$img" > "$img.new"; mv "$img.new" "$img" ;;
    images)
        [[ "$*" == *"label=org.opencontainers.image.source=https://github.com/bioAF/bioAF"* ]] || exit 1
        if [[ "$*" == *"{{.Repository}}:{{.Tag}}"* ]]; then cut -d' ' -f2 "$img" | grep -v '^-$'
        else cut -d' ' -f1 "$img" | sort -u; fi ;;
    ps) sed 's/^/img:/' "$FAKE_IDS/in-use" 2>/dev/null ;;
esac
exit 0
STUB
    chmod +x "$WORK/fake-docker"
    : > "$FAKE_IDS/images"
    mkdir -m 700 "$WORK/host-state"
}

images() { printf '%s\n' "$@" >> "$FAKE_IDS/images"; }
has_ref() { grep -q "^sha256:$1 ghcr.io/bioaf/bioaf-$2\$" "$FAKE_IDS/images"; }
gone() { ! grep -q "^sha256:$1 " "$FAKE_IDS/images"; }
# What ./bioaf build does to the version tag: it moves to the new image.
rebuild() { lib "_deploy_docker tag sha256:$1 ghcr.io/bioaf/bioaf-$2"; }

@test "each generation's images carry its own tag, so a same-tag rebuild keeps the rollback images" {
    tag_docker
    images "sha256:b1 ghcr.io/bioaf/bioaf-backend:v1" "sha256:f1 ghcr.io/bioaf/bioaf-frontend:v1" "sha256:b2 -"
    running b1 f1; lib deploy_record_generation v1
    [ "$(state "d['current']['local_tag']")" = "gen-b1-f1" ]
    has_ref b1 backend:gen-b1-f1 && has_ref f1 frontend:gen-b1-f1
    rebuild b2 backend:v1                     # the version tag leaves b1; on containerd that alone deleted it
    running b2 f1; lib deploy_record_generation v1
    has_ref b1 backend:gen-b1-f1 && has_ref f1 frontend:gen-b1-f1
    has_ref b2 backend:gen-b2-f1 && has_ref f1 frontend:gen-b2-f1
    [ "$(state "d['previous']['local_tag']")" = "gen-b1-f1" ]
    [ "$(state "d['previous']['images_present']")" = "True" ]
}

@test "the tags are in place before a build moves the version tag (records made before tags existed)" {
    tag_docker
    images "sha256:b1 ghcr.io/bioaf/bioaf-backend:v1" "sha256:f1 ghcr.io/bioaf/bioaf-frontend:v1"
    running b1 f1; lib deploy_record_running v1   # an older install: generations recorded, nothing tagged
    run lib deploy_sync_generation_tags
    [ "$status" -eq 0 ]
    has_ref b1 backend:gen-b1-f1 && has_ref f1 frontend:gen-b1-f1
    [ "$(state "d['current']['local_tag']")" = "gen-b1-f1" ]
}

@test "staging syncs the generation tags before it prunes, and finishing records them" {
    BIOAF="$BATS_TEST_DIRNAME/../../bioaf"
    body="$(sed -n '/^deploy_retention_before_staging()/,/^}/p' "$BIOAF")"
    sync_line="$(grep -n 'deploy_sync_generation_tags' <<< "$body" | head -1 | cut -d: -f1)"
    prune_line="$(grep -n 'deploy_prune_images' <<< "$body" | head -1 | cut -d: -f1)"
    [ -n "$sync_line" ] && [ -n "$prune_line" ] && [ "$sync_line" -lt "$prune_line" ]
    sed -n '/^deploy_finish()/,/^}/p' "$BIOAF" | grep -q 'deploy_record_generation'
}

@test "a generation that leaves the record loses its tag, and only bioAF generation tags are touched" {
    tag_docker
    images "sha256:b1 -" "sha256:f1 -" "sha256:b2 -" "sha256:b3 -" "sha256:b1 ghcr.io/bioaf/bioaf-backend:keep-me"
    running b1 f1; lib deploy_record_generation v1
    running b2 f1; lib deploy_record_generation v1
    running b3 f1; lib deploy_record_generation v1
    ! has_ref b1 backend:gen-b1-f1 && ! has_ref f1 frontend:gen-b1-f1
    has_ref b1 backend:keep-me
    has_ref b2 backend:gen-b2-f1 && has_ref b3 backend:gen-b3-f1 && has_ref f1 frontend:gen-b3-f1
}

@test "a generation whose images are gone is marked absent, never claimed as a rollback" {
    tag_docker
    images "sha256:b2 -" "sha256:f2 -"
    running b1 f1; lib deploy_record_running v1    # b1/f1 were already deleted (a same-tag rebuild before tags)
    running b2 f2; lib deploy_record_running v1
    run lib deploy_sync_generation_tags
    [ "$status" -eq 0 ]
    [ "$(state "d['previous']['images_present']")" = "False" ]
    [ "$(state "d['current']['images_present']")" = "True" ]
    [[ "$output" == *"previous"* ]] && [[ "$output" == *"no longer on this host"* ]]
    [ "$(state "d['previous']['backend']")" = "sha256:b1" ]   # its recovery set stays attributable
}

@test "rolling back to the previous generation's tag swaps current and previous" {
    tag_docker
    images "sha256:b1 -" "sha256:f1 -" "sha256:b2 -"
    running b1 f1; lib deploy_record_generation v1
    running b2 f1; lib deploy_record_generation v1
    touch "$WORK/backups-marker"
    running b1 f1; lib deploy_record_generation gen-b1-f1    # BIOAF_IMAGE_TAG=gen-b1-f1, then restart
    [ "$(state "d['current']['backend']")" = "sha256:b1" ]
    [ "$(state "d['current']['local_tag']")" = "gen-b1-f1" ]
    [ "$(state "d['previous']['backend']")" = "sha256:b2" ]
    has_ref b1 backend:gen-b1-f1 && has_ref b2 backend:gen-b2-f1
}

@test "the pinned tag is never removed, even when no generation names it" {
    tag_docker
    mkdir -p "$WORK/docker"; echo "BIOAF_IMAGE_TAG=gen-b0-f0" > "$WORK/docker/.env"
    images "sha256:b0 ghcr.io/bioaf/bioaf-backend:gen-b0-f0" "sha256:b1 -" "sha256:f1 -"
    running b1 f1; lib deploy_record_generation v1
    has_ref b0 backend:gen-b0-f0
}

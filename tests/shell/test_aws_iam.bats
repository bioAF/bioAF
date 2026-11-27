#!/usr/bin/env bats
# install-aws.sh permissions (security plan 04B): a fresh install creates the role boundary and the
# installer-owned managed policies before attaching the app's, and tags the VM with its installation; --reconcile-permissions on an
# existing installation lists what it will claim, changes nothing without confirmation, and then
# bounds and tags only bioAF's own roles and resources before replacing the app policy. Every AWS
# call is a stub that records its arguments; nothing reaches AWS.

REPO="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"

setup_file() {
    local dir tool name
    export SYSTEM_BIN="$BATS_FILE_TMPDIR/system-bin"
    mkdir -p "$SYSTEM_BIN"
    for dir in /usr/bin /bin /usr/sbin /sbin; do
        for tool in "$dir"/*; do
            name="${tool##*/}"
            case "$name" in gcloud | gsutil | bq | aws | aws_completer | brew | curl | sleep) continue ;; esac
            [ -e "$SYSTEM_BIN/$name" ] || [ -L "$SYSTEM_BIN/$name" ] || ln -s "$tool" "$SYSTEM_BIN/$name"
        done
    done
}

setup() {
    TEST_DIR="$(mktemp -d)"
    export TEST_DIR
    export HOME="$TEST_DIR/home"
    export STUBS_DIR="$TEST_DIR/stubs"
    export CALL_LOG="$TEST_DIR/calls.log"
    export DOCS="$TEST_DIR/docs"
    export BIOAF_TTY="$TEST_DIR/tty"
    mkdir -p "$HOME" "$STUBS_DIR" "$DOCS"
    : > "$CALL_LOG"; : > "$BIOAF_TTY"
    export PATH="$STUBS_DIR:$SYSTEM_BIN"
    unset BIOAF_INSTALLER_REF AWS_DEFAULT_REGION AWS_REGION AWS_PROFILE
    printf '#!/usr/bin/env bash\necho Linux\n' > "$STUBS_DIR/uname"
    printf '#!/usr/bin/env bash\nexit 0\n' > "$STUBS_DIR/sleep"
    cat > "$STUBS_DIR/curl" <<'STUB'
#!/usr/bin/env bash
echo "curl $*" >> "$CALL_LOG"
case "$*" in *checkip.amazonaws.com*) printf '203.0.113.7'; exit 0 ;; esac
exit 1
STUB

    # aws: FAKE_EXISTING=1 is an installed account (bioaf-app, a VM, bioAF roles and resources);
    # FAKE_BOUNDARY=1 means the boundary policy already exists with FAKE_VERSIONS old versions.
    # Policy documents passed as file:// are copied into $DOCS for inspection.
    cat > "$STUBS_DIR/aws" <<'STUB'
#!/usr/bin/env bash
echo "aws $*" >> "$CALL_LOG"
for arg in "$@"; do
    case "$arg" in file://*) f="${arg#file://}"; [ -f "$f" ] && cp "$f" "$DOCS/$1-$2-$RANDOM$RANDOM" ;; esac
done
case "$*" in
    "--version") echo "aws-cli/2.31.0" ;;
    "sts get-caller-identity") ;;
    *"sts get-caller-identity --query Account"*) echo 123456789012 ;;
    *"sts get-caller-identity --query Arn"*) echo "arn:aws:iam::123456789012:user/me" ;;
    "configure get region") echo us-west-1 ;;
    *describe-vpcs*) echo vpc-default ;;
    *describe-subnets*) echo subnet-1 ;;
    *describe-key-pairs*) exit 255 ;;
    *create-key-pair*) echo KEY-MATERIAL ;;
    *describe-security-groups*IpPermissions*) ;;
    *describe-security-groups*) echo None ;;
    *create-security-group*) echo sg-1 ;;
    *"iam get-role "*) [ "${FAKE_EXISTING:-0}" = 1 ] || exit 254 ;;
    *"iam get-instance-profile"*--query*) echo bioaf-app ;;
    *"iam get-instance-profile"*) exit 254 ;;
    *"iam get-policy "*) [ "${FAKE_BOUNDARY:-0}" = 1 ] || exit 254 ;;
    *"iam list-policy-versions"*) n="${FAKE_VERSIONS:-0}"; out=""; for i in $(seq "$n" -1 1); do out="$out v$i"; done; echo "${out# }" ;;
    *"iam get-role-policy"*bioaf-app-starter*) [ "${FAKE_EXISTING:-0}" = 1 ] || exit 254 ;;
    *"iam get-role-policy"*) exit 254 ;;
    *"iam list-instance-profiles-for-role --role-name bioaf-acme-1-node"*) echo bioaf-work-node-1 ;;
    *"iam list-instance-profiles-for-role"*) ;;
    *"iam list-roles"*) printf 'bioaf-app\tbioaf-acme-1-node\tbioaf-codebuild-image-build\tbioaf-ops-made\n' ;;
    *"iam list-role-tags --role-name bioaf-ops-made"*) echo 0 ;;
    *"iam list-role-tags"*) echo 1 ;;
    *"resourcegroupstaggingapi get-resources"*managed_by*) printf 'arn:aws:ec2:us-west-1:123456789012:vpc/vpc-c\tarn:aws:ec2:us-west-1:123456789012:subnet/subnet-c\n' ;;
    *"resourcegroupstaggingapi get-resources"*bioaf-managed*) printf 'arn:aws:ec2:us-west-1::image/ami-w\n' ;;
    *"ssm get-parameters"*) echo ami-1 ;;
    *"describe-instances"*"--instance-ids"*VpcId*) echo vpc-app ;;
    *describe-instances*) if [ "${FAKE_EXISTING:-0}" = 1 ]; then echo i-app; else echo None; fi ;;
    *"run-instances"*) echo i-new ;;
esac
exit 0
STUB
    chmod +x "$STUBS_DIR"/*
}

teardown() {
    rm -rf "$TEST_DIR"
}

answers() { printf '%s\n' "$@" > "$BIOAF_TTY"; }

calls() { grep "^aws $1" "$CALL_LOG" || true; }

line_of() { grep -n -- "$1" "$CALL_LOG" | head -1 | cut -d: -f1; }

app_policy() { cat "$(ls "$DOCS"/iam-* | while read -r f; do grep -l '"Sid":"Ec2ChangeOwned"' "$f"; done | tail -1)"; }

app_policies=(bioaf-app-access-core bioaf-app-access-control bioaf-app-access-roles bioaf-app-access-services)

@test "a fresh install creates the boundary and managed policies, then attaches the app's" {
    answers n
    run bash "$REPO/install-aws.sh" </dev/null
    [ "$status" -eq 0 ]
    [ -n "$(calls 'iam create-policy --policy-name bioaf-app-created-role-boundary')" ]
    for name in bioaf-role-s3-readwrite bioaf-role-s3-readonly bioaf-role-cluster-autoscaler bioaf-role-image-build "${app_policies[@]}"; do
        [ -n "$(calls "iam create-policy --policy-name $name")" ]
    done
    for name in "${app_policies[@]}"; do
        [ -n "$(calls "iam attach-role-policy --role-name bioaf-app --policy-arn arn:aws:iam::123456789012:policy/$name")" ]
    done
    # Managed policies only: nothing is written inline on the app role.
    [ -z "$(calls 'iam put-role-policy')" ]
    [ "$(line_of 'create-policy --policy-name bioaf-app-created-role-boundary')" -lt "$(line_of 'attach-role-policy --role-name bioaf-app')" ]
    policy="$(app_policy)"
    [[ "$policy" == *'"ec2:Vpc":"arn:aws:ec2:us-west-1:123456789012:vpc/vpc-default"'* ]]
    [[ "$policy" != *'"ec2:*"'* ]]
}

@test "a launched VM carries the installation tag" {
    answers y n n n
    run bash "$REPO/install-aws.sh" </dev/null
    launch="$(calls 'ec2 run-instances')"
    [[ "$launch" == *"Key=bioaf:installation,Value=123456789012-us-west-1"* ]]
}

@test "an existing boundary gets a new default version, pruning the oldest at IAM's limit" {
    answers n
    FAKE_BOUNDARY=1 FAKE_VERSIONS=4 run bash "$REPO/install-aws.sh" </dev/null
    [ -n "$(calls 'iam delete-policy-version --policy-arn arn:aws:iam::123456789012:policy/bioaf-app-created-role-boundary --version-id v1')" ]
    [ -n "$(calls 'iam create-policy-version')" ]
    [[ "$(calls 'iam create-policy-version')" == *"--set-as-default"* ]]
    [ -z "$(calls 'iam create-policy --policy-name')" ]
}

@test "reconcile changes nothing unless the operator confirms" {
    answers n
    FAKE_EXISTING=1 run bash "$REPO/install-aws.sh" --reconcile-permissions </dev/null
    [ "$status" -eq 0 ]
    [[ "$output" == *"bioaf-acme-1-node"* ]]
    [[ "$output" == *"vpc/vpc-c"* ]]
    [[ "$output" == *"Nothing was changed"* ]]
    [ -z "$(calls 'iam put-role')" ]
    [ -z "$(calls 'iam tag-role')" ]
    [ -z "$(calls 'resourcegroupstaggingapi tag-resources')" ]
    [ -z "$(calls 'iam create-policy')" ]
}

@test "reconcile bounds and tags only bioAF's own roles and resources, then replaces the app policy" {
    answers y
    FAKE_EXISTING=1 run bash "$REPO/install-aws.sh" --reconcile-permissions </dev/null
    [ "$status" -eq 0 ]
    boundary="arn:aws:iam::123456789012:policy/bioaf-app-created-role-boundary"
    [ -n "$(calls "iam put-role-permissions-boundary --role-name bioaf-acme-1-node --permissions-boundary $boundary")" ]
    [ -n "$(calls 'iam put-role-permissions-boundary --role-name bioaf-codebuild-image-build')" ]
    [ -z "$(calls 'iam put-role-permissions-boundary --role-name bioaf-app')" ]
    [ -z "$(calls 'iam put-role-permissions-boundary --role-name bioaf-ops-made')" ]
    [ -n "$(calls 'iam tag-role --role-name bioaf-acme-1-node --tags Key=bioaf:installation,Value=123456789012-us-west-1')" ]
    [ -n "$(calls 'iam tag-instance-profile --instance-profile-name bioaf-work-node-1 --tags Key=bioaf:installation,Value=123456789012-us-west-1')" ]
    tagged="$(calls 'resourcegroupstaggingapi tag-resources')"
    [[ "$tagged" == *"vpc/vpc-c"* && "$tagged" == *"subnet/subnet-c"* && "$tagged" == *"image/ami-w"* ]]
    [[ "$tagged" == *"bioaf:installation=123456789012-us-west-1"* ]]
    [ -n "$(calls 'ec2 create-tags --region us-west-1 --resources i-app')" ]
    for name in "${app_policies[@]}"; do
        [ -n "$(calls "iam attach-role-policy --role-name bioaf-app --policy-arn arn:aws:iam::123456789012:policy/$name")" ]
    done
    [ -n "$(calls 'iam delete-role-policy --role-name bioaf-app --policy-name bioaf-app-starter')" ]
    [ -z "$(calls 'iam put-role-policy')" ]
    [[ "$(app_policy)" == *'vpc/vpc-app'* ]]
    # The starter policy goes only after the new policies are attached, and attachment only after
    # the roles and resources are claimed.
    [ "$(line_of 'tag-resources')" -lt "$(line_of 'attach-role-policy --role-name bioaf-app')" ]
    [ "$(line_of 'attach-role-policy --role-name bioaf-app')" -lt "$(line_of 'delete-role-policy --role-name bioaf-app')" ]
    # Nothing else is created.
    [ -z "$(calls 'ec2 run-instances')" ]
    [ -z "$(calls 'ec2 create-key-pair')" ]
    [ -z "$(calls 'ec2 create-security-group')" ]
    [ -z "$(calls 'iam create-role')" ]
}

@test "reconcile without an installation stops before changing anything" {
    answers y
    run bash "$REPO/install-aws.sh" --reconcile-permissions </dev/null
    [ "$status" -ne 0 ]
    [[ "$output" == *"no installation to reconcile"* ]]
    [ -z "$(calls 'iam put-role')" ]
    [ -z "$(calls 'iam create-policy')" ]
    [ -z "$(calls 'iam attach-role-policy')" ]
}

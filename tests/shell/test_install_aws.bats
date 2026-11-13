#!/usr/bin/env bats
# install-aws.sh arguments and the values it provisions with (plan 23): both documented flag forms,
# early rejection of bad values, the effective region from any source, the SSH source range, and the
# prefill it writes. Every AWS call is a stub that records its arguments; nothing reaches AWS.

REPO="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"

setup_file() {
    local dir tool name
    export SYSTEM_BIN="$BATS_FILE_TMPDIR/system-bin"
    mkdir -p "$SYSTEM_BIN"
    for dir in /usr/bin /bin /usr/sbin /sbin; do
        for tool in "$dir"/*; do
            name="${tool##*/}"
            case "$name" in gcloud | gsutil | bq | aws | aws_completer | brew | curl | sleep) continue ;; esac
            [ -e "$SYSTEM_BIN/$name" ] || ln -s "$tool" "$SYSTEM_BIN/$name"
        done
    done
}

setup() {
    TEST_DIR="$(mktemp -d)"
    export TEST_DIR
    export HOME="$TEST_DIR/home"
    export STUBS_DIR="$TEST_DIR/stubs"
    export CALL_LOG="$TEST_DIR/calls.log"
    export BIOAF_TTY="$TEST_DIR/tty"
    mkdir -p "$HOME" "$STUBS_DIR"
    : > "$CALL_LOG"; : > "$BIOAF_TTY"
    export PATH="$STUBS_DIR:$SYSTEM_BIN"
    unset BIOAF_INSTALLER_REF AWS_DEFAULT_REGION AWS_REGION AWS_PROFILE
    printf '#!/usr/bin/env bash\necho Linux\n' > "$STUBS_DIR/uname"
    printf '#!/usr/bin/env bash\nexit 0\n' > "$STUBS_DIR/sleep"

    # curl: only the public-IP lookup. FAKE_MY_IP is its body, FAKE_IP_STATUS its exit.
    cat > "$STUBS_DIR/curl" <<'STUB'
#!/usr/bin/env bash
echo "curl $*" >> "$CALL_LOG"
case "$*" in
    *checkip.amazonaws.com*) printf '%s' "${FAKE_MY_IP-203.0.113.7}"; exit "${FAKE_IP_STATUS:-0}" ;;
esac
exit 1
STUB

    # aws: a new account with a default VPC. FAKE_CONFIG_REGION is the configured region,
    # FAKE_ACCOUNT the account ID, FAKE_SG an existing security group (default none), and
    # FAKE_SSH_CIDRS the SSH ranges that group already allows.
    cat > "$STUBS_DIR/aws" <<'STUB'
#!/usr/bin/env bash
echo "aws $*" >> "$CALL_LOG"
case "$*" in
    "--version") echo "aws-cli/2.31.0" ;;
    "sts get-caller-identity") ;;
    *"sts get-caller-identity --query Account"*) printf '%s\n' "${FAKE_ACCOUNT-123456789012}" ;;
    *"sts get-caller-identity --query Arn"*) echo "arn:aws:iam::123456789012:user/me" ;;
    "configure get region") [ -n "${FAKE_CONFIG_REGION-}" ] || exit 1; printf '%s\n' "$FAKE_CONFIG_REGION" ;;
    *describe-vpcs*) echo vpc-1 ;;
    *describe-subnets*) echo subnet-1 ;;
    *describe-key-pairs*) exit 255 ;;
    *create-key-pair*) echo KEY-MATERIAL ;;
    *describe-security-groups*IpPermissions*) printf '%s\n' "${FAKE_SSH_CIDRS-}" ;;
    *describe-security-groups*) echo "${FAKE_SG:-None}" ;;
    *create-security-group*) echo sg-1 ;;
    *"iam get-role"*) exit 254 ;;
    *"iam get-instance-profile"*--query*) echo bioaf-app ;;
    *"iam get-instance-profile"*) exit 254 ;;
    *"ssm get-parameters"*) echo ami-1 ;;
    *describe-instances*) echo None ;;
esac
exit 0
STUB
    chmod +x "$STUBS_DIR"/*
}

teardown() {
    rm -rf "$TEST_DIR"
}

answers() { printf '%s\n' "$@" > "$BIOAF_TTY"; }

# install [args...]: run the installer from its clone; decline the VM launch at the end.
install() {
    [ -s "$BIOAF_TTY" ] || answers n
    run bash "$REPO/install-aws.sh" "$@" </dev/null
}

ssh_rules() { grep -o 'IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=\[{CidrIp=[^}]*}\]' "$CALL_LOG" | sed 's/.*CidrIp=//; s/}\]$//'; }

no_aws_calls() { ! grep -q '^aws ' "$CALL_LOG"; }

# prefill_values: the prefill as key=value lines, parsed as YAML when PyYAML is present, else as
# flat "key: value" lines (the format the installer writes).
prefill_values() {
    python3 - "$HOME/.bioaf/prefill.yaml" <<'PY'
import sys
text = open(sys.argv[1]).read()
try:
    import yaml
    data = yaml.safe_load(text)
except ImportError:
    data = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            key, _, value = line.partition(": ")
            assert key not in data, key
            data[key] = value
for key in sorted(data):
    print(f"{key}={data[key]}")
PY
}

@test "the documented space-separated flags and the equals forms are both honored" {
    install --region us-west-2 --ssh-cidr 198.51.100.0/24
    [ "$status" -eq 0 ]
    [ "$(ssh_rules)" = 198.51.100.0/24 ]
    prefill_values | grep -qx 'aws_region=us-west-2'
    : > "$CALL_LOG"; rm -rf "$HOME/.bioaf"; answers n
    install --region=eu-west-1 --ssh-cidr=198.51.100.9/32
    [ "$status" -eq 0 ]
    [ "$(ssh_rules)" = 198.51.100.9/32 ]
    prefill_values | grep -qx 'aws_region=eu-west-1'
}

@test "a flag with a missing or empty value is refused before anything runs" {
    local args
    for args in "--region" "--ssh-cidr" "--region --ssh-cidr 1.2.3.4/32" "--region=" "--ssh-cidr=" "--ref"; do
        : > "$CALL_LOG"
        # shellcheck disable=SC2086
        install $args
        [ "$status" -eq 2 ]
        no_aws_calls
        [ ! -e "$HOME/.bioaf/prefill.yaml" ]
    done
    install --ssh-cidr ""
    [ "$status" -eq 2 ]
}

@test "a malformed SSH range is refused before anything runs" {
    local cidr
    for cidr in 203.0.113.7 256.1.1.1/32 1.2.3.4/33 01.2.3.4/32 1.2.3/24 '1.2.3.4/32,IpProtocol=-1' \
        '1.2.3.4/32}],IpRanges=[{CidrIp=0.0.0.0/0' ' 1.2.3.4/32' $'1.2.3.4/32\n' '1.2.3.4/032' '::/0' 'any'; do
        : > "$CALL_LOG"
        install --ssh-cidr "$cidr"
        [ "$status" -eq 2 ]
        [[ "$output" == *"--ssh-cidr"* ]]
        no_aws_calls
    done
}

@test "a malformed region flag is refused before anything runs" {
    local region
    for region in $'us-east-1\naws_account_id: 999' 'us-east-1: x' '../../tmp/x' 'US-EAST-1' 'us east 1' 'us-east-1"' \
        'us-east-1/x' 'useast1' '-us-east-1' $'us-east-1\r'; do
        : > "$CALL_LOG"
        install --region "$region"
        [ "$status" -eq 2 ]
        [[ "$output" == *"--region"* ]]
        no_aws_calls
    done
}

@test "a malformed region from the AWS configuration or the prompt stops before any key, rule or prefill" {
    FAKE_CONFIG_REGION=$'us-east-1\naws_account_id: 999' install --ssh-cidr 198.51.100.9/32
    [ "$status" -ne 0 ]
    [ ! -e "$HOME/.bioaf/prefill.yaml" ]
    ! grep -qE 'create-key-pair|create-security-group|authorize-security-group-ingress' "$CALL_LOG"
    [ -z "$(ls -A "$HOME/.ssh" 2>/dev/null)" ]
    : > "$CALL_LOG"
    answers '../../tmp/x' n
    install --ssh-cidr 198.51.100.9/32
    [ "$status" -ne 0 ]
    [ ! -e "$HOME/.bioaf/prefill.yaml" ]
    ! grep -qE 'create-key-pair|authorize-security-group-ingress' "$CALL_LOG"
}

@test "valid regions from the configuration or the prompt are used" {
    FAKE_CONFIG_REGION=ap-southeast-2 install --ssh-cidr 198.51.100.9/32
    [ "$status" -eq 0 ]
    prefill_values | grep -qx 'aws_region=ap-southeast-2'
    rm -rf "$HOME/.bioaf"; answers us-gov-west-1 n
    install --ssh-cidr 198.51.100.9/32
    [ "$status" -eq 0 ]
    prefill_values | grep -qx 'aws_region=us-gov-west-1'
}

@test "a detected public IP becomes a /32 SSH rule" {
    FAKE_MY_IP=$'203.0.113.7\n' install --region us-east-1
    [ "$status" -eq 0 ]
    [ "$(ssh_rules)" = 203.0.113.7/32 ]
}

@test "failed, empty or malformed IP detection opens no SSH rule and asks for --ssh-cidr" {
    local case
    for case in fail empty html partial multi v6; do
        : > "$CALL_LOG"; answers n
        case "$case" in
            fail) export FAKE_IP_STATUS=6 FAKE_MY_IP="" ;;
            empty) export FAKE_IP_STATUS=0 FAKE_MY_IP="" ;;
            html) export FAKE_IP_STATUS=0 FAKE_MY_IP="<html>error</html>" ;;
            partial) export FAKE_IP_STATUS=0 FAKE_MY_IP="203.0.11" ;;
            multi) export FAKE_IP_STATUS=0 FAKE_MY_IP=$'203.0.113.7\n198.51.100.1' ;;
            v6) export FAKE_IP_STATUS=0 FAKE_MY_IP="2001:db8::1" ;;
        esac
        install --region us-east-1
        [ "$status" -ne 0 ]
        [[ "$output" == *"--ssh-cidr"* ]]
        [ -z "$(ssh_rules)" ]
        ! grep -q '0.0.0.0/0}\]' <(grep 'FromPort=22' "$CALL_LOG")
        ! grep -q 'create-key-pair' "$CALL_LOG"
    done
}

@test "an explicit world-open SSH range is honored exactly, with a warning" {
    install --region us-east-1 --ssh-cidr 0.0.0.0/0
    [ "$status" -eq 0 ]
    [ "$(ssh_rules)" = 0.0.0.0/0 ]
    [[ "$output" == *"any address"* ]]
}

@test "a reused security group's broader SSH rules are reported, not removed or described as restricted" {
    FAKE_SG=sg-9 FAKE_SSH_CIDRS=$'0.0.0.0/0\t198.51.100.9/32' install --region us-east-1 --ssh-cidr 198.51.100.9/32
    [ "$status" -eq 0 ]
    [ "$(ssh_rules)" = 198.51.100.9/32 ]
    [[ "$output" == *"0.0.0.0/0"* ]]
    [[ "$output" == *"kept"* ]]
    ! grep -q 'revoke-security-group-ingress' "$CALL_LOG"
}

@test "the prefill holds exactly the intended scalar values" {
    install --region us-east-2 --ssh-cidr 198.51.100.9/32
    [ "$status" -eq 0 ]
    [ "$(prefill_values)" = "$(printf '%s\n' aws_account_id=123456789012 \
        aws_app_role_arn=arn:aws:iam::123456789012:role/bioaf-app aws_credential_source=instance_profile \
        aws_region=us-east-2 cloud_provider=aws)" ]
}

@test "an account ID that is not 12 digits stops before anything is provisioned" {
    FAKE_ACCOUNT=$'123456789012\nx: y' install --region us-east-1 --ssh-cidr 198.51.100.9/32
    [ "$status" -ne 0 ]
    [[ "$output" == *"not 12 digits"* ]]
    ! grep -qE 'create-key-pair|authorize-security-group-ingress' "$CALL_LOG"
    [ ! -e "$HOME/.bioaf/prefill.yaml" ]
}

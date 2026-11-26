"""The Work Node account-scoped sshd policy (plan 03B, D03-5 network OR key).

One root-owned include, ``00-bioaf-account-policy.conf``, holds only Match blocks:

- ``bioaf-sync`` is public-key-only from every source;
- with Work Node ranges, the scientist may use password or key from inside a range, and only a
  public key from anywhere else;
- with no range, the scientist keeps password or key from anywhere (today's behavior).

Match blocks override global settings for the connections they match, so an older image's global
``PasswordAuthentication yes`` cannot reopen password login outside a range, and no bioAF global
password override is written any more. Other accounts (the provider operator, guest key lookup,
required MFA) keep the image's own configuration.

On the node the result is validated with ``sshd -t`` and the effective settings with
``sshd -T -C`` for the scientist inside/outside and ``bioaf-sync``. If a range is configured and
the effective result is not what it promises (an earlier unmanaged Match, an old sshd), the
scientist becomes key-only everywhere (``failed_closed``) rather than leaving outside password
login open. The outcome is written to /etc/bioaf/ssh-policy-status and reported with heartbeats.

The policy does not change port 22 reachability: public Work Nodes keep their R05 exposure.
"""

from __future__ import annotations

import ipaddress
import re

POLICY_PATH = "/etc/ssh/sshd_config.d/00-bioaf-account-policy.conf"
STATUS_PATH = "/etc/bioaf/ssh-policy-status"
LEGACY_GLOBAL_OVERRIDE = "/etc/ssh/sshd_config.d/99-bioaf-password-auth.conf"
SYNC_USER = "bioaf-sync"
_USERNAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
# Documentation ranges (RFC 5737 / 6598 / 3849) used to probe "outside every range".
_OUTSIDE_CANDIDATES = ("192.0.2.1", "198.51.100.1", "203.0.113.1", "100.64.0.1", "2001:db8::1")


class SshPolicyError(ValueError):
    """A username or range that cannot be compiled safely."""


def validate_ranges(ranges: list[str]) -> list[str]:
    """Canonical networks; single addresses become /32 or /128. Universal ranges are refused."""
    canonical = []
    for raw in ranges:
        text = str(raw).strip()
        try:
            network = ipaddress.ip_network(text, strict=True)
        except ValueError:
            raise SshPolicyError("each range must be an IP address or a network in CIDR form") from None
        if network.prefixlen == 0:
            raise SshPolicyError("a range that allows every address is not a restriction")
        canonical.append(str(network))
    return canonical


def _validate_username(username: str) -> str:
    if not _USERNAME.fullmatch(username or "") or username == SYNC_USER:
        raise SshPolicyError("the login account name is not a safe Unix username")
    return username


def compile_account_policy(username: str, ranges: list[str]) -> str:
    user = _validate_username(username)
    networks = validate_ranges(ranges)
    lines = [
        "# Managed by bioAF (plan 03B). Account-scoped SSH login policy for this Work Node.",
        f"Match User {SYNC_USER}",
        "    AuthenticationMethods publickey",
        "    PasswordAuthentication no",
        "    KbdInteractiveAuthentication no",
    ]
    if networks:
        lines += [
            f"Match User {user} Address {','.join(networks)}",
            # Key or password. Not "any": OpenSSH 8.9 rejects the whole file when it evaluates a
            # Match block holding "AuthenticationMethods any" for another account.
            "    AuthenticationMethods publickey password",
            "    PasswordAuthentication yes",
            "    KbdInteractiveAuthentication no",
            "    PubkeyAuthentication yes",
            f"Match User {user}",
            "    AuthenticationMethods publickey",
            "    PasswordAuthentication no",
            "    KbdInteractiveAuthentication no",
            "    PubkeyAuthentication yes",
        ]
    else:
        lines += [
            # No AuthenticationMethods line: the image default (any) applies; see the note above.
            f"Match User {user}",
            "    PasswordAuthentication yes",
            "    KbdInteractiveAuthentication no",
            "    PubkeyAuthentication yes",
        ]
    lines.append("Match all")
    return "\n".join(lines) + "\n"


def _key_only_policy(username: str) -> str:
    return compile_account_policy(username, ["127.0.0.1/32"]).replace(
        "    AuthenticationMethods publickey password\n    PasswordAuthentication yes",
        "    AuthenticationMethods publickey\n    PasswordAuthentication no",
    )


def probe_addresses(ranges: list[str]) -> tuple[str, str]:
    """(an address inside the first range, an address outside every range) for effective checks."""
    networks = [ipaddress.ip_network(r) for r in validate_ranges(ranges)]
    first = networks[0]
    inside = str(first.network_address if first.num_addresses == 1 else next(first.hosts()))
    for candidate in _OUTSIDE_CANDIDATES:
        address = ipaddress.ip_address(candidate)
        if not any(address in n for n in networks if n.version == address.version):
            return inside, candidate
    raise SshPolicyError("the ranges leave no documentation address outside them to check")


def sshd_policy_install_lines(username: str, ranges: list[str]) -> list[str]:
    """Startup-script lines that install, validate and (with a range) fail closed."""
    policy = compile_account_policy(username, ranges)
    networks = validate_ranges(ranges)

    def effective(user: str, addr: str) -> str:
        return f'/usr/sbin/sshd -T -C "user={user},addr={addr},host=bioaf-check,laddr=127.0.0.1,lport=22" 2>/dev/null'

    lines = [
        "",
        "# 1b. Account-scoped SSH login policy (plan 03B); no global password override",
        f"rm -f {LEGACY_GLOBAL_OVERRIDE}",
        "mkdir -p /etc/bioaf",
        f"cat > {POLICY_PATH} <<'BIOAF_SSH_POLICY_EOF'",
        policy.rstrip("\n"),
        "BIOAF_SSH_POLICY_EOF",
        f"chown root:root {POLICY_PATH}",
        f"chmod 644 {POLICY_PATH}",
        # sshd -T -C prints nothing for an account that does not exist, so the sync account is created
        # (as the sync step below creates it) before its effective settings are checked.
        f"id {SYNC_USER} >/dev/null 2>&1 || useradd -r -m -s /bin/bash {SYNC_USER} || true",
        "bioaf_policy=applied",
        "/usr/sbin/sshd -t || bioaf_policy=failed",
    ]
    sync_checks = [
        f'{effective(SYNC_USER, "198.51.100.1")} | grep -qx "authenticationmethods publickey" || bioaf_policy=failed',
        f'{effective(SYNC_USER, "198.51.100.1")} | grep -qx "passwordauthentication no" || bioaf_policy=failed',
    ]
    if networks:
        inside, outside = probe_addresses(networks)
        lines += [
            f'{effective(username, outside)} | grep -qx "authenticationmethods publickey" || bioaf_policy=failed',
            f'{effective(username, outside)} | grep -qx "passwordauthentication no" || bioaf_policy=failed',
            f'{effective(username, outside)} | grep -qx "kbdinteractiveauthentication no" || bioaf_policy=failed',
            f'{effective(username, inside)} | grep -qx "passwordauthentication yes" || bioaf_policy=failed',
            *sync_checks,
            'if [ "$bioaf_policy" != applied ]; then',
            "  # The range cannot be enforced as promised: no password login from anywhere for this account.",
            f"  cat > {POLICY_PATH} <<'BIOAF_SSH_POLICY_EOF'",
            _key_only_policy(username).rstrip("\n"),
            "BIOAF_SSH_POLICY_EOF",
            "  bioaf_policy=failed_closed",
            "fi",
        ]
    else:
        lines += [
            f'{effective(username, "198.51.100.1")} | grep -qx "passwordauthentication yes" || bioaf_policy=failed',
            *sync_checks,
        ]
    lines += [
        f'echo "$bioaf_policy" > {STATUS_PATH}',
        "systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null"
        " || systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true",
    ]
    return lines

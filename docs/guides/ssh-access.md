# SSH Access to Running Containers

bioAF provides SSH and exec access to running compute containers, allowing bioinformaticians to inspect state, debug failing pipelines, test commands interactively, and develop new pipeline steps. All SSH sessions are authenticated, authorized, and audit-logged.

## Prerequisites

Before using SSH access, ensure the following:

- **Role requirement:** You must have the `admin` or `comp_bio` role. Bench scientists and viewers cannot access compute containers directly.
- **SSH key registered:** Your SSH public key must be uploaded to your bioAF profile. Navigate to **Profile > SSH Keys** and add your public key (typically `~/.ssh/id_ed25519.pub` or `~/.ssh/id_rsa.pub`).
- **Compute backend running:** The Kubernetes or SLURM compute backend must be provisioned and healthy. Check **Compute > Status** for a green health indicator.
- **Target container running:** You can only connect to containers that are currently executing a pipeline run or interactive session. Completed or failed containers are no longer accessible.

## First-Time Setup

### Step 1: Generate an SSH Key (if needed)

If you do not already have an SSH key pair, generate one:

```bash
ssh-keygen -t ed25519 -C "your-email@example.com"
```

Accept the default file location (`~/.ssh/id_ed25519`) and set a passphrase when prompted. The ed25519 algorithm is recommended for its security and performance.

### Step 2: Upload Your Public Key

Copy your public key to the clipboard:

```bash
cat ~/.ssh/id_ed25519.pub
```

In the bioAF UI, navigate to **Profile > SSH Keys** and click "Add Key." Paste the public key and give it a descriptive label (e.g., "Work Laptop 2026"). Click "Save."

bioAF distributes your public key to the compute backend within 60 seconds. You can verify by checking the key's status indicator -- it should show "Synced."

### Step 3: Configure Your SSH Client

Add the following block to your `~/.ssh/config` file to simplify future connections:

```text
Host bioaf-*
    User bioaf
    IdentityFile ~/.ssh/id_ed25519
    StrictHostKeyChecking accept-new
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

The `ServerAliveInterval` setting prevents idle disconnections. The `StrictHostKeyChecking accept-new` setting automatically accepts host keys for new containers without prompting.

## Connecting to Running Containers

### Option 1: Via the bioAF UI

The simplest way to connect is through the bioAF UI:

1. Navigate to **Pipelines > Runs** and find the running pipeline.
2. Click on the run to open its detail view.
3. Click the "Connect" button in the toolbar. bioAF displays the SSH command pre-filled with the correct host and port.
4. Copy the command and paste it into your terminal.

The displayed command looks like:

```bash
ssh bioaf@gateway.your-instance.bioaf.dev -p 2222 -t container-id
```

### Option 2: Via the CLI

If you prefer the command line:

```bash
# List running containers
bioaf compute list --running

# Connect to a specific container
bioaf compute ssh <container-id>
```

The CLI automatically resolves the container ID to the correct gateway host and port.

### Option 3: Direct SSH (Kubernetes)

For Kubernetes backends, bioAF runs an SSH gateway as a Kubernetes service. You can connect directly:

```bash
ssh -p 2222 bioaf@<gateway-external-ip> -t <pod-name>
```

The gateway IP is displayed in **Compute > Status > SSH Gateway**. The pod name is visible in the pipeline run detail view.

## What You Can Do Inside a Container

Once connected, you are in the container's working directory with the same environment as the pipeline:

- **Inspect files:** Browse input files, intermediate outputs, and logs.
- **Run commands:** Execute pipeline tools manually to debug issues (e.g., run a single Cell Ranger step with verbose logging).
- **Edit scripts:** Modify pipeline scripts in-place for testing. Changes are ephemeral -- they do not persist after the container terminates.
- **Monitor resources:** Use `top`, `htop`, or `nvidia-smi` to check CPU, memory, and GPU utilization.
- **Install packages:** Use `pip`, `conda`, or `apt-get` to install debugging tools. These are ephemeral and lost when the container terminates.

You cannot:

- Access other users' containers without their permission.
- Modify files outside the container's workspace mount.
- Escalate privileges beyond the bioaf user (containers run as non-root).

## Audit Trail

Every SSH session is recorded in the bioAF audit log. The following events are captured:

- **Session start:** Timestamp, user, source IP, target container/pod, pipeline run ID.
- **Session end:** Timestamp, duration.
- **Commands executed:** If command logging is enabled (see below), individual commands are recorded.

### Enabling Command Logging

Command logging is optional and disabled by default for privacy reasons. To enable it:

1. Navigate to **Settings > Security > SSH Access**.
2. Toggle "Log SSH Commands" to enabled.
3. Set the retention period for command logs (default: 90 days).

When enabled, all commands typed during SSH sessions are recorded and visible in the audit log at **Settings > Audit Log**. Users are informed at login with a banner message that command logging is active.

### Viewing Session History

To review SSH sessions for a specific pipeline run:

1. Open the pipeline run detail view.
2. Click the "Sessions" tab.
3. Each session shows the connecting user, start/end times, and (if command logging is enabled) a transcript of commands.

Alternatively, query the audit log directly at **Settings > Audit Log** with the filter `event_type = ssh.session_start`.

## Security Considerations

- SSH keys are stored encrypted in Google Secret Manager and synced to the compute backend via the BAL.
- The SSH gateway terminates connections when the underlying container/pod terminates. There is no way to connect to a container after it has stopped.
- Failed authentication attempts are logged and contribute to rate limiting. After 5 failed attempts from a single IP within 10 minutes, that IP is blocked for 1 hour.
- The SSH gateway only accepts key-based authentication. Password authentication is not supported.

## Tips

- Use `tmux` or `screen` inside the container if you expect your local connection to be interrupted. The session persists inside the container even if your SSH connection drops.
- For long debugging sessions, set a reminder that ephemeral containers will terminate when the pipeline completes or times out. Save any important findings (logs, output files) to the persistent GCS workspace before the container exits.
- If you frequently connect to the same type of container, create shell aliases in your local `.bashrc` that wrap the `bioaf compute ssh` command.
- For Kubernetes backends, you can also use `kubectl exec` directly if you have cluster credentials. However, this bypasses bioAF's audit logging and is not recommended for production use.

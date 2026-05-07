"""Guards on the work-node Packer template's disk config (to-resolve).

The build VM is transient -- Packer creates it, runs the provisioner,
destroys it. We deliberately use `pd-standard` (HDD) for the build
disk so the 50 GB does not consume `SSD_TOTAL_GB` regional quota,
which is already pressured by the GKE pool nodes' pd-balanced boot
disks (those count toward SSD_TOTAL_GB too).

The image artifact is uploaded to GCE Image Service and remains
usable for pd-ssd work-node boot disks at launch time -- only the
*build* VM uses pd-standard.
"""

import re

from app.services.environment_build_service import PACKER_VM_TEMPLATE


def test_packer_build_disk_uses_pd_standard_not_pd_ssd():
    """The Packer build VM must use pd-standard so its 50 GB does not
    eat into the regional SSD_TOTAL_GB quota (which the GKE pool nodes
    already pressure)."""
    match = re.search(r'disk_type\s*=\s*"(pd-[a-z]+)"', PACKER_VM_TEMPLATE)
    assert match, "PACKER_VM_TEMPLATE must declare disk_type"
    assert match.group(1) == "pd-standard", (
        f"Packer build disk should be pd-standard (HDD, free quota), "
        f"got {match.group(1)!r}. pd-ssd and pd-balanced both count "
        f"against SSD_TOTAL_GB, which is what we are explicitly trying "
        f"to avoid for the transient build VM."
    )


def test_packer_build_disk_size_is_50gb():
    """Sanity check the build VM disk size has not drifted; 50 GB is
    the documented value sized for Ubuntu base + miniforge + a typical
    conda environment.yml install."""
    match = re.search(r"disk_size\s*=\s*(\d+)", PACKER_VM_TEMPLATE)
    assert match, "PACKER_VM_TEMPLATE must declare disk_size"
    assert match.group(1) == "50", (
        f"Expected 50 GB build disk, got {match.group(1)!r}. If a "
        f"larger size is genuinely required, update the test and the "
        f"to-resolve.md status notes."
    )

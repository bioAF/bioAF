import pytest
import pytest_asyncio
from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["comp_bio"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def comp_bio_token(comp_bio_user) -> str:
    return AuthService.create_token(
        comp_bio_user.id,
        comp_bio_user.email,
        comp_bio_user.role_id,
        comp_bio_user.organization_id,
        role_name="comp_bio",
    )


@pytest.mark.asyncio
async def test_list_templates_initializes_builtins(client, admin_token):
    """First call initializes built-in templates."""
    response = await client.get(
        "/api/template-notebooks",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 5
    names = [n["name"] for n in data["notebooks"]]
    assert "QC & Filtering" in names
    assert "Clustering & Marker Genes" in names
    assert "Trajectory Inference" in names


@pytest.mark.asyncio
async def test_list_templates_idempotent(client, admin_token):
    """Calling list twice doesn't duplicate templates."""
    await client.get("/api/template-notebooks", headers={"Authorization": f"Bearer {admin_token}"})
    response = await client.get("/api/template-notebooks", headers={"Authorization": f"Bearer {admin_token}"})
    data = response.json()
    names = [n["name"] for n in data["notebooks"]]
    assert names.count("QC & Filtering") == 1


@pytest.mark.asyncio
async def test_get_template_detail(client, admin_token):
    """Get a specific template."""
    # Initialize
    list_resp = await client.get("/api/template-notebooks", headers={"Authorization": f"Bearer {admin_token}"})
    templates = list_resp.json()["notebooks"]
    template_id = templates[0]["id"]

    response = await client.get(
        f"/api/template-notebooks/{template_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == template_id
    assert data["is_builtin"] is True
    assert "parameters" in data


@pytest.mark.asyncio
async def test_get_template_not_found(client, admin_token):
    response = await client.get(
        "/api/template-notebooks/9999",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_viewer_can_list_templates(client, viewer_token):
    """Viewers can list templates."""
    response = await client.get(
        "/api/template-notebooks",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_viewer_cannot_clone_template(client, viewer_token):
    """Viewers cannot clone templates."""
    response = await client.post(
        "/api/template-notebooks/1/clone",
        json={"new_name": "test"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_templates_have_correct_categories(client, admin_token):
    """Templates have expected categories."""
    response = await client.get("/api/template-notebooks", headers={"Authorization": f"Bearer {admin_token}"})
    data = response.json()
    categories = {n["category"] for n in data["notebooks"]}
    assert "qc" in categories
    assert "normalization" in categories
    assert "clustering" in categories
    assert "differential_expression" in categories
    assert "trajectory" in categories


@pytest.mark.asyncio
async def test_templates_ordered_by_sort_order(client, admin_token):
    """Templates are returned in correct order."""
    response = await client.get("/api/template-notebooks", headers={"Authorization": f"Bearer {admin_token}"})
    data = response.json()
    notebooks = data["notebooks"]
    # QC comes first, then the interactive scRNA templates, then the Level-3 headless ones. Assert the
    # ordering itself rather than which template happens to have been added most recently.
    from app.services.template_notebook_service import BUILTIN_TEMPLATES

    assert notebooks[0]["category"] == "qc"
    expected = [t["name"] for t in sorted(BUILTIN_TEMPLATES, key=lambda t: t["sort_order"])]
    assert [n["name"] for n in notebooks] == expected


# ---- Level-3 headless template contracts (study 13 regressions) ----
#
# Both defects below were found on the first real ATAC-seq Level-3 attempt, AFTER a 12-sample
# pipeline had already succeeded, and neither surfaced as an error the platform could see.


def _da_source() -> str:
    import json
    from pathlib import Path

    from app.services import template_notebook_service as tns

    nb = json.loads((Path(tns.PACKAGE_TEMPLATES_DIR) / "da_peaks_deseq2.ipynb").read_text())
    return "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")


def test_da_template_resolves_sample_accessions_to_matrix_columns():
    """A declared sample is an accession; nf-core names the column after the merged BAM.

    `SRX9040493` vs `SRX9040493_REP1.mLb.clN.sorted.bam`. The notebook exact-matched, so all 12
    samples were "missing" and it aborted on `stop("samples not in matrix")`, which is why
    da_peaks_deseq2 had never once produced a result.
    """
    src = _da_source()
    assert "resolve_col" in src, "the DA template must resolve accessions to matrix columns"
    assert "startsWith(cols" in src, "resolution must be prefix-based, not exact"
    # An ambiguous match must refuse rather than silently pick a column.
    assert "cannot choose one" in src


def test_da_template_honours_a_paired_design_like_the_bulk_template():
    """`block_labels` was passed by the driver, declared nowhere, and read by nothing.

    A plan declaring matched pairs (and passing validate_paired_designs) was analysed with
    `~ condition`. The DA and bulk templates must agree on this, since both serve Level 3.
    """
    src = _da_source()
    assert "block_labels" in src
    assert "~ block + condition" in src

    from app.services.template_notebook_service import BUILTIN_TEMPLATES

    da = next(t for t in BUILTIN_TEMPLATES if t["local_file"] == "da_peaks_deseq2.ipynb")
    parameters = da["parameters"]
    assert isinstance(parameters, dict)
    assert "block_labels" in parameters, "the driver passes block_labels; it must be declared"

import pytest
import pytest_asyncio

from app.models.experiment import Experiment
from app.models.user import User
from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["comp_bio"],
        organization_id=admin_user.organization_id,
        status="active",
        name="Sarah CompBio",
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


@pytest_asyncio.fixture
async def bench_user(session, admin_user):
    password_hash = AuthService.hash_password("benchpass123")
    user = User(
        email="bench@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["bench"],
        organization_id=admin_user.organization_id,
        status="active",
        name="Jake Bench",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def bench_token(bench_user) -> str:
    return AuthService.create_token(
        bench_user.id, bench_user.email, bench_user.role_id, bench_user.organization_id, role_name="bench"
    )


@pytest_asyncio.fixture
async def experiment(session, admin_user):
    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="GBM Atlas v2",
        status="registered",
    )
    session.add(exp)
    await session.flush()
    await session.commit()
    return exp


SNAPSHOT_PAYLOAD_A = {
    "label": "leiden_0.5_no_correction",
    "object_type": "anndata",
    "cell_count": 8432,
    "gene_count": 18291,
    "parameters_json": {
        "neighbors": {"params": {"n_neighbors": 15, "method": "umap"}},
        "leiden": {"params": {"resolution": 0.5}},
    },
    "embeddings_json": {
        "X_pca": {"n_components": 50},
        "X_umap": {"n_components": 2},
    },
    "clusterings_json": {
        "leiden": {
            "n_clusters": 9,
            "distribution": {"0": 1200, "1": 980, "2": 850, "3": 720, "4": 650, "5": 580, "6": 510, "7": 480, "8": 462},
        },
    },
    "layers_json": ["counts", "log1p"],
    "metadata_columns_json": ["batch", "condition", "leiden", "total_counts"],
}

SNAPSHOT_PAYLOAD_B = {
    "label": "leiden_0.5_harmony_corrected",
    "object_type": "anndata",
    "cell_count": 8430,
    "gene_count": 18291,
    "parameters_json": {
        "neighbors": {"params": {"n_neighbors": 15, "method": "umap"}},
        "leiden": {"params": {"resolution": 0.5}},
        "harmony": {"params": {"theta": 2.0}},
    },
    "embeddings_json": {
        "X_pca": {"n_components": 50},
        "X_harmony": {"n_components": 50},
        "X_umap": {"n_components": 2},
    },
    "clusterings_json": {
        "leiden": {
            "n_clusters": 11,
            "distribution": {
                "0": 890,
                "1": 970,
                "2": 850,
                "3": 680,
                "4": 650,
                "5": 580,
                "6": 510,
                "7": 480,
                "8": 462,
                "9": 340,
                "10": 210,
            },
        },
    },
    "layers_json": ["counts", "log1p"],
    "metadata_columns_json": ["batch", "condition", "leiden", "total_counts"],
}

SEURAT_PAYLOAD = {
    "label": "sct_leiden_0.5",
    "object_type": "seurat",
    "cell_count": 8102,
    "gene_count": 18291,
    "parameters_json": {
        "FindNeighbors": {"params": {"dims": "1:30"}},
        "FindClusters": {"params": {"resolution": 0.5}},
    },
    "embeddings_json": {
        "pca": {"n_components": 50},
        "umap": {"n_components": 2},
    },
    "clusterings_json": {
        "seurat_clusters": {
            "n_clusters": 8,
            "distribution": {"0": 1400, "1": 1200, "2": 1100, "3": 900, "4": 850, "5": 800, "6": 500, "7": 352},
        },
    },
    "command_log_json": [
        {"name": "NormalizeData", "params": {"assay": "RNA"}},
        {"name": "FindVariableFeatures", "params": {"assay": "RNA", "nfeatures": 2000}},
        {"name": "ScaleData", "params": {"assay": "RNA"}},
        {"name": "RunPCA", "params": {"assay": "RNA", "npcs": 50}},
        {"name": "FindNeighbors", "params": {"reduction": "pca", "dims": "1:30"}},
        {"name": "FindClusters", "params": {"resolution": 0.5}},
        {"name": "RunUMAP", "params": {"reduction": "pca", "dims": "1:30"}},
    ],
}


# --- CRUD Tests ---


@pytest.mark.asyncio
async def test_create_snapshot(client, admin_token, experiment):
    payload = {**SNAPSHOT_PAYLOAD_A, "experiment_id": experiment.id}
    resp = await client.post(
        "/api/snapshots",
        json=payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["label"] == "leiden_0.5_no_correction"
    assert data["cell_count"] == 8432
    assert data["object_type"] == "anndata"
    assert data["cluster_count"] == 9


@pytest.mark.asyncio
async def test_create_snapshot_requires_scope(client, admin_token):
    payload = {**SNAPSHOT_PAYLOAD_A}
    resp = await client.post(
        "/api/snapshots",
        json=payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_snapshot_nonexistent_experiment(client, admin_token):
    payload = {**SNAPSHOT_PAYLOAD_A, "experiment_id": 99999}
    resp = await client.post(
        "/api/snapshots",
        json=payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_snapshots(client, admin_token, experiment):
    # Create two snapshots
    for payload_base in [SNAPSHOT_PAYLOAD_A, SNAPSHOT_PAYLOAD_B]:
        payload = {**payload_base, "experiment_id": experiment.id}
        await client.post(
            "/api/snapshots",
            json=payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    resp = await client.get(
        f"/api/snapshots?experiment_id={experiment.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2


@pytest.mark.asyncio
async def test_get_snapshot_detail(client, admin_token, experiment):
    payload = {**SNAPSHOT_PAYLOAD_A, "experiment_id": experiment.id}
    create_resp = await client.post(
        "/api/snapshots",
        json=payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    snap_id = create_resp.json()["id"]

    resp = await client.get(
        f"/api/snapshots/{snap_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["parameters_json"]["neighbors"]["params"]["n_neighbors"] == 15
    assert data["embeddings_json"]["X_pca"]["n_components"] == 50
    assert "leiden" in data["clusterings_json"]


@pytest.mark.asyncio
async def test_get_snapshot_not_found(client, admin_token):
    resp = await client.get(
        "/api/snapshots/99999",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_star_snapshot(client, admin_token, experiment):
    payload = {**SNAPSHOT_PAYLOAD_A, "experiment_id": experiment.id}
    create_resp = await client.post(
        "/api/snapshots",
        json=payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    snap_id = create_resp.json()["id"]

    # Star it
    resp = await client.post(
        f"/api/snapshots/{snap_id}/star",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["starred"] is True

    # Unstar it
    resp = await client.post(
        f"/api/snapshots/{snap_id}/star",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["starred"] is False


@pytest.mark.asyncio
async def test_list_starred_only(client, admin_token, experiment):
    # Create and star first snapshot
    payload_a = {**SNAPSHOT_PAYLOAD_A, "experiment_id": experiment.id}
    resp_a = await client.post("/api/snapshots", json=payload_a, headers={"Authorization": f"Bearer {admin_token}"})
    snap_a_id = resp_a.json()["id"]
    await client.post(f"/api/snapshots/{snap_a_id}/star", headers={"Authorization": f"Bearer {admin_token}"})

    # Create second snapshot (not starred)
    payload_b = {**SNAPSHOT_PAYLOAD_B, "experiment_id": experiment.id}
    await client.post("/api/snapshots", json=payload_b, headers={"Authorization": f"Bearer {admin_token}"})

    # List starred only
    resp = await client.get(
        f"/api/snapshots?experiment_id={experiment.id}&starred=true",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["snapshots"][0]["starred"] is True


# --- Auth Tests ---


@pytest.mark.asyncio
async def test_comp_bio_can_create(client, comp_bio_token, experiment):
    payload = {**SNAPSHOT_PAYLOAD_A, "experiment_id": experiment.id}
    resp = await client.post(
        "/api/snapshots",
        json=payload,
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_bench_cannot_create(client, bench_token, experiment):
    payload = {**SNAPSHOT_PAYLOAD_A, "experiment_id": experiment.id}
    resp = await client.post(
        "/api/snapshots",
        json=payload,
        headers={"Authorization": f"Bearer {bench_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_can_read(client, viewer_token, admin_token, experiment):
    # Create snapshot as admin
    payload = {**SNAPSHOT_PAYLOAD_A, "experiment_id": experiment.id}
    await client.post("/api/snapshots", json=payload, headers={"Authorization": f"Bearer {admin_token}"})

    # Viewer can list
    resp = await client.get(
        f"/api/snapshots?experiment_id={experiment.id}",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 200


# --- Comparison Tests ---


@pytest.mark.asyncio
async def test_compare_two_identical_snapshots(client, admin_token, experiment):
    ids = []
    for _ in range(2):
        payload = {**SNAPSHOT_PAYLOAD_A, "experiment_id": experiment.id}
        resp = await client.post("/api/snapshots", json=payload, headers={"Authorization": f"Bearer {admin_token}"})
        ids.append(resp.json()["id"])

    resp = await client.get(
        f"/api/snapshots/compare?ids={ids[0]},{ids[1]}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    # All parameters should be unchanged
    for param in data["parameter_diff"]:
        assert param["changed"] is False


@pytest.mark.asyncio
async def test_compare_two_different_snapshots(client, admin_token, experiment):
    ids = []
    for payload_base in [SNAPSHOT_PAYLOAD_A, SNAPSHOT_PAYLOAD_B]:
        payload = {**payload_base, "experiment_id": experiment.id}
        resp = await client.post("/api/snapshots", json=payload, headers={"Authorization": f"Bearer {admin_token}"})
        ids.append(resp.json()["id"])

    resp = await client.get(
        f"/api/snapshots/compare?ids={ids[0]},{ids[1]}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()

    # harmony.params.theta should appear as changed (present in B, absent in A)
    harmony_diff = [p for p in data["parameter_diff"] if "harmony" in p["parameter_path"]]
    assert len(harmony_diff) > 0
    assert harmony_diff[0]["changed"] is True

    # Embedding diff should show X_harmony only in B
    harmony_emb = [e for e in data["embedding_diff"] if e["embedding_name"] == "X_harmony"]
    assert len(harmony_emb) == 1
    assert len(harmony_emb[0]["present_in"]) == 1

    # Clustering diff should have union of cluster labels (0-10)
    leiden_diff = [c for c in data["clustering_diff"] if c["clustering_name"] == "leiden"]
    assert len(leiden_diff) == 1
    # Snapshot B has clusters 0-10, A has 0-8
    dist_a = leiden_diff[0]["distributions"][str(ids[0])]
    assert "9" in dist_a  # zero-filled for missing clusters
    assert dist_a["9"] == 0

    # command_log_diff should be null (both anndata)
    assert data["command_log_diff"] is None

    # Cell count series
    assert len(data["cell_count_series"]) == 2


@pytest.mark.asyncio
async def test_compare_mixed_anndata_seurat(client, admin_token, experiment):
    ids = []
    for payload_base in [SNAPSHOT_PAYLOAD_A, SEURAT_PAYLOAD]:
        payload = {**payload_base, "experiment_id": experiment.id}
        resp = await client.post("/api/snapshots", json=payload, headers={"Authorization": f"Bearer {admin_token}"})
        ids.append(resp.json()["id"])

    resp = await client.get(
        f"/api/snapshots/compare?ids={ids[0]},{ids[1]}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    # command_log_diff should be present (one seurat)
    assert data["command_log_diff"] is not None
    assert len(data["command_log_diff"]) > 0


@pytest.mark.asyncio
async def test_compare_different_clustering_resolutions(client, admin_token, experiment):
    payload_low = {
        **SNAPSHOT_PAYLOAD_A,
        "experiment_id": experiment.id,
        "label": "low_res",
        "clusterings_json": {
            "leiden": {"n_clusters": 3, "distribution": {"0": 3000, "1": 2800, "2": 2632}},
        },
    }
    payload_high = {
        **SNAPSHOT_PAYLOAD_A,
        "experiment_id": experiment.id,
        "label": "high_res",
        "clusterings_json": {
            "leiden": {
                "n_clusters": 6,
                "distribution": {"0": 1500, "1": 1400, "2": 1300, "3": 1200, "4": 1100, "5": 932},
            },
        },
    }
    ids = []
    for p in [payload_low, payload_high]:
        resp = await client.post("/api/snapshots", json=p, headers={"Authorization": f"Bearer {admin_token}"})
        ids.append(resp.json()["id"])

    resp = await client.get(
        f"/api/snapshots/compare?ids={ids[0]},{ids[1]}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()

    leiden_diff = [c for c in data["clustering_diff"] if c["clustering_name"] == "leiden"]
    assert len(leiden_diff) == 1
    # Low-res snapshot should have zeros for clusters 3-5
    dist_low = leiden_diff[0]["distributions"][str(ids[0])]
    assert dist_low["3"] == 0
    assert dist_low["4"] == 0
    assert dist_low["5"] == 0
    # High-res snapshot should have all clusters
    dist_high = leiden_diff[0]["distributions"][str(ids[1])]
    assert dist_high["0"] == 1500


@pytest.mark.asyncio
async def test_compare_too_few_ids(client, admin_token, experiment):
    payload = {**SNAPSHOT_PAYLOAD_A, "experiment_id": experiment.id}
    resp = await client.post("/api/snapshots", json=payload, headers={"Authorization": f"Bearer {admin_token}"})
    snap_id = resp.json()["id"]

    resp = await client.get(
        f"/api/snapshots/compare?ids={snap_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_compare_too_many_ids(client, admin_token, experiment):
    ids = []
    for i in range(6):
        payload = {**SNAPSHOT_PAYLOAD_A, "experiment_id": experiment.id, "label": f"snap_{i}"}
        resp = await client.post("/api/snapshots", json=payload, headers={"Authorization": f"Bearer {admin_token}"})
        ids.append(str(resp.json()["id"]))

    resp = await client.get(
        f"/api/snapshots/compare?ids={','.join(ids)}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_compare_nonexistent_snapshot(client, admin_token, experiment):
    payload = {**SNAPSHOT_PAYLOAD_A, "experiment_id": experiment.id}
    resp = await client.post("/api/snapshots", json=payload, headers={"Authorization": f"Bearer {admin_token}"})
    snap_id = resp.json()["id"]

    resp = await client.get(
        f"/api/snapshots/compare?ids={snap_id},99999",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404

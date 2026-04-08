from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.analysis_snapshot import AnalysisSnapshot
from app.models.experiment import Experiment
from app.models.pipeline_run import PipelineRun
from app.models.project import Project
from app.models.project_sample import ProjectSample
from app.models.sample import Sample
from app.schemas.provenance import ProvenanceDAG, ProvenanceEdge, ProvenanceNode


class ProvenanceService:
    @staticmethod
    async def build_project_provenance(session: AsyncSession, project_id: int) -> ProvenanceDAG:
        nodes: list[ProvenanceNode] = []
        edges: list[ProvenanceEdge] = []
        seen_nodes: set[str] = set()

        # Load project
        project_result = await session.execute(select(Project).where(Project.id == project_id))
        project = project_result.scalar_one_or_none()
        if not project:
            return ProvenanceDAG(nodes=[], edges=[])

        project_node_id = f"project:{project.id}"
        nodes.append(
            ProvenanceNode(
                id=project_node_id,
                type="project",
                label=project.name,
                metadata={"status": project.status},
            )
        )
        seen_nodes.add(project_node_id)

        # Load experiments directly linked via experiment.project_id
        direct_exp_result = await session.execute(select(Experiment).where(Experiment.project_id == project_id))
        direct_experiments = direct_exp_result.scalars().all()

        for experiment in direct_experiments:
            exp_node_id = f"experiment:{experiment.id}"
            if exp_node_id not in seen_nodes:
                nodes.append(
                    ProvenanceNode(
                        id=exp_node_id,
                        type="experiment",
                        label=experiment.name,
                        metadata={"status": experiment.status},
                    )
                )
                seen_nodes.add(exp_node_id)
                edges.append(
                    ProvenanceEdge(
                        source=exp_node_id,
                        target=project_node_id,
                        relationship="contains",
                    )
                )

            # Load samples for this experiment
            sample_result = await session.execute(select(Sample).where(Sample.experiment_id == experiment.id))
            for sample in sample_result.scalars().all():
                sample_node_id = f"sample:{sample.id}"
                if sample_node_id not in seen_nodes:
                    nodes.append(
                        ProvenanceNode(
                            id=sample_node_id,
                            type="sample",
                            label=sample.sample_id_unique or f"Sample {sample.id}",
                            metadata={
                                "organism": sample.organism,
                                "tissue_type": sample.tissue_type,
                                "qc_status": sample.qc_status,
                            },
                        )
                    )
                    seen_nodes.add(sample_node_id)
                    edges.append(
                        ProvenanceEdge(
                            source=exp_node_id,
                            target=sample_node_id,
                            relationship="contains",
                        )
                    )

        # Load project samples with experiments (via junction table)
        ps_result = await session.execute(
            select(ProjectSample, Sample, Experiment)
            .join(Sample, Sample.id == ProjectSample.sample_id)
            .join(Experiment, Experiment.id == Sample.experiment_id)
            .where(ProjectSample.project_id == project_id)
        )
        ps_rows = ps_result.all()

        # Add experiment and sample nodes (deduped via seen_nodes)
        for _, sample, experiment in ps_rows:
            exp_node_id = f"experiment:{experiment.id}"
            if exp_node_id not in seen_nodes:
                nodes.append(
                    ProvenanceNode(
                        id=exp_node_id,
                        type="experiment",
                        label=experiment.name,
                        metadata={"status": experiment.status},
                    )
                )
                seen_nodes.add(exp_node_id)
                edges.append(
                    ProvenanceEdge(
                        source=exp_node_id,
                        target=project_node_id,
                        relationship="contains",
                    )
                )

            sample_node_id = f"sample:{sample.id}"
            if sample_node_id not in seen_nodes:
                nodes.append(
                    ProvenanceNode(
                        id=sample_node_id,
                        type="sample",
                        label=sample.sample_id_unique or f"Sample {sample.id}",
                        metadata={
                            "organism": sample.organism,
                            "tissue_type": sample.tissue_type,
                            "qc_status": sample.qc_status,
                        },
                    )
                )
                seen_nodes.add(sample_node_id)
                edges.append(
                    ProvenanceEdge(
                        source=exp_node_id,
                        target=sample_node_id,
                        relationship="contains",
                    )
                )

        # Load pipeline runs (project-scoped)
        run_result = await session.execute(
            select(PipelineRun)
            .options(selectinload(PipelineRun.references))
            .where(PipelineRun.project_id == project_id)
        )
        project_runs = run_result.scalars().all()

        # Also load experiment-scoped runs from source experiments
        experiment_ids = list({exp.id for _, _, exp in ps_rows} | {exp.id for exp in direct_experiments})
        exp_run_result = (
            await session.execute(
                select(PipelineRun)
                .options(selectinload(PipelineRun.references))
                .where(
                    PipelineRun.experiment_id.in_(experiment_ids),
                    PipelineRun.project_id.is_(None),
                )
            )
            if experiment_ids
            else None
        )
        exp_runs = list(exp_run_result.scalars().all()) if exp_run_result else []

        all_runs = list(project_runs) + exp_runs
        for run in all_runs:
            run_node_id = f"pipeline_run:{run.id}"
            if run_node_id not in seen_nodes:
                nodes.append(
                    ProvenanceNode(
                        id=run_node_id,
                        type="pipeline_run",
                        label=f"{run.pipeline_name} v{run.pipeline_version or '?'}",
                        metadata={
                            "status": run.status,
                            "pipeline_name": run.pipeline_name,
                            "created_at": run.created_at.isoformat() if run.created_at else None,
                        },
                    )
                )
                seen_nodes.add(run_node_id)

                # Edge: project or experiment -> run
                if run.project_id:
                    edges.append(
                        ProvenanceEdge(
                            source=project_node_id,
                            target=run_node_id,
                            relationship="input_to",
                        )
                    )
                elif run.experiment_id:
                    exp_id = f"experiment:{run.experiment_id}"
                    if exp_id in seen_nodes:
                        edges.append(
                            ProvenanceEdge(
                                source=exp_id,
                                target=run_node_id,
                                relationship="input_to",
                            )
                        )

                # Reference edges
                for ref in run.references or []:
                    ref_node_id = f"reference:{ref.id}"
                    if ref_node_id not in seen_nodes:
                        nodes.append(
                            ProvenanceNode(
                                id=ref_node_id,
                                type="reference",
                                label=f"{ref.name} {ref.version}",
                                metadata={
                                    "status": ref.status,
                                    "source": ref.source,
                                },
                            )
                        )
                        seen_nodes.add(ref_node_id)
                    edges.append(
                        ProvenanceEdge(
                            source=ref_node_id,
                            target=run_node_id,
                            relationship="used_reference",
                        )
                    )

        # Load snapshots (will be empty until Phase 10b)
        snap_result = await session.execute(select(AnalysisSnapshot).where(AnalysisSnapshot.project_id == project_id))
        snapshots = snap_result.scalars().all()
        for snap in snapshots:
            snap_node_id = f"snapshot:{snap.id}"
            nodes.append(
                ProvenanceNode(
                    id=snap_node_id,
                    type="snapshot",
                    label=snap.label,
                    metadata={
                        "object_type": snap.object_type,
                        "cell_count": snap.cell_count,
                        "starred": snap.starred,
                    },
                )
            )
            edges.append(
                ProvenanceEdge(
                    source=project_node_id,
                    target=snap_node_id,
                    relationship="captured_at",
                )
            )

        return ProvenanceDAG(nodes=nodes, edges=edges)

    @staticmethod
    async def build_experiment_provenance(session: AsyncSession, experiment_id: int) -> ProvenanceDAG:
        nodes: list[ProvenanceNode] = []
        edges: list[ProvenanceEdge] = []
        seen_nodes: set[str] = set()

        # Load experiment
        exp_result = await session.execute(select(Experiment).where(Experiment.id == experiment_id))
        experiment = exp_result.scalar_one_or_none()
        if not experiment:
            return ProvenanceDAG(nodes=[], edges=[])

        exp_node_id = f"experiment:{experiment.id}"
        nodes.append(
            ProvenanceNode(
                id=exp_node_id,
                type="experiment",
                label=experiment.name,
                metadata={"status": experiment.status},
            )
        )
        seen_nodes.add(exp_node_id)

        # Load samples
        sample_result = await session.execute(select(Sample).where(Sample.experiment_id == experiment_id))
        samples = sample_result.scalars().all()
        for sample in samples:
            sample_node_id = f"sample:{sample.id}"
            nodes.append(
                ProvenanceNode(
                    id=sample_node_id,
                    type="sample",
                    label=sample.sample_id_unique or f"Sample {sample.id}",
                    metadata={
                        "organism": sample.organism,
                        "tissue_type": sample.tissue_type,
                        "qc_status": sample.qc_status,
                    },
                )
            )
            seen_nodes.add(sample_node_id)
            edges.append(
                ProvenanceEdge(
                    source=exp_node_id,
                    target=sample_node_id,
                    relationship="contains",
                )
            )

        # Load pipeline runs
        run_result = await session.execute(
            select(PipelineRun)
            .options(selectinload(PipelineRun.references))
            .where(PipelineRun.experiment_id == experiment_id)
        )
        runs = run_result.scalars().all()
        for run in runs:
            run_node_id = f"pipeline_run:{run.id}"
            nodes.append(
                ProvenanceNode(
                    id=run_node_id,
                    type="pipeline_run",
                    label=f"{run.pipeline_name} v{run.pipeline_version or '?'}",
                    metadata={
                        "status": run.status,
                        "pipeline_name": run.pipeline_name,
                        "created_at": run.created_at.isoformat() if run.created_at else None,
                    },
                )
            )
            seen_nodes.add(run_node_id)
            edges.append(
                ProvenanceEdge(
                    source=exp_node_id,
                    target=run_node_id,
                    relationship="input_to",
                )
            )

            for ref in run.references or []:
                ref_node_id = f"reference:{ref.id}"
                if ref_node_id not in seen_nodes:
                    nodes.append(
                        ProvenanceNode(
                            id=ref_node_id,
                            type="reference",
                            label=f"{ref.name} {ref.version}",
                            metadata={
                                "status": ref.status,
                                "source": ref.source,
                            },
                        )
                    )
                    seen_nodes.add(ref_node_id)
                edges.append(
                    ProvenanceEdge(
                        source=ref_node_id,
                        target=run_node_id,
                        relationship="used_reference",
                    )
                )

        # Load snapshots
        snap_result = await session.execute(
            select(AnalysisSnapshot).where(AnalysisSnapshot.experiment_id == experiment_id)
        )
        snapshots = snap_result.scalars().all()
        for snap in snapshots:
            snap_node_id = f"snapshot:{snap.id}"
            nodes.append(
                ProvenanceNode(
                    id=snap_node_id,
                    type="snapshot",
                    label=snap.label,
                    metadata={
                        "object_type": snap.object_type,
                        "cell_count": snap.cell_count,
                        "starred": snap.starred,
                    },
                )
            )
            edges.append(
                ProvenanceEdge(
                    source=exp_node_id,
                    target=snap_node_id,
                    relationship="captured_at",
                )
            )

        return ProvenanceDAG(nodes=nodes, edges=edges)

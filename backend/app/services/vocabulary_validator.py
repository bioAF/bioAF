"""Reusable vocabulary validation service for controlled vocabulary fields."""

import logging
import time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.controlled_vocabulary import ControlledVocabulary

logger = logging.getLogger("bioaf.vocabulary_validator")

# In-memory cache: {field_name: (values_set, timestamp)}
_cache: dict[str, tuple[set[str], float]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes


def _derive_instrument_platform(instrument_model: str) -> str:
    """Derive instrument_platform from instrument_model prefix."""
    if instrument_model.startswith("Illumina"):
        return "ILLUMINA"
    if instrument_model.startswith("PacBio"):
        return "PACBIO_SMRT"
    if instrument_model.startswith("Oxford Nanopore"):
        return "OXFORD_NANOPORE"
    return "OTHER"


def invalidate_cache(field_name: str | None = None) -> None:
    """Invalidate the vocabulary cache. If field_name is None, clear all."""
    if field_name:
        _cache.pop(field_name, None)
    else:
        _cache.clear()


class VocabularyValidator:
    """Validates field values against the controlled_vocabularies table."""

    @staticmethod
    async def _get_allowed_values(session: AsyncSession, field_name: str) -> set[str]:
        """Get allowed values for a field, with in-memory caching."""
        now = time.time()
        cached = _cache.get(field_name)
        if cached and (now - cached[1]) < _CACHE_TTL_SECONDS:
            return cached[0]

        result = await session.execute(
            select(ControlledVocabulary.allowed_value).where(
                ControlledVocabulary.field_name == field_name,
                ControlledVocabulary.is_active == True,  # noqa: E712
            )
        )
        values = {row[0] for row in result.all()}
        _cache[field_name] = (values, now)
        return values

    @staticmethod
    async def validate_field(session: AsyncSession, field_name: str, value: str | None) -> None:
        """Raise 422 HTTPException if value is not in the controlled vocabulary.

        None values are always accepted (fields are optional).
        """
        if value is None:
            return

        allowed = await VocabularyValidator._get_allowed_values(session, field_name)
        if not allowed:
            # No vocabulary defined for this field — accept any value
            return

        if value not in allowed:
            sorted_values = sorted(allowed)
            raise HTTPException(
                422,
                f"Invalid value '{value}' for field '{field_name}'. Allowed values: {', '.join(sorted_values)}",
            )

    @staticmethod
    async def validate_experiment_fields(session: AsyncSession, data: dict) -> None:
        """Validate all controlled vocabulary fields on an experiment."""
        for field_name in ("design_type",):
            value = data.get(field_name)
            if value is not None:
                await VocabularyValidator.validate_field(session, field_name, value)

    @staticmethod
    async def validate_sample_fields(session: AsyncSession, data: dict) -> None:
        """Validate all controlled vocabulary fields on a sample."""
        for field_name in ("molecule_type", "library_prep_method", "library_layout"):
            value = data.get(field_name)
            if value is not None:
                await VocabularyValidator.validate_field(session, field_name, value)

    @staticmethod
    async def validate_batch_fields(session: AsyncSession, data: dict) -> None:
        """Validate all controlled vocabulary fields on a batch."""
        for field_name in ("instrument_model", "instrument_platform", "quality_score_encoding"):
            value = data.get(field_name)
            if value is not None:
                await VocabularyValidator.validate_field(session, field_name, value)

    @staticmethod
    async def validate_pipeline_run_fields(session: AsyncSession, data: dict) -> None:
        """Validate all controlled vocabulary fields on a pipeline run."""
        for field_name in ("reference_genome", "alignment_algorithm"):
            value = data.get(field_name)
            if value is not None:
                await VocabularyValidator.validate_field(session, field_name, value)

    @staticmethod
    async def get_allowed_values(session: AsyncSession, field_name: str, active_only: bool = True) -> list[str]:
        """Return all allowed values for a field."""
        query = select(ControlledVocabulary.allowed_value).where(ControlledVocabulary.field_name == field_name)
        if active_only:
            query = query.where(ControlledVocabulary.is_active == True)  # noqa: E712
        query = query.order_by(ControlledVocabulary.display_order)

        result = await session.execute(query)
        return [row[0] for row in result.all()]

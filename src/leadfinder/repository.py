from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from leadfinder.languages import normalize_language_filters
from leadfinder.models import DiscoveryQuery, SearchProfileRecord
from leadfinder.profiles import SearchProfileSpec, generate_query_specs


def upsert_profile(
    session: Session,
    spec: SearchProfileSpec,
    query_limit: int,
) -> SearchProfileRecord:
    profile = session.scalar(
        select(SearchProfileRecord).where(SearchProfileRecord.slug == spec.slug)
    )
    values = {
        "name": spec.name,
        "description": spec.description,
        "services": list(spec.services),
        "locations": list(spec.locations),
        "intents": list(spec.intents),
        "languages": list(normalize_language_filters(spec.languages)),
        "negative_terms": list(spec.negative_terms),
        "positive_examples": list(spec.positive_examples),
        "classifier_prompt": spec.classifier_prompt,
        "enabled": True,
    }
    if profile is None:
        profile = SearchProfileRecord(slug=spec.slug, **values)
        session.add(profile)
        session.flush()
    else:
        for field, value in values.items():
            setattr(profile, field, value)

    existing = {
        item.query: item
        for item in session.scalars(
            select(DiscoveryQuery).where(DiscoveryQuery.profile_id == profile.id)
        )
    }
    desired = generate_query_specs(spec, limit=query_limit)
    desired_queries = {query for query, _query_type in desired}
    for item in existing.values():
        item.active = item.query in desired_queries
    for query, query_type in desired:
        if query in existing:
            existing[query].query_type = query_type
            existing[query].active = True
            continue
        session.add(
            DiscoveryQuery(
                profile_id=profile.id,
                query=query,
                query_type=query_type,
                active=True,
            )
        )
    return profile


def spec_from_record(record: SearchProfileRecord) -> SearchProfileSpec:
    return SearchProfileSpec(
        slug=record.slug,
        name=record.name,
        description=record.description,
        services=tuple(record.services),
        locations=tuple(record.locations),
        intents=tuple(record.intents),
        languages=tuple(record.languages),
        negative_terms=tuple(record.negative_terms),
        positive_examples=tuple(record.positive_examples),
        classifier_prompt=record.classifier_prompt,
    )

"""plan_8_3 section 0.4: every unresolved outcome bioAF can emit names the control that resolves it.

Of the unresolved outcomes plan_8_3's first implementation made reachable, one had a control (a
recorded table confirmation) and two had none: an unresolved filter magnitude and an unresolved
biological unit identity were dead ends, and both were recorded as "not built, on purpose". A gate
whose only outcome is unresolved, with nothing that can resolve it, is not a completed path.

So the registry is walked against the code's OWN vocabularies. Every typed cause a hold carries, every
terminal reason a check stops with, every support state, every author-table state and every failure a
decision can report has an entry; each entry says whether it is an unresolved outcome or an established
fact, and an unresolved one names a control that exists in the build. An added refusal cannot silently
become a dead end: it fails here first.
"""

import importlib

import pytest

from app.services import llm_decision
from app.services import validation_acquisition_outcome as acquisition
from app.services import validation_applicability as applicability
from app.services import validation_author_table_state as table_state
from app.services import validation_check_queue as queue
from app.services.validation_unresolved_outcomes import (
    ESTABLISHED_FACT,
    UNRESOLVED_OUTCOMES,
    controls_for,
    vocabularies,
)


def _declared() -> set[str]:
    return {kind for members in vocabularies().values() for kind in members}


class TestTheRegistryCoversTheCodesOwnVocabularies:
    def test_it_walks_the_sets_the_implementation_declares(self):
        """The walk is over the code, not a hand-kept list: each vocabulary is imported from where it
        is defined, so adding a member there is what fails this."""
        declared = vocabularies()
        assert set(declared["acquisition_cause"]) == set(acquisition._LIMITATION_FOR_CAUSE)
        # `access_refused` is both a hold's cause and a check's terminal reason; the check's entry is
        # keyed with a `_check` suffix so each keeps its own words and its own control.
        assert set(declared["check_terminal_reason"]) == {
            f"{r}_check" if r in acquisition._LIMITATION_FOR_CAUSE else r for r in queue.TERMINAL_REASONS
        }
        assert set(declared["support_state"]) == set(applicability.SUPPORT_STATES)
        assert set(declared["author_table_state"]) == set(table_state.STATES)
        assert set(declared["decision_outcome"]) == {o for o in llm_decision.OUTCOMES if o != llm_decision.OUTCOME_OK}

    def test_every_declared_kind_has_an_entry(self):
        missing = sorted(_declared() - set(UNRESOLVED_OUTCOMES))
        assert missing == [], f"no entry for {missing}"

    def test_no_entry_names_a_kind_the_code_does_not_declare(self):
        stale = sorted(set(UNRESOLVED_OUTCOMES) - _declared())
        assert stale == [], f"stale entries for {stale}"


class TestEveryEntrySaysWhatItIsAndWhatResolvesIt:
    @pytest.mark.parametrize("kind", sorted(UNRESOLVED_OUTCOMES))
    def test_it_states_what_the_outcome_says_and_where_it_comes_from(self, kind):
        entry = UNRESOLVED_OUTCOMES[kind]
        assert entry.what.strip()
        assert entry.emitted_by.strip()
        assert entry.vocabulary in vocabularies()

    @pytest.mark.parametrize("kind", sorted(UNRESOLVED_OUTCOMES))
    def test_an_unresolved_outcome_names_a_control_and_an_established_fact_does_not(self, kind):
        entry = UNRESOLVED_OUTCOMES[kind]
        if entry.control == ESTABLISHED_FACT:
            assert not entry.control_symbol
            assert entry.why_no_control.strip()
        else:
            assert entry.control.strip()
            assert entry.control_module.strip()
            assert entry.control_symbol.strip()


class TestEveryControlExistsInThisBuild:
    @pytest.mark.parametrize("kind", sorted(k for k, e in UNRESOLVED_OUTCOMES.items() if e.control_symbol))
    def test_the_module_imports_and_holds_the_symbol_the_entry_names(self, kind):
        entry = UNRESOLVED_OUTCOMES[kind]
        module = importlib.import_module(entry.control_module)
        assert hasattr(module, entry.control_symbol), f"{entry.control_module} has no {entry.control_symbol}"

    def test_the_three_the_plan_names_are_all_built_and_deterministic(self):
        """The plan's own list: an unestablished table binding had a control, and the unresolved filter
        magnitude and the unresolved biological unit identity did not. A recorded confirmation is a
        person stating a fact, so none of the three needs a model call."""
        for kind in ("binding", "biological_identity_unresolved", "filter_magnitude_unresolved"):
            entry = UNRESOLVED_OUTCOMES[kind]
            assert entry.control_symbol
            assert entry.deterministic is True

    def test_controls_for_lists_what_would_resolve_a_studys_outcomes(self):
        found = controls_for(["biological_identity_unresolved", "filter_magnitude_unresolved", "unsupported"])
        assert [f["kind"] for f in found] == ["biological_identity_unresolved", "filter_magnitude_unresolved"]
        assert all(f["deterministic"] for f in found)

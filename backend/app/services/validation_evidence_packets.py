"""plan_8_6 section 3: one bounded evidence packet per obligation, selected for that obligation.

The passages an obligation is judged on must be relevant TO THAT OBLIGATION. Study 65's M1.B, a
COMPUTATIONAL preprocessing row, was verified from Matrigel, mTeSR Plus and EDTA passaging: the
assessor was handed the first forty keyword-matched sentences of the paper, and those were what they
were. Its M1.A, asked about the same paper, correctly said the evidence was wet-lab steps.

**Keywords rank candidates; a token's presence is not proof of relevance.** What decides eligibility
is the SECTION a passage sits in and the kind of source it is. Ranking then puts the passages that
name this obligation's subject first, and a passage that names only what the obligation explicitly
excludes is dropped as irrelevant and counted as dropped.

**Coverage travels with the packet.** Which sections were available, which were supplied, how many
were excluded, what was unavailable and what was deferred by budget. Indexing a section is not
evidence that the assessor inspected it, and an absence finding rests on this record
(`validation_judgment.coverage_supports_absence`).

Pure: it is given an index and returns a packet. No network, no model, no database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.validation_evidence_index import (
    ABSTRACT,
    AVAILABILITY,
    DISCUSSION,
    INTRODUCTION,
    LEGEND,
    METHODS,
    OTHER,
    RESULTS,
)

PACKET_VERSION = 1

# What one request may carry. The judgment contract's budget is 8,192 input tokens including the
# instructions and the question, and the rest is evidence; at roughly four characters to a token
# this leaves the packet well inside it. What does not fit is offered once as an expansion rather
# than silently dropped.
MAX_PACKET_CHARS = 24000
MAX_PACKET_PASSAGES = 30
MAX_EXPANSION_PASSAGES = 20

# The kinds of evidence that are not the article's running text.
DEPOSIT = "deposit"
CODE = "code"
TOOLS = "tools"
SUPPLEMENT = "supplement"
# plan_8_6 section 7: the design facts a comparison rests on, read from the paper's own words by
# `validation_design_summary`. A comparator is not a design, and E2.B is answered from these.
DESIGN = "design"


@dataclass(frozen=True)
class Selector:
    """Which evidence can answer an obligation, and which words rank it first.

    ``sections`` and ``extras`` decide ELIGIBILITY: a passage from a section this obligation is not
    about is not a candidate however many of its words match. ``include`` ranks; ``exclude`` names
    the evidence that looks relevant and is not, and a passage that matches only that is dropped.
    ``needs`` names the source kinds whose absence leaves the packet unable to support an absence
    finding.
    """

    sections: tuple[str, ...]
    extras: tuple[str, ...]
    needs: tuple[str, ...]
    include: re.Pattern
    exclude: re.Pattern | None = None


def _re(*parts: str) -> re.Pattern:
    return re.compile("|".join(parts), re.I)


# Written in PRIORITY order: a packet fills from the first kind before it spends budget on the last.
_PAPER = (METHODS, LEGEND, AVAILABILITY, ABSTRACT, DISCUSSION, RESULTS, OTHER, INTRODUCTION)
_PROCEDURE = (METHODS, LEGEND, OTHER)
_DESIGN = (METHODS, LEGEND, DISCUSSION, RESULTS, OTHER)
_TRACE = (AVAILABILITY, METHODS, LEGEND, DISCUSSION, RESULTS, OTHER)

# Every documentary obligation rests on the paper's text and on the attachments that carry the rest
# of it. A supplementary methods document bioAF never retrieved is a source an absence finding would
# be about, and section 8 will not let one be reported over it.
_ALWAYS_NEEDS = ("text", "supplements")

# The computational parameters an obligation about preprocessing is answered from.
_COMPUTATIONAL = _re(
    r"\btrim\w*|\badapter|\bcutadapt|\btrim[\s-]?galore|\bfastp\b|\bfastqc\b",
    r"\balign\w*|\bmapp?(?:ed|ing)\b|\bSTAR\b|\bbowtie|\bbwa\b|\bhisat|\bsalmon\b|\bkallisto\b|\bminimap",
    r"\bquantif\w*|\bRSEM\b|\bfeatureCounts\b|\bHTSeq\b|\bcount matrix|\bcounts? table",
    r"\bnormali[sz]\w*|\bCPM\b|\bTPM\b|\bFPKM\b|\bRPKM\b|\bSCTransform\b|\bregress(?:ed|ing)? out",
    r"\bdemultiplex\w*|\bbarcode|\bUMI\b|\bdeduplicat\w*|\bduplicates? were",
    r"\bCell\s?Ranger\b|\bSTARsolo\b|\bkb[\s-]?python\b|\bsoupx\b|\bscrublet\b|\bdoublet",
    r"\bmitochondrial\b|\bpercent\.?mt\b|\bmin\.?cells\b|\bmin\.?(?:genes|features)\b",
    r"\bfilter(?:ed|ing|s)?\b|\bthreshold\w*|\bcut-?off",
    # "removed" on its own is a wet-lab word too. What makes it preprocessing is WHAT was removed.
    r"\b(?:cells?|reads?|genes?|barcodes?|doublets?|samples?|libraries|features?|transcripts?)"
    r"\s+(?:\w+\s+){0,3}?(?:were|was)\s+(?:removed|discarded|excluded|retained|kept|filtered)",
    r"\bquality (?:score|control|filter)|\bQ30\b|\bMAPQ\b|\bPhred\b|\bbatch (?:effect|correction)",
    # "integrated" is what a transgene does as often as what two datasets do, so it is qualified.
    r"\bSeurat\b|\bscanpy\b|\bSCANPY\b|\bharmony\b|\bcluster\w*|\bUMAP\b|\btSNE\b|\bPCA\b"
    r"|\bintegrat(?:e|ed|ion|ing)\s+(?:the\s+)?(?:datasets?|samples?|objects?|batches|libraries)",
    r"\bpreprocess\w*|\bprocess(?:ed|ing) (?:with|using|in)\b|\bpipeline\b|\bnf-core\b|\bsnakemake\b",
)
# Wet-lab materials: what M1.B's "material settings" is NOT about.
_BENCH_MATERIALS = _re(
    r"\bMatrigel\b|\bmTeSR\b|\bmedium\b|\bmedia\b|\bserum\b|\bFBS\b|\bpassag\w*|\bdoxycycline\b|\bDMEM\b",
    r"\bincubat\w*|\b37\s*.?C\b|\bCO2\b|\bantibod\w*|\bimmunosta\w*|\bparaformaldehyde\b|\btrypsin\b",
    r"\bEDTA\b|\bcoat\w*\s+(?:plates?|dishes)|\bplated\b|\bflask\b|\bdissect\w*|\bcryopreserv\w*",
)
# The bench procedure: what an experimental obligation IS about.
_BENCH_PROCEDURE = _re(
    r"\bcultur\w*|\bMatrigel\b|\bmTeSR\b|\bmedium\b|\bmedia\b|\bpassag\w*|\bdoxycycline\b|\bincubat\w*",
    r"\btransfect\w*|\btransduc\w*|\binfect\w*|\belectroporat\w*|\bnucleofect\w*|\btreated with\b",
    r"\bantibod\w*|\bstain\w*|\bfix\w*\s+(?:in|with)|\bimmuno\w*|\bmicroscop\w*|\bconfocal\b",
    r"\bdissect\w*|\bharvest\w*|\bisolat\w*|\bbiops\w*|\bsort\w*|\bFACS\b|\bflow cytometr\w*",
    r"\bRNA was extracted|\bTRIzol\b|\bRNeasy\b|\bQIAGEN\b|\bkit\b|\bcatalog\w*|\breagent\b",
    r"\blibrar(?:y|ies) (?:were|was) (?:prepared|constructed|generated)|\bsequenc\w* on\b",
    r"\bIllumina\b|\bNovaSeq\b|\bHiSeq\b|\bNextSeq\b|\bMiSeq\b|\b10x\b|\bChromium\b|\bSmart-seq",
    r"\bpaired-end\b|\bsingle-end\b|\bread length\b|\bdepth of\b|\bmillion reads\b",
)
# Subscripts are flattened out of a JATS article, so the paper's own "log2fc" reaches bioAF as
# "log 2 fc" and its "padj" as "p adj". A selector that knows only the compact spelling misses the
# thresholds the paper actually states, which is how study 65's stated cutoff reached no obligation.
_FOLD_CHANGE = r"\blog\s*2\s*(?:fc|fold[\s-]?change)|\bfold[\s-]?change\b|\bFC\s*[<>]"
_ADJUSTED_P = r"\bp\s*[-.]?\s*adj\w*|\bq\s*[-.]?\s*value|\bFDR\b|\badjusted\s+P\b|\bBenjamini\b|\bBonferroni\b"

_ANALYSIS_ONLY = _re(
    r"\bDESeq2\b|\bedgeR\b|\blimma\b",
    _FOLD_CHANGE,
    _ADJUSTED_P,
    r"\bGO enrichment|\benrichment analysis|\bWald test\b|\bclustering resolution\b",
)

_SELECTORS: dict[str, Selector] = {
    "S1": Selector(
        _PAPER,
        (DEPOSIT,),
        _ALWAYS_NEEDS,
        _re(r"\borganism\b|\bspecies\b|\bhuman\b|\bmouse\b|\bmurine\b|Homo sapiens|Mus musculus|\bdonor\b|\bpatient\b"),
    ),
    "S1.B": Selector(
        _PAPER,
        (DEPOSIT,),
        (*_ALWAYS_NEEDS, "deposit"),
        _re(r"\borganism\b|\bspecies\b|\bhuman\b|\bmouse\b|Homo sapiens|Mus musculus"),
    ),
    "S2": Selector(
        _PAPER,
        (DEPOSIT,),
        _ALWAYS_NEEDS,
        _re(
            r"\btissue\b|\bcell type\b|\bcell line\b|\bmaterial\b|\bderived from\b|\bprimary\b",
            r"\bbiops\w*|\bgranulosa\b|\biPSC\b|\bhiPSC\b|\bESC\b|\borganoid\b|\bovaroid\b|\bexplant\b",
        ),
    ),
    "S2.B": Selector(
        _PAPER,
        (DEPOSIT,),
        (*_ALWAYS_NEEDS, "deposit"),
        _re(r"\btissue\b|\bcell type\b|\bcell line\b|\bmaterial\b|\bderived from\b|\bsource name\b"),
    ),
    "S3": Selector(
        _DESIGN,
        (DEPOSIT,),
        _ALWAYS_NEEDS,
        _re(
            r"\btreat\w*|\bcontrol\b|\bcondition\b|\barm\b|\bgroup\b|\bvehicle\b|\buntreated\b|\binduc\w*|\btime\s?point"
        ),
    ),
    "S4": Selector(
        _DESIGN,
        (DESIGN, DEPOSIT),
        _ALWAYS_NEEDS,
        _re(
            r"\bn\s*=\s*\d|\bsamples?\b|\bnumber of\b|\btotal of\b|\bexclud\w*|\bincluded\b",
            r"\bper (?:condition|group|sample|clone|line|time\s?point)\b|\b\d+\s+\w+s?\s+per\s+\w+",
        ),
    ),
    "S5": Selector(
        _DESIGN,
        (DESIGN, DEPOSIT),
        _ALWAYS_NEEDS,
        _re(
            r"\breplicat\w*|\bbiological\b|\btechnical\b|\bindependent experiments?\b|\bpaired\b|\bblock\w*",
            r"\bbatch\b|\bdonor\b|\blitter\b|\bn\s*=\s*\d",
            r"\bper (?:clone|line|condition|group|sample|time\s?point)\b|\b\d+\s+\w+s?\s+per\s+\w+",
        ),
    ),
    "E1": Selector(_PROCEDURE, (SUPPLEMENT,), _ALWAYS_NEEDS, _BENCH_PROCEDURE, _ANALYSIS_ONLY),
    "E2": Selector(
        _DESIGN,
        (DESIGN, SUPPLEMENT),
        _ALWAYS_NEEDS,
        _re(
            r"\bcontrol\b|\bcomparator\b|\buntreated\b|\bvehicle\b|\bwild-?type\b|\bknockout\b|\bparental\b",
            r"\breplicat\w*|\bindependent experiments?\b|\bn\s*=\s*\d",
            r"\bper (?:group|clone|line|condition|sample|animal|mouse|donor|well|time\s?point)\b",
            r"\b\d+\s+\w+s?\s+per\s+\w+|\bsamples? per\b|\beach (?:sample|group|condition|clone|line)\b",
            r"\bclone\w*|\bline\w*\b|\bselect\w*|\bhigh-performing\b|\brandomi\w*|\bblind\w*|\bpaired\b|\bpool\w*",
            r"\bcompared (?:to|with)\b|\bversus\b|\brelative to\b|\breference (?:sample|line)\b",
        ),
    ),
    "E3": Selector(
        _DESIGN,
        (DESIGN, SUPPLEMENT),
        _ALWAYS_NEEDS,
        _re(
            r"\bexclud\w*|\bdiscard\w*|\bomitt?\w*|\bfailed\b|\bquality control\b|\bQC\b|\boutlier\w*",
            r"\bcriteri\w*|\bpassed\b|\bthreshold for inclusion\b|\bwere removed\b|\bcontaminat\w*",
            r"\bn\s*=\s*\d|\bsamples?\b|\breplicat\w*",
        ),
    ),
    "M1": Selector(_PROCEDURE, (CODE, SUPPLEMENT), _ALWAYS_NEEDS, _COMPUTATIONAL, _BENCH_MATERIALS),
    "M2": Selector(
        _PROCEDURE,
        (CODE, SUPPLEMENT),
        _ALWAYS_NEEDS,
        _re(
            r"\bgenome\b|\breference\b|\bGRCh\d+|\bhg\d+\b|\bmm\d+\b|\bGENCODE\b|\bEnsembl\b|\bRefSeq\b",
            r"\bannotation\b|\bGTF\b|\bGFF\b|\bindex\b|\bbuild\b|\bdatabase\b|\bversion \d|\bv\d+\.\d+",
        ),
        _BENCH_MATERIALS,
    ),
    "M3": Selector(
        (METHODS, RESULTS, LEGEND, DISCUSSION, OTHER),
        (CODE, SUPPLEMENT),
        _ALWAYS_NEEDS,
        _re(
            r"\btest(?:ed)?\b|\bmodel\w*|\bDESeq2\b|\bedgeR\b|\blimma\b|\bWald\b|\blikelihood\b|\bANOVA\b",
            r"\bt-test\b|\bWilcoxon\b|\bMann-?Whitney\b|\bregression\b|\bcovariat\w*|\bdesign formula\b",
            r"\bmixed (?:effects?|model)\b|\brandom effect\b|\bpaired\b|\breplicat\w*|\bn\s*=\s*\d",
            r"\bindependen\w*|\bunit of\b|\bbatch\b|\bdispersion\b",
        ),
        _BENCH_MATERIALS,
    ),
    "M4": Selector(
        (METHODS, RESULTS, LEGEND, OTHER),
        (CODE, SUPPLEMENT),
        _ALWAYS_NEEDS,
        _re(
            r"\bsignifican\w*|\bp-?value\b",
            _ADJUSTED_P,
            r"\bmultiple (?:testing|comparison)\b|\bcorrect\w*|\bthreshold\w*|\bcut-?off\b",
            _FOLD_CHANGE,
            r"\bup-?regulated\b|\bdown-?regulated\b|\bdirection\b",
        ),
        _BENCH_MATERIALS,
    ),
    "M5": Selector(
        _TRACE,
        (CODE, SUPPLEMENT, TOOLS),
        _ALWAYS_NEEDS,
        _re(
            r"\bcode\b|\bscript\w*|\brepositor\w*|\bGitHub\b|\bZenodo\b|\bnotebook\b|\bavailable at\b",
            r"\bpipeline\b|\bworkflow\b|\breproduc\w*|\bperformed (?:using|with)\b|\banalys\w* (?:using|with)\b",
            r"\bparameter\w*|\bsetting\w*|\bthreshold\w*|\bversion \d|\bconfig\w*",
        ),
    ),
    "M5.B": Selector(
        _TRACE,
        (CODE, SUPPLEMENT, TOOLS),
        (*_ALWAYS_NEEDS, "code"),
        _re(
            r"\bcode\b|\bscript\w*|\bnotebook\b|\bparameter\w*|\bsetting\w*|\bthreshold\w*|\bcut-?off\b",
            r"\bversion \d|\bconfig\w*|\balign\w*|\bfilter\w*|\bnormali[sz]\w*|\benrichment\b",
            _FOLD_CHANGE,
            _ADJUSTED_P,
        ),
    ),
    "C5": Selector(
        (METHODS, AVAILABILITY, OTHER),
        (CODE, TOOLS, SUPPLEMENT),
        (*_ALWAYS_NEEDS, "code"),
        _re(
            r"\bversion \d|\bv\d+\.\d+|\bdefault\w*|\bparameter\w*|\bsetting\w*|\bconfig\w*|\boption\w*",
            r"\btool\b|\bpackage\b|\blibrar\w*|\bfunction\b|\bmode\b|\bflag\b",
        ),
    ),
}


def selector_for(leaf: str) -> Selector | None:
    """The selector for one obligation: its own, else its criterion's."""
    return _SELECTORS.get(leaf) or _SELECTORS.get(str(leaf).partition(".")[0])


# How much a passage's SECTION is worth against the terms it matches. A long results narration
# matches more distinct terms than the one methods paragraph that states the design, and ranking on
# terms alone let the narration take the budget: study 65's "6 ovaroids per sample, 2 samples per
# time point" never reached E2. Big enough that a neutral paragraph of the right section outranks a
# keyword-rich one of the wrong section, small enough that a highly relevant legend still beats an
# unrelated methods paragraph.
SECTION_WEIGHT = 3


def _section_bonus(selector: Selector, kind: str, *, extras: bool = False) -> int:
    """What a passage's section kind is worth. ``sections`` and ``extras`` are in priority order."""
    declared = selector.extras if extras else selector.sections
    if kind not in declared:
        return 0
    return SECTION_WEIGHT * (len(declared) - declared.index(kind))


def _score(text: str, selector: Selector, section: str) -> tuple[int, bool]:
    """``(rank, excluded)``. A passage matching only what the obligation excludes is not a candidate."""
    hits = len({m.group(0).lower() for m in selector.include.finditer(text)})
    against = len({m.group(0).lower() for m in selector.exclude.finditer(text)}) if selector.exclude else 0
    if against and not hits:
        return 0, True
    # A section whose own TITLE names the obligation's subject ranks its paragraphs first: a
    # parameter in "Bulk RNA-seq analysis" is about the analysis in a way a stray token is not.
    title_bonus = 2 if selector.include.search(section or "") else 0
    return hits * 2 + title_bonus - against, False


def packet_for(
    leaf: str,
    *,
    index: dict | None,
    extras: list[dict] | None = None,
    limitations: list[dict] | None = None,
    budget_chars: int = MAX_PACKET_CHARS,
) -> dict:
    """``{"passages", "expansion", "coverage"}``: the evidence this obligation may be judged on.

    ``extras`` are the pieces of evidence that are not the article's running text, each carrying a
    ``kind`` (``deposit``, ``code``, ``tools``, ``supplement``). ``limitations`` are the sources
    bioAF tried and could not get, as ``{"needs", "reason"}``; one that names a source this
    obligation depends on makes its coverage insufficient for an absence finding.
    """
    selector = selector_for(leaf)
    passages = [p for p in (index or {}).get("passages") or [] if isinstance(p, dict)]
    if selector is None:
        # No selector declared: supply the article's text in document order rather than nothing, and
        # say that the selection was not scoped.
        kept = passages[:MAX_PACKET_PASSAGES]
        rest = passages[MAX_PACKET_PASSAGES:]
        return _packet(
            leaf, kept, rest[:MAX_EXPANSION_PASSAGES], _coverage(leaf, passages, kept, 0, rest, rest, [], None)
        )

    eligible = [p for p in passages if p.get("kind") in selector.sections]
    # (rank, relevant, document order, passage). ``relevant`` is whether the passage matched this
    # obligation's own terms, kept apart from the section bonus so that a neutral paragraph deferred
    # by budget is not reported as relevant evidence bioAF failed to carry.
    ranked: list[tuple[int, bool, int, dict]] = []
    excluded = 0
    for order, passage in enumerate(eligible):
        rank, dropped = _score(str(passage.get("text") or ""), selector, str(passage.get("section") or ""))
        if dropped:
            excluded += 1
            continue
        ranked.append((rank + _section_bonus(selector, str(passage.get("kind") or "")), rank > 0, order, passage))
    for order, extra in enumerate(extras or [], start=len(eligible)):
        if not isinstance(extra, dict) or extra.get("kind") not in selector.extras:
            continue
        rank, dropped = _score(str(extra.get("text") or ""), selector, str(extra.get("source") or ""))
        if dropped:
            excluded += 1
            continue
        # Evidence that is not the article's text is eligible BECAUSE of what it is: a deposit record
        # answers "do independent records agree" whether or not it repeats the paper's vocabulary.
        ranked.append(
            (max(rank, 1) + _section_bonus(selector, str(extra.get("kind") or ""), extras=True), True, order, extra)
        )

    ranked.sort(key=lambda row: (-row[0], row[2]))
    # Relevance first; where nothing is relevant, the SECTION is the relevance. A passage that
    # matched none of this obligation's terms is supplied only when nothing matched any: that is
    # what keeps the culture protocol out of a preprocessing packet while leaving a method written
    # in words no selector knows still assessable.
    relevant_rows = [row for row in ranked if row[1]]
    chosen = relevant_rows or ranked
    by_section_only = not relevant_rows and bool(ranked)
    spare = [row for row in ranked if row not in chosen]

    kept: list[dict] = []
    deferred: list[dict] = []
    missed: list[dict] = []
    spent = 0
    for _, relevant, _, passage in chosen:
        text = str(passage.get("text") or "")
        if len(kept) < MAX_PACKET_PASSAGES and spent + len(text) <= budget_chars:
            kept.append(passage)
            spent += len(text)
            continue
        deferred.append(passage)
        if relevant:
            # Evidence that matched this obligation's own terms and did not fit. This is what makes
            # the packet truncated, and what section 8 refuses to report an absence over.
            missed.append(passage)

    # Back into document order: an assessor reading a procedure needs its steps in the order the
    # paper wrote them, not in the order a ranking function liked them.
    order_of = {id(p): i for i, p in enumerate(eligible + list(extras or []))}
    kept.sort(key=lambda p: order_of.get(id(p), 0))
    # What the one targeted expansion may add: the relevant passages the budget left out first, then
    # the eligible sections the ranking passed over. One second ask, not a nested retry loop.
    expansion = deferred + [row[3] for row in spare]
    coverage = _coverage(leaf, eligible, kept, excluded, deferred, missed, limitations or [], selector)
    coverage["selected_by"] = "section" if by_section_only else "relevance"
    return _packet(leaf, kept, expansion[:MAX_EXPANSION_PASSAGES], coverage)


def _packet(leaf: str, kept: list[dict], expansion: list[dict], coverage: dict) -> dict:
    return {
        "leaf": leaf,
        "passages": [_supplied(p) for p in kept],
        "expansion": [_supplied(p) for p in expansion],
        "coverage": coverage,
    }


def _supplied(passage: dict) -> dict:
    """One passage as the assessor sees it: an id it can cite, where it came from, and its words."""
    return {
        "id": str(passage.get("id")),
        "source": str(passage.get("source") or "the paper"),
        "text": str(passage.get("text") or ""),
        "section": passage.get("section"),
        "location": passage.get("location"),
        "certain": passage.get("certain"),
        # plan_8_6 section 7: what kind of fact this passage carries, where it is more than the
        # paper's running text. E2.B's acceptance rests on being cited a design fact.
        "carries": passage.get("carries"),
    }


def _coverage(
    leaf: str,
    eligible: list[dict],
    kept: list[dict],
    excluded: int,
    deferred: list[dict],
    missed: list[dict],
    limitations: list[dict],
    selector: Selector | None,
) -> dict:
    """What this packet carried, and what it did not. Section 8 rests an absence finding on it.

    ``deferred`` is everything the budget left out; ``missed`` is the part of it that matched this
    obligation's own terms. Only the second makes the packet truncated: an eligible paragraph that
    said nothing about this obligation is not relevant evidence bioAF failed to carry, and counting
    it as such would leave every obligation on a real paper permanently unable to report an absence.
    """
    needs = set(selector.needs) if selector else set()
    unavailable = [
        str(limit.get("reason") or limit.get("needs"))
        for limit in limitations
        if isinstance(limit, dict) and str(limit.get("needs")) in needs
    ]
    supplied = []
    for passage in kept:
        label = str(passage.get("section") or passage.get("source") or "").strip()
        if label and label not in supplied:
            supplied.append(label)
    reasons = []
    if not kept:
        reasons.append(
            "bioAF holds no evidence relevant to this obligation"
            + (f"; {excluded} passages were about something else" if excluded else "")
        )
    if unavailable:
        reasons.append("; ".join(unavailable))
    if missed:
        reasons.append(f"{len(missed)} relevant passages did not fit this request's budget")
    sufficient = bool(kept) and not unavailable and not missed
    return {
        "packet_version": PACKET_VERSION,
        "leaf": leaf,
        "sufficient": sufficient,
        "supplied": supplied,
        "sections_available": sorted({str(p.get("section") or p.get("kind") or "") for p in eligible} - {""}),
        "passages_supplied": len(kept),
        "excluded_irrelevant": excluded,
        "unavailable": unavailable,
        "deferred": [str(p.get("id")) for p in deferred],
        "deferred_relevant": [str(p.get("id")) for p in missed],
        "truncated": bool(missed),
        "reason": "; ".join(reasons),
    }

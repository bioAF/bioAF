"""What bioAF keeps of a failed run's log.

A failed run stored the LAST 500 characters of its log. Nextflow puts the
diagnosis at the START of its error block (the process that failed and its exit
status) and boilerplate at the end ("Tip: you can try...", "Check '.nextflow.log'
for details"), so the stored text reliably kept the part nobody needs and dropped
the part that names the failure.

Observed on run 31 of the demo: the stored message began mid-URI and the process
name and exit status were gone, so diagnosing it needed the logs endpoint. The
budget is not the problem and is unchanged; where the budget is spent is.
"""

from app.services.pipeline_monitor_service import PipelineMonitorService

_excerpt = PipelineMonitorService._failure_excerpt

# The shape Nextflow actually emits, from run 31.
_NEXTFLOW_FAILURE = """
executor >  k8s (fusion enabled) (6)
[77/7b3ca2] NFC...OLS_IDXSTATS (SRX30659361) | 1 of 1
ERROR ~ Error executing process > 'NFCORE_BAMTOFASTQ:BAMTOFASTQ:PREPARE_INDICES:SAMTOOLS_FAIDX ([])'

Caused by:
  Process `NFCORE_BAMTOFASTQ:...:SAMTOOLS_FAIDX ([])` terminated with an error exit status (1)

Command executed:

  samtools faidx

Command exit status:
  1

Command error:
  cut: .fai: No such file or directory

Work dir:
  gs://bioaf-raw-bioaf-co-4bd459/nextflow-work/07/a21a9eb95e58c52cbf45fc33eb976a

Container:
  wave.seqera.io/wt/1533fda4b2c6/library/htslib_samtools:1.23.1

Tip: you can try to figure out what's wrong by changing to the process work dir

 -- Check '.nextflow.log' file for details
"""


def test_the_excerpt_names_the_process_that_failed():
    """The single most useful line, and the one the old slice always dropped."""
    excerpt = _excerpt(_NEXTFLOW_FAILURE)

    assert "SAMTOOLS_FAIDX" in excerpt
    assert "Error executing process" in excerpt


def test_the_excerpt_keeps_the_exit_status_and_the_command_error():
    excerpt = _excerpt(_NEXTFLOW_FAILURE)

    assert "exit status (1)" in excerpt
    assert "No such file or directory" in excerpt


def test_the_excerpt_does_not_start_mid_word():
    """What made the stored message unreadable: it began part-way through a URI."""
    excerpt = _excerpt(_NEXTFLOW_FAILURE)

    assert excerpt.startswith("ERROR ~")


def test_the_budget_is_unchanged():
    """The cap is not the problem, so it does not move: a longer message would
    change what every surface rendering it has to fit."""
    assert len(_excerpt("x" * 5000)) == 500


def test_a_log_with_no_error_banner_keeps_its_tail():
    """Unchanged behavior for anything that is not a Nextflow error block, where
    the end is still the best guess at what went wrong."""
    log = "line\n" * 400

    assert _excerpt(log) == log[-500:]


def test_a_short_log_is_kept_whole():
    assert _excerpt("job died") == "job died"


def test_the_last_banner_wins_when_a_log_carries_several():
    """A retried process logs its banner more than once, and the final one is the
    failure that actually ended the run."""
    log = "ERROR ~ first failure, retried\n" + ("filler\n" * 10) + "ERROR ~ second failure, fatal\n"

    assert _excerpt(log).startswith("ERROR ~ second failure")


def test_nothing_at_all_is_handled():
    assert _excerpt("") == ""
    assert _excerpt(None) == ""

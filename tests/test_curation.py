"""Curation engine: favourites and artist blocks."""

from tidal_sync.domain.results import UploadOutcome


def test_upload_outcome_is_importable_from_domain():
    """Two engines share this type, so it lives in domain, not in the importer."""
    outcome = UploadOutcome(applied=["1"], rejected=["2"])

    assert outcome.applied == ["1"]
    assert outcome.rejected == ["2"]
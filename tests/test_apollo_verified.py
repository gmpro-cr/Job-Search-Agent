"""Apollo emails are only accepted when Apollo marks them verified."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agent.nodes import apollo_verified_email


def test_verified_email_is_kept():
    assert apollo_verified_email({"email": "a@acme.com", "email_status": "verified"}) == "a@acme.com"


def test_guessed_or_unknown_status_is_dropped():
    assert apollo_verified_email({"email": "a@acme.com", "email_status": "guessed"}) == ""
    assert apollo_verified_email({"email": "a@acme.com", "email_status": "likely to engage"}) == ""
    assert apollo_verified_email({"email": "a@acme.com"}) == ""


def test_locked_placeholder_is_dropped():
    person = {"email": "email_not_unlocked@domain.com", "email_status": "verified"}
    assert apollo_verified_email(person) == ""

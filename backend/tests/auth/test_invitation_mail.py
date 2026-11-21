from unittest.mock import patch

import pytest
from app.auth.invitation_mail import InvitationMailer, InvitationMailUnavailable


def mailer():
    return InvitationMailer("https://notebook.example", "smtp.example", 587,
                            "sender@example.net", "fixture-password")


def test_invitation_mail_has_one_recipient_and_uses_tls_before_authentication():
    with patch("smtplib.SMTP") as connect:
        session = connect.return_value.__enter__.return_value
        session.send_message.return_value = {}
        mailer().send("recipient@example.net", "fixture-token")
        methods = [call[0] for call in session.method_calls]
        assert methods == ["starttls", "login", "send_message"]
        context = session.starttls.call_args.kwargs["context"]
        assert context.check_hostname
        message = session.send_message.call_args.args[0]
        assert session.send_message.call_args.kwargs["to_addrs"] == ["recipient@example.net"]
        assert "https://notebook.example/invite#token=fixture-token" in message.get_content()


def test_transport_failure_is_sanitized_and_never_authenticates_without_tls():
    with patch("smtplib.SMTP") as connect:
        session = connect.return_value.__enter__.return_value
        session.starttls.side_effect = OSError("private SMTP diagnostics")
        with pytest.raises(InvitationMailUnavailable) as exc:
            mailer().send("recipient@example.net", "fixture-token")
        assert str(exc.value) == "Invitation mail could not be confirmed"
        session.login.assert_not_called()
        session.send_message.assert_not_called()


def test_recipient_refusal_is_not_reported_as_success():
    with patch("smtplib.SMTP") as connect:
        session = connect.return_value.__enter__.return_value
        session.send_message.return_value = {"recipient@example.net": (550, b"rejected")}
        with pytest.raises(InvitationMailUnavailable):
            mailer().send("recipient@example.net", "fixture-token")


@pytest.mark.parametrize("address", ["first@example.net,second@example.net", "a@example.net\nBcc:b@example.net"])
def test_invalid_recipient_never_opens_connection(address):
    with patch("smtplib.SMTP") as connect:
        with pytest.raises(InvitationMailUnavailable):
            mailer().send(address, "fixture-token")
        connect.assert_not_called()

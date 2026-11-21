"""SMTP transport; call only after a successful invitation transaction commits."""

import re
import smtplib
import ssl
from dataclasses import dataclass, field
from email.headerregistry import Address
from email.message import EmailMessage
from urllib.parse import urlencode


class InvitationMailUnavailable(RuntimeError):
    """SMTP acceptance was not confirmed; do not expose server diagnostics."""


@dataclass(frozen=True)
class InvitationMailer:
    public_origin: str
    host: str
    port: int
    sender: str
    password: str = field(repr=False)

    def send(self, email: str, token: str) -> None:
        failed = False
        try:
            if not re.fullmatch(r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+", email):
                raise ValueError("invalid recipient")
            recipient = Address(addr_spec=email)
            sender = Address(addr_spec=self.sender)
            message = EmailMessage()
            message["From"] = sender
            message["To"] = recipient
            message["Subject"] = "Du bist zu Notebook eingeladen"
            link = f"{self.public_origin.rstrip('/')}/invite#{urlencode({'token': token})}"
            message.set_content(
                "Du bist zu Notebook eingeladen. Öffne die Einladung mit dem Konto, "
                "dessen bestätigte E-Mail-Adresse zu dieser Nachricht passt.\n\n"
                f"{link}\n\nWenn du keine Einladung erwartest, kannst du diese Nachricht ignorieren.\n"
            )
            with smtplib.SMTP(self.host, self.port, timeout=10) as client:
                client.starttls(context=ssl.create_default_context())
                client.login(sender.addr_spec, self.password)
                refused = client.send_message(message, from_addr=sender.addr_spec,
                                              to_addrs=[recipient.addr_spec])
                failed = bool(refused)
        except (OSError, smtplib.SMTPException, ValueError):
            failed = True
        if failed:
            raise InvitationMailUnavailable("Invitation mail could not be confirmed")

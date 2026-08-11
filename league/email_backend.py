from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend as DjangoSMTPEmailBackend
from django.utils.functional import cached_property


class EmailBackend(DjangoSMTPEmailBackend):
    """SMTP backend that can add a private CA without disabling TLS checks."""

    @cached_property
    def ssl_context(self):
        context = super().ssl_context
        ca_file = getattr(settings, 'EMAIL_CA_FILE', '')
        if ca_file:
            context.load_verify_locations(cafile=ca_file)
        return context

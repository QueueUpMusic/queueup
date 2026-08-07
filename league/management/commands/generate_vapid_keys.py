import base64
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.core.management.base import BaseCommand


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


class Command(BaseCommand):
    help = 'Generate a VAPID private/public key pair for browser push.'

    def handle(self, *args, **options):
        key = ec.generate_private_key(ec.SECP256R1())
        private_der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        public_raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.stdout.write(f'WEBPUSH_PRIVATE_KEY={b64url(private_der)}')
        self.stdout.write(f'WEBPUSH_PUBLIC_KEY={b64url(public_raw)}')

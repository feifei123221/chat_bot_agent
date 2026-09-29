from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path
import socket

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


ROOT = Path(__file__).resolve().parents[1]
CERT_DIR = ROOT / "certs"
CERT_PATH = CERT_DIR / "lan-cert.pem"
KEY_PATH = CERT_DIR / "lan-key.pem"


def local_ipv4() -> str:
    candidates = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    for entry in candidates:
        address = entry[4][0]
        if not address.startswith(("127.", "169.254.")):
            return address
    return "127.0.0.1"


def certificate_needs_refresh() -> bool:
    if not CERT_PATH.exists() or not KEY_PATH.exists():
        return True
    try:
        certificate = x509.load_pem_x509_certificate(CERT_PATH.read_bytes())
    except ValueError:
        return True
    return certificate.not_valid_after_utc <= datetime.now(timezone.utc) + timedelta(days=7)


def generate() -> None:
    if not certificate_needs_refresh():
        return

    CERT_DIR.mkdir(exist_ok=True)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    hostname = socket.gethostname()
    address = local_ipv4()
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, hostname)]
    )
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.DNSName(hostname), x509.IPAddress(ip_address(address))]
            ),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )
    KEY_PATH.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    CERT_PATH.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    print(f"Generated LAN certificate for {address}")


if __name__ == "__main__":
    generate()

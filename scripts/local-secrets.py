#!/usr/bin/env python3
"""Create and rotate workstation-only secrets without printing their values."""

import argparse
import base64
import hmac
import json
import os
import re
import secrets
import ssl
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILE = ROOT / "k8s/overlays/local/secrets-local.json"
NAMESPACE = "demo"
CONTEXT = "takehome"
SERVER_CA_SECRET = "postgres-cluster-server-ca-v2"
CLIENT_CA_SECRET = "postgres-cluster-client-ca-v2"
SERVER_TLS_SECRET = "postgres-cluster-server-tls-v2"
REPLICATION_TLS_SECRET = "postgres-cluster-replication-tls-v2"
SECRETS = {
    "bootstrap": ("datastore-credentials-v2", "demo"),
    "api": ("api-datastore-credentials-v2", "snapshot_api"),
    "collector": ("collector-datastore-credentials-v2", "snapshot_collector"),
}


def fail(message):
    raise SystemExit(message)


def run_kubectl(arguments, *, input_bytes=None):
    return subprocess.run(
        ["kubectl", "--context", CONTEXT, *arguments],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def load_credentials(path, create=False):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.exists() and create:
        payload = {
            "version": 2,
            "credentials": {
                role: {"username": username, "password": secrets.token_hex(32)}
                for role, (_, username) in SECRETS.items()
            },
        }
        descriptor, temporary_name = tempfile.mkstemp(prefix=".secrets-local.", dir=path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(payload, output, separators=(",", ":"))
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_name, path)
            os.chmod(path, 0o600)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise

    try:
        metadata = path.lstat()
    except OSError as error:
        fail(f"Cannot access local credentials file: {error.strerror}.")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        fail("The local credentials path must be a regular file, not a link or special file.")
    if metadata.st_uid != os.getuid():
        fail("The local credentials file must be owned by the current user.")
    os.chmod(path, 0o600)

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        fail("The local credentials file is unreadable or malformed. Restore it from a secure backup.")
    if payload.get("version") != 2 or not isinstance(payload.get("credentials"), dict):
        fail("The local credentials file has an unsupported format. Restore its matching versioned backup.")
    for key, (_, expected_username) in SECRETS.items():
        entry = payload["credentials"].get(key)
        if not isinstance(entry, dict):
            fail("The local credentials file is incomplete. Restore the complete versioned backup.")
        password = entry.get("password", "")
        if entry.get("username") != expected_username or not re.fullmatch(r"[0-9a-f]{64}", password):
            fail("The local credentials file is invalid. Restore the complete versioned backup.")
    return payload["credentials"]


def get_secret(name):
    result = run_kubectl(["-n", NAMESPACE, "get", "secret", name, "-o", "json"])
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        fail("Kubernetes returned an unreadable Secret response. Check the cluster API and retry.")


def encoded(value):
    return base64.b64encode(value).decode("ascii")


def secret_document(name, credential, ca_certificate=None):
    data = {
        "username": encoded(credential["username"].encode("utf-8")),
        "password": encoded(credential["password"].encode("ascii")),
    }
    if ca_certificate is not None:
        data["ca.crt"] = encoded(ca_certificate)
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "labels": {"cnpg.io/reload": "true"},
        },
        "immutable": True,
        "type": "kubernetes.io/basic-auth",
        "data": data,
    }


def assert_secret_matches(name, credential, ca_certificate=None):
    secret = get_secret(name)
    if secret is None:
        return False
    expected = {
        "username": credential["username"].encode("utf-8"),
        "password": credential["password"].encode("ascii"),
    }
    if ca_certificate is not None:
        expected["ca.crt"] = ca_certificate
    try:
        actual = {
            key: base64.b64decode(secret.get("data", {}).get(key, ""), validate=True)
            for key in expected
        }
    except (ValueError, TypeError):
        fail(f"Installed Secret {name} is invalid. Rotate it to a new immutable version before retrying.")
    if secret.get("immutable") is not True or any(
        not hmac.compare_digest(actual[key], expected[key]) for key in expected
    ):
        fail(f"Installed Secret {name} does not match the protected local credential set. Rotate it to a new version.")
    return True


def apply_secret(name, credential, ca_certificate=None):
    existing = assert_secret_matches(name, credential, ca_certificate)
    if existing:
        return
    document = secret_document(name, credential, ca_certificate)
    result = run_kubectl(
        ["-n", NAMESPACE, "apply", "-f", "-"],
        input_bytes=json.dumps(document, separators=(",", ":")).encode("utf-8"),
    )
    if result.returncode != 0:
        fail(f"Could not create immutable Secret {name}. Check Kubernetes access and retry.")


def read_server_ca_certificate():
    secret = get_secret(SERVER_CA_SECRET)
    if not secret:
        fail("The versioned PostgreSQL server CA is missing. Run the local startup script again.")
    try:
        certificate = base64.b64decode(secret["data"]["ca.crt"], validate=True)
    except (KeyError, ValueError, TypeError):
        fail("The versioned PostgreSQL server CA Secret is invalid. Restore its secure backup before retrying.")
    if not certificate.startswith(b"-----BEGIN CERTIFICATE-----"):
        fail("The versioned PostgreSQL server CA certificate is invalid. Restore its secure backup before retrying.")
    return certificate


def openssl(arguments, *, input_bytes=None):
    result = subprocess.run(
        ["openssl", *arguments],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        if arguments and arguments[0] == "verify":
            certificate_kind = "server hostname" if "-verify_hostname" in arguments else "replication client"
            fail(f"OpenSSL could not validate the versioned PostgreSQL {certificate_kind} certificate against its CA.")
        operation = arguments[0] if arguments else "command"
        fail(f"OpenSSL '{operation}' failed while creating or validating a versioned PostgreSQL certificate. Check OpenSSL and retry.")
    return result.stdout


def assert_certificate_dns_name(certificate_path, hostname):
    try:
        decoded = ssl._ssl._test_decode_cert(str(certificate_path))
        certificate_names = {
            value
            for kind, value in decoded.get("subjectAltName", ())
            if kind == "DNS"
        }
    except (OSError, ValueError, ssl.SSLError):
        fail("Python could not inspect the PostgreSQL server certificate names.")
    if hostname not in certificate_names:
        fail("The PostgreSQL server certificate is missing the database service DNS name.")


def create_ca_secret(name, common_name):
    existing = get_secret(name)
    if existing is not None:
        try:
            certificate = base64.b64decode(existing["data"]["ca.crt"], validate=True)
            private_key = base64.b64decode(existing["data"]["ca.key"], validate=True)
        except (KeyError, ValueError, TypeError):
            fail(f"Versioned CA Secret {name} is incomplete. Restore its secure backup before retrying.")
        if existing.get("immutable") is not True:
            fail(f"Versioned CA Secret {name} must be immutable. Rotate it to a new version before retrying.")
        with tempfile.TemporaryDirectory(prefix="takehome-ca-check-") as directory:
            os.chmod(directory, 0o700)
            cert_path = Path(directory) / "ca.crt"
            key_path = Path(directory) / "ca.key"
            cert_path.write_bytes(certificate)
            key_path.write_bytes(private_key)
            certificate_public_key = openssl(["x509", "-in", str(cert_path), "-pubkey", "-noout"])
            key_public_key = openssl(["pkey", "-in", str(key_path), "-pubout"])
            if not hmac.compare_digest(certificate_public_key, key_public_key):
                fail(f"Versioned CA Secret {name} contains a mismatched certificate and key.")
            openssl(["x509", "-in", str(cert_path), "-noout", "-checkend", str(30 * 24 * 60 * 60)])
        return

    with tempfile.TemporaryDirectory(prefix="takehome-ca-create-") as directory:
        os.chmod(directory, 0o700)
        key_path = Path(directory) / "ca.key"
        certificate_path = Path(directory) / "ca.crt"
        config_path = Path(directory) / "openssl.cnf"
        config_path.write_text(
            "[req]\n"
            "distinguished_name = dn\n"
            "x509_extensions = ca_ext\n"
            "prompt = no\n"
            "[dn]\n"
            f"CN = {common_name}\n"
            "[ca_ext]\n"
            "basicConstraints = critical,CA:TRUE\n"
            "keyUsage = critical,keyCertSign,cRLSign\n"
            "subjectKeyIdentifier = hash\n",
            encoding="ascii",
        )
        openssl(["ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(key_path)])
        os.chmod(key_path, 0o600)
        openssl([
            "req", "-new", "-x509", "-sha256", "-days", "3650",
            "-config", str(config_path), "-key", str(key_path), "-out", str(certificate_path),
        ])
        secret = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": name, "namespace": NAMESPACE},
            "immutable": True,
            "type": "Opaque",
            "data": {
                "ca.crt": encoded(certificate_path.read_bytes()),
                "ca.key": encoded(key_path.read_bytes()),
            },
        }
        result = run_kubectl(
            ["-n", NAMESPACE, "apply", "-f", "-"],
            input_bytes=json.dumps(secret, separators=(",", ":")).encode("utf-8"),
        )
        if result.returncode != 0:
            fail(f"Could not create immutable CA Secret {name}. Check Kubernetes access and retry.")


def create_tls_secret(name, ca_name, common_name, purpose, dns_names=()):
    ca_secret = get_secret(ca_name)
    if not ca_secret:
        fail(f"CA Secret {ca_name} is missing. Run the local startup script again.")
    try:
        ca_certificate = base64.b64decode(ca_secret["data"]["ca.crt"], validate=True)
        ca_private_key = base64.b64decode(ca_secret["data"]["ca.key"], validate=True)
    except (KeyError, ValueError, TypeError):
        fail(f"CA Secret {ca_name} is incomplete. Restore its secure backup before retrying.")

    existing = get_secret(name)
    if existing is not None:
        if existing.get("immutable") is not True or existing.get("type") != "kubernetes.io/tls":
            fail(f"Versioned TLS Secret {name} must be an immutable TLS Secret. Rotate it to a new version.")
        try:
            certificate = base64.b64decode(existing["data"]["tls.crt"], validate=True)
            private_key = base64.b64decode(existing["data"]["tls.key"], validate=True)
        except (KeyError, ValueError, TypeError):
            fail(f"Versioned TLS Secret {name} is incomplete. Restore its secure backup before retrying.")
    else:
        certificate = None
        private_key = None

    with tempfile.TemporaryDirectory(prefix="takehome-tls-create-") as directory:
        os.chmod(directory, 0o700)
        ca_path = Path(directory) / "ca.crt"
        ca_key_path = Path(directory) / "ca.key"
        cert_path = Path(directory) / "tls.crt"
        key_path = Path(directory) / "tls.key"
        config_path = Path(directory) / "openssl.cnf"
        ca_path.write_bytes(ca_certificate)
        ca_key_path.write_bytes(ca_private_key)

        section = "server_cert" if purpose == "server" else "client_cert"
        extensions = (
            "[server_cert]\n"
            "basicConstraints = critical,CA:FALSE\n"
            "keyUsage = critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage = serverAuth\n"
            "subjectAltName = @alt_names\n"
            "[client_cert]\n"
            "basicConstraints = critical,CA:FALSE\n"
            "keyUsage = critical,digitalSignature\n"
            "extendedKeyUsage = clientAuth\n"
        )
        subject_alt_names = "".join(f"DNS.{index} = {name}\n" for index, name in enumerate(dns_names, 1))
        config_path.write_text(
            "[req]\n"
            "distinguished_name = dn\n"
            "prompt = no\n"
            "[dn]\n"
            f"CN = {common_name}\n"
            f"{extensions}"
            "[alt_names]\n"
            f"{subject_alt_names}",
            encoding="ascii",
        )

        if certificate is None:
            openssl(["ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(key_path)])
            os.chmod(key_path, 0o600)
            openssl(["req", "-new", "-key", str(key_path), "-config", str(config_path), "-out", str(Path(directory) / "tls.csr")])
            openssl([
                "x509", "-req", "-sha256", "-days", "365",
                "-in", str(Path(directory) / "tls.csr"),
                "-CA", str(ca_path), "-CAkey", str(ca_key_path), "-CAcreateserial",
                "-out", str(cert_path), "-extfile", str(config_path), "-extensions", section,
            ])
            certificate = cert_path.read_bytes()
            private_key = key_path.read_bytes()
        else:
            cert_path.write_bytes(certificate)
            key_path.write_bytes(private_key)

        certificate_public_key = openssl(["x509", "-in", str(cert_path), "-pubkey", "-noout"])
        key_public_key = openssl(["pkey", "-in", str(key_path), "-pubout"])
        if not hmac.compare_digest(certificate_public_key, key_public_key):
            fail(f"Versioned TLS Secret {name} contains a mismatched certificate and key.")
        verify_args = ["verify", "-purpose", "sslserver" if purpose == "server" else "sslclient", "-CAfile", str(ca_path)]
        if purpose == "server":
            assert_certificate_dns_name(cert_path, "postgres-cluster-rw.demo.svc.cluster.local")
        openssl([*verify_args, str(cert_path)])
        openssl(["x509", "-in", str(cert_path), "-noout", "-checkend", str(30 * 24 * 60 * 60)])

        if existing is None:
            document = {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": name, "namespace": NAMESPACE},
                "immutable": True,
                "type": "kubernetes.io/tls",
                "data": {
                    "tls.crt": encoded(certificate),
                    "tls.key": encoded(private_key),
                },
            }
            result = run_kubectl(
                ["-n", NAMESPACE, "apply", "-f", "-"],
                input_bytes=json.dumps(document, separators=(",", ":")).encode("utf-8"),
            )
            if result.returncode != 0:
                fail(f"Could not create immutable TLS Secret {name}. Check Kubernetes access and retry.")


def ensure_ca_secrets():
    create_ca_secret(SERVER_CA_SECRET, "takehome PostgreSQL server CA v2")
    create_ca_secret(CLIENT_CA_SECRET, "takehome PostgreSQL client CA v2")
    server_names = (
        "postgres-cluster-rw",
        "postgres-cluster-rw.demo",
        "postgres-cluster-rw.demo.svc",
        "postgres-cluster-rw.demo.svc.cluster.local",
        "postgres-cluster-r",
        "postgres-cluster-r.demo",
        "postgres-cluster-r.demo.svc",
        "postgres-cluster-r.demo.svc.cluster.local",
        "postgres-cluster-ro",
        "postgres-cluster-ro.demo",
        "postgres-cluster-ro.demo.svc",
        "postgres-cluster-ro.demo.svc.cluster.local",
    )
    create_tls_secret(
        SERVER_TLS_SECRET,
        SERVER_CA_SECRET,
        "postgres-cluster-rw.demo.svc.cluster.local",
        "server",
        server_names,
    )
    create_tls_secret(
        REPLICATION_TLS_SECRET,
        CLIENT_CA_SECRET,
        "streaming_replica",
        "client",
    )
    print("Versioned immutable PostgreSQL CAs and TLS certificates are ready.")


def verify_current_certificates():
    result = run_kubectl(["-n", NAMESPACE, "get", "cluster", "postgres-cluster", "-o", "json"])
    if result.returncode != 0:
        fail("Could not inspect the PostgreSQL cluster certificate status.")
    try:
        certificate_status = json.loads(result.stdout).get("status", {}).get("certificates", {})
    except json.JSONDecodeError:
        fail("Kubernetes returned an unreadable PostgreSQL certificate status.")
    if (
        certificate_status.get("serverCASecret") != SERVER_CA_SECRET
        or certificate_status.get("clientCASecret") != CLIENT_CA_SECRET
        or certificate_status.get("serverTLSSecret") != SERVER_TLS_SECRET
        or certificate_status.get("replicationTLSSecret") != REPLICATION_TLS_SECRET
    ):
        fail("CloudNativePG has not switched to the versioned CA and TLS Secrets yet.")

    server_ca = get_secret(SERVER_CA_SECRET)
    client_ca = get_secret(CLIENT_CA_SECRET)
    server_certificate_secret = get_secret(certificate_status.get("serverTLSSecret", ""))
    replication_certificate_secret = get_secret(certificate_status.get("replicationTLSSecret", ""))
    try:
        server_ca_certificate = base64.b64decode(server_ca["data"]["ca.crt"], validate=True)
        client_ca_certificate = base64.b64decode(client_ca["data"]["ca.crt"], validate=True)
        server_certificate = base64.b64decode(server_certificate_secret["data"]["tls.crt"], validate=True)
        replication_certificate = base64.b64decode(replication_certificate_secret["data"]["tls.crt"], validate=True)
    except (KeyError, TypeError, ValueError):
        fail("CloudNativePG has not issued server and replication certificates for the versioned CAs yet.")

    with tempfile.TemporaryDirectory(prefix="takehome-cert-verify-") as directory:
        os.chmod(directory, 0o700)
        paths = {name: Path(directory) / name for name in ("server-ca.crt", "client-ca.crt", "server.crt", "replication.crt")}
        paths["server-ca.crt"].write_bytes(server_ca_certificate)
        paths["client-ca.crt"].write_bytes(client_ca_certificate)
        paths["server.crt"].write_bytes(server_certificate)
        paths["replication.crt"].write_bytes(replication_certificate)
        openssl(["verify", "-purpose", "sslserver", "-CAfile", str(paths["server-ca.crt"]), str(paths["server.crt"])])
        assert_certificate_dns_name(paths["server.crt"], "postgres-cluster-rw.demo.svc.cluster.local")
        openssl(["verify", "-purpose", "sslclient", "-CAfile", str(paths["client-ca.crt"]), str(paths["replication.crt"])])


def ensure_credentials(path):
    generated = not path.exists()
    load_credentials(path, create=True)
    if generated:
        print("Created a protected local credential file for this Minikube profile.")
    else:
        print("Using the existing protected local credential file.")


def create_bootstrap_secret(path):
    credentials = load_credentials(path)
    apply_secret(SECRETS["bootstrap"][0], credentials["bootstrap"])
    print("Bootstrap database credentials are ready.")


def create_application_secrets(path):
    credentials = load_credentials(path)
    ca_certificate = read_server_ca_certificate()
    apply_secret(SECRETS["api"][0], credentials["api"], ca_certificate)
    apply_secret(SECRETS["collector"][0], credentials["collector"], ca_certificate)
    print("API and collector database credentials are ready with the current PostgreSQL CA.")


def rotate_bootstrap_password(path):
    credentials = load_credentials(path)
    result = run_kubectl([
        "-n", NAMESPACE, "get", "pods",
        "-l", "cnpg.io/cluster=postgres-cluster,role=primary",
        "-o", "jsonpath={.items[0].metadata.name}",
    ])
    pod_name = result.stdout.decode("utf-8").strip() if result.returncode == 0 else ""
    if not pod_name:
        fail("CloudNativePG has no ready PostgreSQL primary. Restore cluster readiness, then retry.")
    password = credentials["bootstrap"]["password"]
    sql = f"ALTER ROLE demo WITH PASSWORD '{password}';\n".encode("ascii")
    result = run_kubectl([
        "-n", NAMESPACE, "exec", "-i", pod_name, "--",
        "psql", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "demo",
    ], input_bytes=sql)
    if result.returncode != 0:
        fail("Could not rotate the local database owner password. Check the PostgreSQL primary and retry.")
    print("Local database owner password matches its new versioned Secret.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("ensure", "certificates", "bootstrap", "roles", "rotate-owner", "verify-certificates"))
    parser.add_argument("--credentials-file", type=Path, default=DEFAULT_FILE)
    args = parser.parse_args()

    if args.command == "ensure":
        ensure_credentials(args.credentials_file)
    elif args.command == "certificates":
        ensure_ca_secrets()
    elif args.command == "bootstrap":
        create_bootstrap_secret(args.credentials_file)
    elif args.command == "roles":
        create_application_secrets(args.credentials_file)
    elif args.command == "rotate-owner":
        rotate_bootstrap_password(args.credentials_file)
    elif args.command == "verify-certificates":
        verify_current_certificates()


if __name__ == "__main__":
    main()

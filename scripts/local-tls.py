#!/usr/bin/env python3
"""Create local HTTPS certificates and provision versioned local TLS Secrets."""

import argparse
import base64
import hmac
import json
import os
import ssl
import stat
import subprocess
import tempfile
from pathlib import Path


NAMESPACE = "demo"
CONTEXT = "takehome"
SECRET_FILES = {
    "web-tls-v3": {"tls.crt": "web-v3.crt", "tls.key": "web-v3.key", "ca.crt": "ca-v2.crt"},
    "api-tls-v2": {"tls.crt": "api-v2.crt", "tls.key": "api-v2.key"},
}


def fail(message):
    raise SystemExit(message)


def tls_directory():
    path = Path(os.environ.get("TAKEHOME_TLS_DIR", Path.home() / ".config" / "takehome" / "tls")).expanduser()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        fail("The local TLS directory must be a regular directory, not a link or special file.")
    if metadata.st_uid != os.getuid():
        fail("The local TLS directory must be owned by the current user.")
    os.chmod(path, 0o700)
    return path


def openssl(arguments):
    result = subprocess.run(
        ["openssl", *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        fail("OpenSSL could not create or validate local HTTPS certificates. Check OpenSSL and retry.")
    return result.stdout


def secure_regular_file(path, mode, label):
    try:
        metadata = path.lstat()
    except OSError:
        fail(f"The local {label} file is missing or inaccessible.")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        fail(f"The local {label} path must be a regular file, not a link or special file.")
    if metadata.st_uid != os.getuid():
        fail(f"The local {label} file must be owned by the current user.")
    os.chmod(path, mode)


def public_key_from_certificate(path):
    return openssl(["x509", "-in", str(path), "-pubkey", "-noout"])


def public_key_from_private_key(path):
    return openssl(["pkey", "-in", str(path), "-pubout"])


def assert_pair(cert_path, key_path, label):
    try:
        cert_public = public_key_from_certificate(cert_path)
        key_public = public_key_from_private_key(key_path)
    except SystemExit:
        fail(f"The local {label} certificate or private key is invalid. Restore it or rotate the TLS Secret version.")
    if not hmac.compare_digest(cert_public, key_public):
        fail(f"The local {label} certificate and private key do not match. Rotate the TLS Secret version.")


def assert_names(cert_path, expected_names):
    try:
        details = ssl._ssl._test_decode_cert(str(cert_path))
    except (OSError, ValueError, ssl.SSLError):
        fail("Python could not inspect a local HTTPS certificate.")
    actual_dns = {value for kind, value in details.get("subjectAltName", ()) if kind == "DNS"}
    actual_ips = {value for kind, value in details.get("subjectAltName", ()) if kind == "IP Address"}
    if not expected_names["dns"].issubset(actual_dns) or not expected_names["ips"].issubset(actual_ips):
        fail("A local HTTPS certificate is missing a required DNS or IP subject name.")


def ensure_ca(directory):
    cert_path = directory / "ca-v2.crt"
    key_path = directory / "ca-v2.key"
    if cert_path.exists() != key_path.exists():
        fail("The local certificate authority files are incomplete. Restore both files or rotate the TLS Secret version.")
    if cert_path.exists():
        secure_regular_file(cert_path, 0o644, "certificate authority certificate")
        secure_regular_file(key_path, 0o600, "certificate authority private key")
        assert_pair(cert_path, key_path, "certificate authority")
        openssl(["x509", "-in", str(cert_path), "-noout", "-checkend", str(365 * 24 * 60 * 60)])
        return

    with tempfile.TemporaryDirectory(prefix="takehome-ca-", dir=directory) as temporary:
        os.chmod(temporary, 0o700)
        temporary_path = Path(temporary)
        generated_key = temporary_path / "ca.key"
        generated_cert = temporary_path / "ca-v2.crt"
        openssl(["ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(generated_key)])
        openssl([
            "req", "-x509", "-new", "-key", str(generated_key), "-out", str(generated_cert), "-days", str(3650),
            "-sha256", "-subj", "/CN=Takehome Local Development Root",
            "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign",
            "-addext", "subjectKeyIdentifier=hash",
        ])
        os.chmod(generated_key, 0o600)
        os.replace(generated_key, key_path)
        os.replace(generated_cert, cert_path)
        os.chmod(key_path, 0o600)
        os.chmod(cert_path, 0o644)


def ensure_leaf(directory, name, dns_names, ip_names):
    cert_path = directory / f"{name}.crt"
    key_path = directory / f"{name}.key"
    names = {"dns": set(dns_names), "ips": set(ip_names)}
    if cert_path.exists() and key_path.exists():
        try:
            secure_regular_file(cert_path, 0o644, f"{name} certificate")
            secure_regular_file(key_path, 0o600, f"{name} private key")
            assert_pair(cert_path, key_path, name)
            assert_names(cert_path, names)
        except SystemExit:
            fail(f"The local {name} TLS files are invalid. Rotate to a new immutable Secret version before retrying.")
        openssl(["x509", "-in", str(cert_path), "-noout", "-checkend", "0"])
        nearing_expiry = subprocess.run(
            ["openssl", "x509", "-in", str(cert_path), "-noout", "-checkend", str(30 * 24 * 60 * 60)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode != 0
        if nearing_expiry:
            print(f"Local {name} certificate expires within 30 days; rotate its immutable TLS Secret version soon.", file=sys.stderr)
        return
    elif cert_path.exists() or key_path.exists():
        fail(f"The local {name} TLS files are incomplete. Restore both files or rotate to a new immutable Secret version.")

    ca_cert = directory / "ca-v2.crt"
    ca_key = directory / "ca-v2.key"
    with tempfile.TemporaryDirectory(prefix=f"takehome-{name}-", dir=directory) as temporary:
        os.chmod(temporary, 0o700)
        temporary_path = Path(temporary)
        generated_key = temporary_path / f"{name}.key"
        request = temporary_path / f"{name}.csr"
        generated_cert = temporary_path / f"{name}.crt"
        config_path = temporary_path / "extensions.cnf"
        alt_names = []
        alt_names.extend(f"DNS.{index}={dns}" for index, dns in enumerate(sorted(dns_names), start=1))
        alt_names.extend(f"IP.{index}={address}" for index, address in enumerate(sorted(ip_names), start=1))
        config_path.write_text(
            "[server_ext]\n"
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature\n"
            "extendedKeyUsage=serverAuth\n"
            "subjectKeyIdentifier=hash\n"
            "authorityKeyIdentifier=keyid,issuer\n"
            "subjectAltName=@alt_names\n"
            "[alt_names]\n"
            + "\n".join(alt_names)
            + "\n",
            encoding="utf-8",
        )
        openssl(["ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(generated_key)])
        openssl(["req", "-new", "-key", str(generated_key), "-out", str(request), "-subj", f"/CN={name}"])
        openssl([
            "x509", "-req", "-in", str(request), "-CA", str(ca_cert), "-CAkey", str(ca_key),
            "-CAcreateserial", "-out", str(generated_cert), "-days", "365", "-sha256",
            "-extfile", str(config_path), "-extensions", "server_ext",
        ])
        os.chmod(generated_key, 0o600)
        os.replace(generated_key, key_path)
        os.replace(generated_cert, cert_path)
        os.chmod(key_path, 0o600)
        os.chmod(cert_path, 0o644)
    assert_pair(cert_path, key_path, name)
    assert_names(cert_path, names)


def ensure():
    os.umask(0o077)
    directory = tls_directory()
    ensure_ca(directory)
    # Keep the old localhost SAN for compatibility while giving the local
    # dashboard a stable, loopback-only hostname and a separately versioned Secret.
    ensure_leaf(directory, "web-v3", {"localhost", "takehome.localhost"}, {"127.0.0.1"})
    ensure_leaf(directory, "api-v2", {"localhost", "snapshot-api.demo.svc.cluster.local"}, {"127.0.0.1"})
    return directory


def run_kubectl(arguments, *, input_bytes=None):
    return subprocess.run(
        ["kubectl", "--context", CONTEXT, *arguments],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def provision(directory):
    for name, file_map in SECRET_FILES.items():
        payload = {key: (directory / filename).read_bytes() for key, filename in file_map.items()}
        current = run_kubectl(["-n", NAMESPACE, "get", "secret", name, "-o", "json"])
        if current.returncode == 0:
            try:
                secret = json.loads(current.stdout)
                current_data = {
                    key: base64.b64decode(secret.get("data", {}).get(key, ""), validate=True)
                    for key in payload
                }
            except (json.JSONDecodeError, ValueError, TypeError):
                fail(f"Installed TLS Secret {name} is invalid. Rotate it to a new version before retrying.")
            if secret.get("immutable") is not True or secret.get("type") != "kubernetes.io/tls" or any(
                not hmac.compare_digest(current_data[key], value) for key, value in payload.items()
            ):
                fail(f"Installed TLS Secret {name} does not match the protected local certificate set. Rotate it to a new version.")
            continue

        document = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": name, "namespace": NAMESPACE},
            "immutable": True,
            "type": "kubernetes.io/tls",
            "data": {key: base64.b64encode(value).decode("ascii") for key, value in payload.items()},
        }
        applied = run_kubectl(
            ["-n", NAMESPACE, "apply", "-f", "-"],
            input_bytes=json.dumps(document, separators=(",", ":")).encode("utf-8"),
        )
        if applied.returncode != 0:
            fail(f"Could not create immutable TLS Secret {name}. Check Kubernetes access and retry.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("ensure", "provision"))
    args = parser.parse_args()
    directory = ensure()
    if args.command == "provision":
        provision(directory)
        print("Immutable local HTTPS Secrets are installed.")
    else:
        print(f"Local HTTPS certificates are ready in {directory}.")
        print(f"Root CA certificate: {directory / 'ca-v2.crt'}")


if __name__ == "__main__":
    main()

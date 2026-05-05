#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_CA_CONFIG="$ROOT_DIR/deploy/certs/vava_local_root_ca.cnf"
CERT_CONFIG="$ROOT_DIR/deploy/certs/vava_local_multi_san.cnf"
CERT_DIR="$ROOT_DIR/legacy"
ROOT_CA_CERT="$CERT_DIR/sunvalley_local_root_ca.crt"
ROOT_CA_KEY="$CERT_DIR/sunvalley_local_root_ca.key"
CERT_FILE="$CERT_DIR/sunvalley_multi_san.crt"
KEY_FILE="$CERT_DIR/sunvalley_multi_san.key"
FULLCHAIN_FILE="$CERT_DIR/sunvalley_multi_san_fullchain.crt"
CSR_FILE="$CERT_DIR/sunvalley_multi_san.csr"
SERIAL_FILE="$CERT_DIR/sunvalley_local_root_ca.srl"
BACKUP_DIR="$CERT_DIR/cert_backups"
STAMP="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$BACKUP_DIR"

for maybe_file in \
    "$ROOT_CA_CERT" \
    "$ROOT_CA_KEY" \
    "$CERT_FILE" \
    "$KEY_FILE" \
    "$FULLCHAIN_FILE" \
    "$SERIAL_FILE"
do
    if [[ -f "$maybe_file" ]]; then
        cp "$maybe_file" "$BACKUP_DIR/$(basename "$maybe_file").$STAMP"
    fi
done

if [[ ! -f "$ROOT_CA_CERT" || ! -f "$ROOT_CA_KEY" ]]; then
    openssl req \
        -x509 \
        -nodes \
        -newkey rsa:2048 \
        -sha256 \
        -days 3650 \
        -keyout "$ROOT_CA_KEY" \
        -out "$ROOT_CA_CERT" \
        -config "$ROOT_CA_CONFIG"
fi

openssl req \
    -new \
    -nodes \
    -newkey rsa:2048 \
    -keyout "$KEY_FILE" \
    -out "$CSR_FILE" \
    -config "$CERT_CONFIG"

openssl x509 \
    -req \
    -in "$CSR_FILE" \
    -CA "$ROOT_CA_CERT" \
    -CAkey "$ROOT_CA_KEY" \
    -CAcreateserial \
    -CAserial "$SERIAL_FILE" \
    -out "$CERT_FILE" \
    -days 825 \
    -sha256 \
    -extfile "$CERT_CONFIG" \
    -extensions v3_server

cat "$CERT_FILE" "$ROOT_CA_CERT" > "$FULLCHAIN_FILE"
rm -f "$CSR_FILE"
chmod 600 "$ROOT_CA_KEY" "$KEY_FILE"

echo "== Root CA =="
openssl x509 -in "$ROOT_CA_CERT" -noout -subject -issuer
echo "== Server Certificate =="
openssl x509 -in "$CERT_FILE" -noout -subject -issuer -ext basicConstraints -ext extendedKeyUsage -ext subjectAltName

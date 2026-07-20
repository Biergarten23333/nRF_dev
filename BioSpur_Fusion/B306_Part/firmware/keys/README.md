# B306 MCUboot signing identity

The tracked public key is `b306_mcuboot_ec_p256.pub.pem`.

The private ECDSA-P256 key is intentionally outside the repository:

```text
/home/zekaixiao/.config/biospur/keys/b306_mcuboot_ec_p256.pem
```

Its mode is `0600`. The SHA-256 fingerprint of its DER-encoded
SubjectPublicKeyInfo is:

```text
0e525dedaa7f50fb38d3c8f1792cacaa20f70204aa46ef6b50d720479c6ef5a2
```

Do not replace this key after the first B306 flash. Before that handover, make a
protected backup and verify that the backup produces the same fingerprint:

```bash
openssl pkey -in <private-key.pem> -pubout -outform DER | sha256sum
```

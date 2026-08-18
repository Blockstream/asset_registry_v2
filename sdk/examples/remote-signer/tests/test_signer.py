from app.signer import _btl_encode, _bitcoin_message_magic, sign

# Test key: deterministic private key for testing (NOT for production)
_TEST_PRIVKEY = bytes.fromhex(
    "0000000000000000000000000000000000000000000000000000000000000001"
)


# --- BTL encoding tests ---


class TestBtlEncode:
    def test_short_length(self):
        assert _btl_encode(0) == bytes([0])
        assert _btl_encode(10) == bytes([10])
        assert _btl_encode(251) == bytes([251])

    def test_two_byte_length(self):
        encoded = _btl_encode(252)
        assert encoded == bytes([0xFD, 252, 0])

        encoded = _btl_encode(65535)
        assert encoded == bytes([0xFD, 0xFF, 0xFF])

    def test_four_byte_length(self):
        encoded = _btl_encode(65536)
        assert encoded == bytes([0xFE, 0, 0, 1, 0])


# --- Bitcoin message magic tests ---


class TestBitcoinMessageMagic:
    def test_short_message(self):
        result = _bitcoin_message_magic(b"Hello")
        expected = b"\x18Bitcoin Signed Message:\n\x05Hello"
        assert result == expected

    def test_empty_message(self):
        result = _bitcoin_message_magic(b"")
        expected = b"\x18Bitcoin Signed Message:\n\x00"
        assert result == expected

    def test_long_message(self):
        msg = b"a" * 300
        result = _bitcoin_message_magic(msg)
        expected = b"\x18Bitcoin Signed Message:\n\xFD\x2C\x01" + msg
        assert result == expected

    def test_unicode_message(self):
        msg = "Héllo".encode("utf-8")
        result = _bitcoin_message_magic(msg)
        assert result.startswith(b"\x18Bitcoin Signed Message:\n")
        assert result.endswith(msg)


# --- Sign integration tests ---


class TestSign:
    def test_sign_simple_message(self):
        result = sign("Hello, World!", _TEST_PRIVKEY)
        assert len(result) == 130  # 2 (rec_id) + 128 (64 bytes * 2 hex chars)
        assert result.startswith("1")  # rec_id 27 = 0x1B → "1..."

    def test_sign_empty_message(self):
        result = sign("", _TEST_PRIVKEY)
        assert len(result) == 130

    def test_sign_unicode(self):
        result = sign("你好世界", _TEST_PRIVKEY)
        assert len(result) == 130

    def test_sign_long_message(self):
        result = sign("a" * 10000, _TEST_PRIVKEY)
        assert len(result) == 130

    def test_non_deterministic(self):
        # ECDSA uses random nonces, so two signs of the same message differ
        sig1 = sign("test", _TEST_PRIVKEY)
        sig2 = sign("test", _TEST_PRIVKEY)
        assert sig1 != sig2

    def test_same_key_different_messages(self):
        sig1 = sign("message A", _TEST_PRIVKEY)
        sig2 = sign("message B", _TEST_PRIVKEY)
        assert sig1 != sig2

    def test_signature_is_valid_hex(self):
        result = sign("test", _TEST_PRIVKEY)
        int(result, 16)  # should not raise

"""
Test encryption/decryption to verify ENCRYPTION_KEY is working.

Usage:
    cd server
    uv run python tests/test_encryption.py
"""

import base64
import os
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()


def test_encryption():
    print("=" * 60)
    print("Encryption/Decryption Test")
    print("=" * 60)

    # Check if key is set
    key_str = os.environ.get("ENCRYPTION_KEY")
    if not key_str:
        print("✗ ENCRYPTION_KEY not set in environment")
        return False

    print("\n1. ENCRYPTION_KEY found")
    print(f"   Length: {len(key_str)} chars")
    print(f"   Starts with: {key_str[:8]}...")
    print(f"   Ends with: ...{key_str[-8:]}")

    # Check if it's base64
    print("\n2. Checking if key is valid base64...")
    try:
        decoded = base64.b64decode(key_str)
        print(f"   ✓ Valid base64, decoded length: {len(decoded)} bytes")
        if len(decoded) == 32:
            print("   ✓ Perfect! 32 bytes = 256-bit key")
        else:
            print(f"   ⚠ Expected 32 bytes for AES-256, got {len(decoded)}")
            print("   Will fall back to SHA-256 hash of key string")
    except Exception as e:
        print(f"   ⚠ Not valid base64: {e}")
        print("   Will use SHA-256 hash of key string")

    # Test encryption service
    print("\n3. Testing encryption service...")
    from app.services.encryption import decrypt_secret, encrypt_secret, get_encryption_key

    # Get the derived key
    derived_key = get_encryption_key()
    print(f"   Derived key length: {len(derived_key)} bytes")
    print(f"   Derived key (hex): {derived_key.hex()[:32]}...")

    # Test encrypt/decrypt
    test_value = "sk-test-12345-this-is-a-test-api-key"
    print("\n4. Testing round-trip encryption...")
    print(f"   Original: {test_value}")

    try:
        encrypted = encrypt_secret(test_value)
        print(f"   Encrypted: {len(encrypted)} bytes")
        print(f"   Encrypted (hex): {encrypted.hex()[:64]}...")

        decrypted = decrypt_secret(encrypted)
        print(f"   Decrypted: {decrypted}")

        if decrypted == test_value:
            print("\n   ✓ SUCCESS: Encryption/decryption works correctly!")
            return True
        else:
            print("\n   ✗ MISMATCH: Decrypted value doesn't match original")
            return False
    except Exception as e:
        print(f"\n   ✗ ERROR: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_db_secret():
    """Test encrypting, storing in DB, retrieving and decrypting."""
    import asyncio
    import uuid

    import asyncpg

    async def run():
        print("\n" + "=" * 60)
        print("Database Round-Trip Test")
        print("=" * 60)

        dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_DSN")
        if not dsn:
            print("✗ DATABASE_URL or DATABASE_DSN not set, skipping DB test")
            return

        from app.services.encryption import decrypt_secret, encrypt_secret

        conn = await asyncpg.connect(dsn)

        try:
            # Get a project ID
            project_row = await conn.fetchrow("SELECT id FROM projects LIMIT 1")
            if not project_row:
                print("✗ No projects found")
                return

            project_id = project_row["id"]
            test_secret_name = f"test_encryption_{uuid.uuid4().hex[:8]}"
            test_value = "my-super-secret-api-key-12345"

            print(f"\n1. Creating test secret: {test_secret_name}")
            print(f"   Value: {test_value}")

            # Encrypt and store
            encrypted = encrypt_secret(test_value)
            print(f"   Encrypted length: {len(encrypted)} bytes")

            await conn.execute(
                """
                INSERT INTO project_secrets (project_id, secret_name, encrypted_value, description)
                VALUES ($1, $2, $3, $4)
                """,
                project_id,
                test_secret_name,
                encrypted,
                "Test encryption",
            )
            print("   ✓ Stored in database")

            # Retrieve and decrypt
            print("\n2. Retrieving from database...")
            row = await conn.fetchrow(
                "SELECT encrypted_value FROM project_secrets WHERE project_id = $1 AND secret_name = $2",
                project_id,
                test_secret_name,
            )

            if not row:
                print("   ✗ Secret not found!")
                return

            retrieved_encrypted = row["encrypted_value"]
            print(f"   Retrieved {len(retrieved_encrypted)} bytes")

            # Check if bytes match
            if retrieved_encrypted == encrypted:
                print("   ✓ Encrypted bytes match original")
            else:
                print("   ✗ Encrypted bytes don't match!")
                print(f"   Original: {encrypted.hex()[:64]}...")
                print(f"   Retrieved: {retrieved_encrypted.hex()[:64]}...")

            # Decrypt
            print("\n3. Decrypting...")
            decrypted = decrypt_secret(retrieved_encrypted)
            print(f"   Decrypted: {decrypted}")

            if decrypted == test_value:
                print("\n   ✓ SUCCESS: Full round-trip works!")
            else:
                print("\n   ✗ MISMATCH")

            # Cleanup
            await conn.execute(
                "DELETE FROM project_secrets WHERE project_id = $1 AND secret_name = $2",
                project_id,
                test_secret_name,
            )
            print("\n4. Cleaned up test secret")

        finally:
            await conn.close()

    asyncio.run(run())


if __name__ == "__main__":
    success = test_encryption()
    if success:
        test_db_secret()

    print("\n" + "=" * 60)
    print("IMPORTANT: If this works locally but Modal fails,")
    print("check that ENCRYPTION_KEY in Modal secrets matches")
    print("your local .env exactly (same value, same encoding).")
    print("=" * 60)

    # Show the key info for Modal debugging
    key_str = os.environ.get("ENCRYPTION_KEY", "")
    print("\n--- Key Details for Modal Verification ---")
    print(f"ENCRYPTION_KEY value: {key_str}")
    print(f"Key length: {len(key_str)} chars")
    print("\nMake sure this EXACT value is in Modal secret 'em-service-secrets'")
    print("under the key ENCRYPTION_KEY (no quotes, no extra whitespace).")

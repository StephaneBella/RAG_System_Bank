import secrets


def generate_secret(num_bytes: int = 32) -> str:
    """Return a cryptographically strong random secret as a hex string."""
    return secrets.token_hex(num_bytes)


if __name__ == "__main__":
    print(generate_secret())

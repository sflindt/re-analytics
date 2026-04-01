"""Simple email-gated access using Supabase.

Requires SUPABASE_URL and SUPABASE_KEY in .env.

Supabase table setup (run once in Supabase SQL Editor):

    CREATE TABLE access_requests (
        id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
        email text UNIQUE NOT NULL,
        name text NOT NULL,
        status text DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'denied')),
        created_at timestamptz DEFAULT now(),
        approved_at timestamptz
    );

    -- Optional: enable Row Level Security
    ALTER TABLE access_requests ENABLE ROW LEVEL SECURITY;

    -- Allow inserts from anon (signup)
    CREATE POLICY "Allow anonymous inserts"
        ON access_requests FOR INSERT
        TO anon WITH CHECK (true);

    -- Allow reads from anon (login check)
    CREATE POLICY "Allow anonymous reads"
        ON access_requests FOR SELECT
        TO anon USING (true);
"""

from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)

_supabase_client = None


def _get_client():
    """Lazy-init Supabase client."""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        return None

    try:
        from supabase import create_client
        _supabase_client = create_client(url, key)
        return _supabase_client
    except ImportError:
        logger.warning("supabase package not installed. pip install supabase")
        return None
    except Exception as e:
        logger.warning(f"Supabase init failed: {e}")
        return None


def is_auth_enabled() -> bool:
    """Check if Supabase auth is configured."""
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"))


def request_access(email: str, name: str) -> str:
    """Submit an access request. Returns 'submitted', 'already_pending', 'approved', or 'error'."""
    client = _get_client()
    if not client:
        return "error"

    email = email.strip().lower()
    name = name.strip()

    try:
        # Check if already exists
        existing = (
            client.table("access_requests")
            .select("status")
            .eq("email", email)
            .execute()
        )
        if existing.data:
            status = existing.data[0]["status"]
            if status == "approved":
                return "approved"
            elif status == "pending":
                return "already_pending"
            elif status == "denied":
                return "denied"

        # Insert new request
        client.table("access_requests").insert({
            "email": email,
            "name": name,
            "status": "pending",
        }).execute()
        return "submitted"

    except Exception as e:
        # Handle unique constraint violation (race condition)
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            return "already_pending"
        logger.warning(f"Access request failed: {e}")
        return "error"


def check_access(email: str) -> str:
    """Check access status for an email. Returns 'approved', 'pending', 'denied', or 'not_found'."""
    client = _get_client()
    if not client:
        return "not_found"

    email = email.strip().lower()

    try:
        result = (
            client.table("access_requests")
            .select("status")
            .eq("email", email)
            .execute()
        )
        if result.data:
            return result.data[0]["status"]
        return "not_found"
    except Exception as e:
        logger.warning(f"Access check failed: {e}")
        return "not_found"

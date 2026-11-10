#!/usr/bin/env python3
"""
Check a Neo4j connection using the same calls KnowledgeGraph makes.

Usage:
    python scripts/check_neo4j.py
    # or with overrides:
    NEO4J_PASSWORD=mypass python scripts/check_neo4j.py
"""
from __future__ import annotations

import asyncio
import os
import sys

from neo4j import AsyncGraphDatabase
from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable


URI = os.getenv("NEO4J_URL", "bolt://localhost:7687")
USER = os.getenv("NEO4J_USER", "neo4j")
PASSWORD = os.getenv("NEO4J_PASSWORD", "")


async def main() -> int:
    print(f"URI:  {URI}")
    print(f"USER: {USER}")
    print(f"PASS: {'*' * len(PASSWORD) if PASSWORD else '(empty)'}")
    print()

    if not PASSWORD:
        print("❌ NEO4J_PASSWORD is empty")
        return 2

    driver = AsyncGraphDatabase.driver(URI, auth=(USER, PASSWORD))
    try:
        # 1. Connectivity — opens a TCP connection and does the Bolt
        #    handshake. Does NOT authenticate.
        print("→ verify_connectivity() ...")
        try:
            await driver.verify_connectivity()
            print("  ✅ TCP + Bolt handshake OK")
        except ServiceUnavailable as exc:
            print(f"  ❌ cannot reach server: {exc}")
            print("     Is Neo4j running?  docker ps | grep neo4j")
            print("     Is it on the expected port?  7687 is Bolt.")
            return 1
        except Exception as exc:
            print(f"  ❌ {type(exc).__name__}: {exc}")
            return 1

        # 2. Authentication + query round-trip
        print("→ running query ...")
        try:
            async with driver.session() as session:
                result = await session.run(
                    "RETURN 1 AS ok, "
                    "       apoc.version() AS apoc "   # harmless if APOC missing
                    if False else                       # skip APOC — not required
                    "RETURN 1 AS ok"
                )
                record = await result.single()
            print(f"  ✅ authenticated — query returned {record['ok']}")
        except AuthError as exc:
            print(f"  ❌ authentication failed: {exc}")
            print()
            print("  Neo4j's default password `neo4j` is one-time-only.")
            print("  If you never changed it, the factory default still works:")
            print("      docker exec -it neo4j cypher-shell -u neo4j -p neo4j")
            print("  Otherwise reset it:")
            print("      docker exec -it neo4j cypher-shell -u neo4j -p <old> \\")
            print("        \"ALTER USER neo4j SET PASSWORD 'neo4j123'\"")
            return 1
        except Neo4jError as exc:
            print(f"  ❌ {exc.code}: {exc.message}")
            return 1

        # 3. Try the constraints KnowledgeGraph creates
        print("→ creating constraints (what KnowledgeGraph.initialize does) ...")
        try:
            async with driver.session() as session:
                for stmt in (
                    "CREATE CONSTRAINT user_id_unique IF NOT EXISTS "
                    "FOR (u:User) REQUIRE u.id IS UNIQUE",
                    "CREATE CONSTRAINT device_id_unique IF NOT EXISTS "
                    "FOR (d:Device) REQUIRE d.id IS UNIQUE",
                    "CREATE CONSTRAINT plugin_id_unique IF NOT EXISTS "
                    "FOR (p:Plugin) REQUIRE p.id IS UNIQUE",
                ):
                    await session.run(stmt)
            print("  ✅ constraints created (or already present)")
        except Neo4jError as exc:
            print(f"  ❌ constraint creation failed: {exc.code} {exc.message}")
            # Not fatal — the graph still works without them, just slower.
            # KnowledgeGraph.initialize() treats this as a hard failure
            # though, so worth noting.
            print("     (This is what makes KnowledgeGraph go unavailable.)")
            return 1

        # 4. Clean up what we created so repeated runs are idempotent
        print("→ verifying data round-trip ...")
        try:
            async with driver.session() as session:
                await session.run(
                    "MERGE (t:__Check {id: 'smoke'}) "
                    "SET t.ts = timestamp()"
                )
                result = await session.run(
                    "MATCH (t:__Check {id: 'smoke'}) RETURN t.ts AS ts"
                )
                rec = await result.single()
                await session.run(
                    "MATCH (t:__Check {id: 'smoke'}) DELETE t"
                )
            print(f"  ✅ wrote + read + deleted (ts={rec['ts']})")
        except Neo4jError as exc:
            print(f"  ❌ round-trip failed: {exc}")
            return 1

        print()
        print("✅ All checks passed — credentials are valid.")
        return 0

    finally:
        await driver.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
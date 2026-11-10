from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from loguru import logger
from neo4j import AsyncGraphDatabase
from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable

from pai.observability.tracer import get_tracer


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class KnowledgeGraph:
    """Neo4j-backed entity and relationship graph.

    Optional — the system runs fine without it. `_available` reflects
    whether the driver is connected and authenticated.
    """

    def __init__(self, uri: str, user: str, password: str):
        self.uri = uri
        self.user = user
        self.password = password
        self.driver = None
        self._available = False
        self._auth_warned = False          # log the auth failure only once

    @staticmethod
    def _validate_identifier(value: str, field: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError(f"Invalid Neo4j {field}: {value!r}")
        return value

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "knowledge_graph.initialize",
            uri=self.uri,
        ) as span:
            try:
                self.driver = AsyncGraphDatabase.driver(
                    self.uri,
                    auth=(self.user, self.password),
                )
                await self.driver.verify_connectivity()

                async with self.driver.session() as session:
                    await session.run(
                        """
                        CREATE CONSTRAINT user_id_unique IF NOT EXISTS
                        FOR (u:User) REQUIRE u.id IS UNIQUE
                        """
                    )
                    await session.run(
                        """
                        CREATE CONSTRAINT device_id_unique IF NOT EXISTS
                        FOR (d:Device) REQUIRE d.id IS UNIQUE
                        """
                    )
                    await session.run(
                        """
                        CREATE CONSTRAINT plugin_id_unique IF NOT EXISTS
                        FOR (p:Plugin) REQUIRE p.id IS UNIQUE
                        """
                    )

                self._available = True
                span.set_attribute("available", True)
                logger.info("KnowledgeGraph initialized")

            except (AuthError, Neo4jError, ServiceUnavailable, OSError) as exc:
                # Cleanup without logging "closed" — the graph never opened.
                await self._close_silently()

                span.set_attribute("available", False)
                span.set_attribute(
                    "unavailable_reason",
                    f"{type(exc).__name__}: {str(exc)[:500]}",
                )

                # Classify the failure to give an actionable message.
                msg = str(exc)
                if isinstance(exc, AuthError) or "unauthorized" in msg.lower():
                    self._log_auth_failure()
                else:
                    logger.warning(
                        "KnowledgeGraph unavailable at {}: {}. "
                        "Continuing without it (optional feature).",
                        self.uri, exc,
                    )

    def _log_auth_failure(self) -> None:
        """Log the auth failure once, with copy-pasteable remediation."""
        if self._auth_warned:
            return
        self._auth_warned = True
        logger.warning(
            "KnowledgeGraph disabled: Neo4j authentication failed.\n"
            "  uri:  {}\n"
            "  user: {}\n"
            "\n"
            "  Neo4j's initial password (`neo4j`) is one-time-only —\n"
            "  it must be changed on first login, so `neo4j/neo4j`\n"
            "  fails after the first successful connect.\n"
            "\n"
            "  Fix one of:\n"
            "    1. Set the real password in .env:\n"
            "         NEO4J_PASSWORD=<your-password>\n"
            "\n"
            "    2. Reset the password (Docker):\n"
            "         docker exec -it neo4j cypher-shell \\\n"
            "           -u neo4j -p <old-password> \\\n"
            "           \"ALTER USER neo4j SET PASSWORD '<new-password>'\"\n"
            "\n"
            "    3. Disable the knowledge graph if you don't need it:\n"
            "         ENABLE_KNOWLEDGE_GRAPH=false\n"
            "\n"
            "  Knowledge graph is optional — all other memory backends\n"
            "  (short-term, long-term, episodic, vector) work without it.",
            self.uri, self.user,
        )

    async def _close_silently(self) -> None:
        """Internal cleanup that never logs. Used when init fails before
        the graph was ever usable, and by `close()` itself."""
        if self.driver:
            try:
                await self.driver.close()
            except Exception:
                pass
        self.driver = None
        self._available = False

    async def close(self) -> None:
        """Public shutdown — logs at INFO only if the graph was open."""
        was_available = self._available
        await self._close_silently()
        if was_available:
            logger.info("KnowledgeGraph closed")

    # ------------------------------------------------------------------
    # Reads / writes — unchanged
    # ------------------------------------------------------------------

    async def add_entity(self, entity_type: str, entity_id: str,
                         properties: Optional[Dict[str, Any]] = None) -> bool:
        tracer = get_tracer()
        async with tracer.span(
            "knowledge_graph.add_entity",
            entity_type=entity_type, entity_id=entity_id,
            available=self._available,
        ) as span:
            if not self._available:
                span.set_attribute("skipped", "unavailable")
                return False
            entity_type = self._validate_identifier(entity_type, "entity type")
            query = f"""
            MERGE (e:{entity_type} {{id: $id}})
            SET e += $properties
            """
            try:
                async with self.driver.session() as session:
                    await session.run(query, id=entity_id,
                                      properties=properties or {})
                span.set_attribute("success", True)
                return True
            except Neo4jError as exc:
                logger.error("Failed to add entity: {}", exc)
                span.set_error(f"Neo4jError: {exc}")
                return False

    async def add_relationship(
        self, subject_type: str, subject_id: str, relation: str,
        object_type: str, object_id: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> bool:
        tracer = get_tracer()
        async with tracer.span(
            "knowledge_graph.add_relationship",
            subject_type=subject_type, relation=relation,
            object_type=object_type, available=self._available,
        ) as span:
            if not self._available:
                span.set_attribute("skipped", "unavailable")
                return False
            subject_type = self._validate_identifier(subject_type, "subject type")
            object_type = self._validate_identifier(object_type, "object type")
            relation = self._validate_identifier(relation, "relationship")
            query = f"""
            MATCH (a:{subject_type} {{id: $subject_id}})
            MATCH (b:{object_type} {{id: $object_id}})
            MERGE (a)-[r:{relation}]->(b)
            SET r += $properties
            """
            try:
                async with self.driver.session() as session:
                    await session.run(query, subject_id=subject_id,
                                      object_id=object_id,
                                      properties=properties or {})
                span.set_attribute("success", True)
                return True
            except Neo4jError as exc:
                logger.error("Failed to add relationship: {}", exc)
                span.set_error(f"Neo4jError: {exc}")
                return False

    async def query(self, cypher: str,
                    params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        tracer = get_tracer()
        async with tracer.span(
            "knowledge_graph.query",
            cypher_length=len(cypher), param_count=len(params or {}),
            available=self._available,
        ) as span:
            if not self._available:
                span.set_attribute("skipped", "unavailable")
                return []
            try:
                async with self.driver.session() as session:
                    result = await session.run(cypher, params or {})
                    rows = [record.data() async for record in result]
                span.set_attribute("result_count", len(rows))
                return rows
            except Neo4jError as exc:
                logger.error("Knowledge graph query failed: {}", exc)
                span.set_error(f"Neo4jError: {exc}")
                return []

    async def get_user_graph(self, user_id: str, *, limit: int = 100) -> List[Dict[str, Any]]:
        return await self.query(
            """
            MATCH (u:User {id: $user_id})-[r]->(n)
            RETURN type(r) AS relation,
                   labels(n) AS labels,
                   n.id AS entity_id,
                   properties(n) AS properties
            LIMIT $limit
            """,
            {"user_id": user_id, "limit": limit},
        )
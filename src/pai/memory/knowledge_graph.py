from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from loguru import logger
from neo4j import AsyncGraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable


_IDENTIFIER = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*$"
)


class KnowledgeGraph:
    """
    Neo4j-backed entity and relationship graph.
    """

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
    ):
        self.uri = uri
        self.user = user
        self.password = password

        self.driver = None
        self._available = False

    @staticmethod
    def _validate_identifier(
        value: str,
        field: str,
    ) -> str:

        if not _IDENTIFIER.fullmatch(value):
            raise ValueError(
                f"Invalid Neo4j {field}: {value!r}"
            )

        return value

    async def initialize(self) -> None:
        try:
            self.driver = AsyncGraphDatabase.driver(
                self.uri,
                auth=(
                    self.user,
                    self.password,
                ),
            )

            await self.driver.verify_connectivity()

            async with self.driver.session() as session:

                await session.run(
                    """
                    CREATE CONSTRAINT user_id_unique IF NOT EXISTS
                    FOR (u:User)
                    REQUIRE u.id IS UNIQUE
                    """
                )

                await session.run(
                    """
                    CREATE CONSTRAINT device_id_unique IF NOT EXISTS
                    FOR (d:Device)
                    REQUIRE d.id IS UNIQUE
                    """
                )

                await session.run(
                    """
                    CREATE CONSTRAINT plugin_id_unique IF NOT EXISTS
                    FOR (p:Plugin)
                    REQUIRE p.id IS UNIQUE
                    """
                )

            self._available = True

            logger.info(
                "KnowledgeGraph initialized"
            )

        except (
            Neo4jError,
            ServiceUnavailable,
            OSError,
        ) as exc:

            await self.close()

            logger.warning(
                "KnowledgeGraph unavailable: {}",
                exc,
            )

    async def add_entity(
        self,
        entity_type: str,
        entity_id: str,
        properties: Optional[
            Dict[str, Any]
        ] = None,
    ) -> bool:

        if not self._available:
            return False

        entity_type = self._validate_identifier(
            entity_type,
            "entity type",
        )

        query = f"""
        MERGE (e:{entity_type} {{id: $id}})
        SET e += $properties
        """

        try:
            async with self.driver.session() as session:
                await session.run(
                    query,
                    id=entity_id,
                    properties=properties or {},
                )

            return True

        except Neo4jError as exc:
            logger.error(
                "Failed to add entity: {}",
                exc,
            )

            return False

    async def add_relationship(
        self,
        subject_type: str,
        subject_id: str,
        relation: str,
        object_type: str,
        object_id: str,
        properties: Optional[
            Dict[str, Any]
        ] = None,
    ) -> bool:

        if not self._available:
            return False

        subject_type = self._validate_identifier(
            subject_type,
            "subject type",
        )

        object_type = self._validate_identifier(
            object_type,
            "object type",
        )

        relation = self._validate_identifier(
            relation,
            "relationship",
        )

        query = f"""
        MATCH (a:{subject_type} {{id: $subject_id}})
        MATCH (b:{object_type} {{id: $object_id}})
        MERGE (a)-[r:{relation}]->(b)
        SET r += $properties
        """

        try:
            async with self.driver.session() as session:
                await session.run(
                    query,
                    subject_id=subject_id,
                    object_id=object_id,
                    properties=properties or {},
                )

            return True

        except Neo4jError as exc:
            logger.error(
                "Failed to add relationship: {}",
                exc,
            )

            return False

    async def query(
        self,
        cypher: str,
        params: Optional[
            Dict[str, Any]
        ] = None,
    ) -> List[Dict[str, Any]]:

        if not self._available:
            return []

        try:
            async with self.driver.session() as session:
                result = await session.run(
                    cypher,
                    params or {},
                )

                return [
                    record.data()
                    async for record in result
                ]

        except Neo4jError as exc:
            logger.error(
                "Knowledge graph query failed: {}",
                exc,
            )

            return []

    async def get_user_graph(
        self,
        user_id: str,
        *,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:

        return await self.query(
            """
            MATCH (u:User {id: $user_id})-[r]->(n)
            RETURN
                type(r) AS relation,
                labels(n) AS labels,
                n.id AS entity_id,
                properties(n) AS properties
            LIMIT $limit
            """,
            {
                "user_id": user_id,
                "limit": limit,
            },
        )

    async def close(self) -> None:
        if self.driver:
            try:
                await self.driver.close()
            except Exception:
                pass

        self.driver = None
        self._available = False

        logger.info(
            "KnowledgeGraph closed"
        )
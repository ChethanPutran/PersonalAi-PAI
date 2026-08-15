from typing import (
    Dict,
    Any,
    List,
    Optional,
)

from neo4j import AsyncGraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from loguru import logger


class KnowledgeGraph:
    """
    Neo4j async knowledge graph manager.
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

    async def initialize(self) -> None:
        """
        Initialize Neo4j driver and constraints.
        """
        print(f"Initializing KnowledgeGraph with URI: {self.uri}, User: {self.user}, Password: {self.password}")
        try:
            self.driver = (
                AsyncGraphDatabase.driver(
                    self.uri,
                    auth=(
                        self.user,
                        self.password,
                    ),
                )
            )

            async with self.driver.session() as session:
                await session.run(
                    """
                    CREATE CONSTRAINT IF NOT EXISTS
                    FOR (u:User)
                    REQUIRE u.id IS UNIQUE
                    """
                )

                await session.run(
                    """
                    CREATE CONSTRAINT IF NOT EXISTS
                    FOR (d:Device)
                    REQUIRE d.id IS UNIQUE
                    """
                )

                await session.run(
                    """
                    CREATE CONSTRAINT IF NOT EXISTS
                    FOR (p:Plugin)
                    REQUIRE p.name IS UNIQUE
                    """
                )

            logger.info(
                "KnowledgeGraph initialized"
            )

        except (Neo4jError, ServiceUnavailable, OSError) as e:
            if self.driver:
                await self.driver.close()
            self.driver = None
            logger.warning(
                f"KnowledgeGraph disabled because Neo4j is unavailable: {e}"
            )

    async def add_entity(
        self,
        entity_type: str,
        entity_id: str,
        properties: Optional[
            Dict[str, Any]
        ] = None,
    ) -> None:
        """
        Add or update graph entity.
        """

        if self.driver is None:
            return

        try:
            cypher = f"""
            MERGE (e:{entity_type} {{id: $id}})
            SET e += $props
            """

            async with self.driver.session() as session:
                await session.run(
                    cypher,
                    id=entity_id,
                    props=properties or {},
                )

        except Neo4jError as e:
            logger.error(
                f"Failed to add entity: {e}"
            )

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
    ) -> None:
        """
        Add relationship between entities.
        """

        if self.driver is None:
            return

        try:
            cypher = f"""
            MATCH (a:{subject_type} {{id: $sub_id}})
            MATCH (b:{object_type} {{id: $obj_id}})

            MERGE (a)-[r:{relation}]->(b)

            SET r += $props
            """

            async with self.driver.session() as session:
                await session.run(
                    cypher,
                    sub_id=subject_id,
                    obj_id=object_id,
                    props=properties or {},
                )

        except Neo4jError as e:
            logger.error(
                f"Failed to add relationship: {e}"
            )

    async def query(
        self,
        cypher: str,
        params: Optional[
            Dict[str, Any]
        ] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute arbitrary Cypher query.
        """

        if self.driver is None:
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

        except Neo4jError as e:
            logger.error(
                f"Query failed: {e}"
            )
            return []

    async def get_user_preferences_graph(
        self,
        user_id: str,
    ) -> Dict[str, List[Dict]]:
        """
        Retrieve user preferences graph.
        """

        cypher = """
        MATCH (u:User {id: $user_id})-[r]->(n)

        RETURN
            type(r) AS relation,
            labels(n)[0] AS entity_type,
            n.id AS entity_id,
            properties(n) AS props
        """

        records = await self.query(
            cypher,
            {"user_id": user_id},
        )

        preferences = {}

        for rec in records:
            relation = rec["relation"]

            preferences.setdefault(
                relation,
                [],
            ).append(
                {
                    "type": rec[
                        "entity_type"
                    ],
                    "id": rec[
                        "entity_id"
                    ],
                    "props": rec["props"],
                }
            )

        return preferences

    async def close(self) -> None:
        """
        Close Neo4j connection.
        """

        if self.driver:
            await self.driver.close()

            logger.info(
                "KnowledgeGraph closed"
            )

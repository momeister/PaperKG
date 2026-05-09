from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_STATEMENTS = [
	"""
	CREATE NODE TABLE IF NOT EXISTS Paper(
		id STRING,
		title STRING,
		year INT64,
		version INT64,
		superseded_by STRING,
		has_full_text BOOLEAN,
		peer_reviewed BOOLEAN,
		retracted BOOLEAN,
		language_original STRING,
		citation_count INT64,
		confidence_score DOUBLE,
		obsolescence_score DOUBLE,
		conflict_flag BOOLEAN,
		embedding_model STRING,
		embedding_version INT64,
		source STRING,
		added_to_graph TIMESTAMP,
		last_updated TIMESTAMP,
		PRIMARY KEY(id)
	)
	""",
	"""
	CREATE NODE TABLE IF NOT EXISTS Concept(
		id STRING,
		label STRING,
		aliases STRING[],
		domain STRING,
		openAlex_id STRING,
		custom BOOLEAN,
		PRIMARY KEY(id)
	)
	""",
	"""
	CREATE NODE TABLE IF NOT EXISTS Author(
		id STRING,
		name STRING,
		orcid STRING,
		affiliation STRING,
		PRIMARY KEY(id)
	)
	""",
	"""
	CREATE NODE TABLE IF NOT EXISTS Method(
		id STRING,
		label STRING,
		domain STRING,
		description STRING,
		PRIMARY KEY(id)
	)
	""",
	"""
	CREATE NODE TABLE IF NOT EXISTS Repository(
		id STRING,
		url STRING,
		language STRING,
		stars INT64,
		PRIMARY KEY(id)
	)
	""",
	"CREATE REL TABLE IF NOT EXISTS CITES(FROM Paper TO Paper)",
	"CREATE REL TABLE IF NOT EXISTS HAS_CONCEPT(FROM Paper TO Concept, weight DOUBLE)",
	"CREATE REL TABLE IF NOT EXISTS HAS_METHOD(FROM Paper TO Method, weight DOUBLE)",
	"CREATE REL TABLE IF NOT EXISTS AUTHORED_BY(FROM Paper TO Author)",
	"CREATE REL TABLE IF NOT EXISTS IMPLEMENTS(FROM Paper TO Repository)",
	"CREATE REL TABLE IF NOT EXISTS SIMILAR_TO(FROM Paper TO Paper, score DOUBLE, type STRING)",
	"CREATE REL TABLE IF NOT EXISTS CONFLICTS_WITH(FROM Paper TO Paper, aspect STRING)",
	"CREATE REL TABLE IF NOT EXISTS SUPERSEDES(FROM Paper TO Paper)",
	"CREATE REL TABLE IF NOT EXISTS RELATED_CONCEPT(FROM Concept TO Concept, relation STRING)",
]


@dataclass
class KuzuConfig:
	db_path: str = "data/graphs/global_kg"


class KuzuGraph:
	"""
	Thin Kuzu wrapper used by Phase 2 ingestion and analysis jobs.
	"""

	def __init__(self, config: KuzuConfig | None = None) -> None:
		self.config = config or KuzuConfig()
		self._db = None
		self._conn = None

	def connect(self) -> None:
		try:
			import kuzu  # type: ignore
		except ImportError as exc:
			raise RuntimeError(
				"Kuzu is not installed. Install with: pip install kuzu"
			) from exc

		db_dir = Path(self.config.db_path)
		db_dir.mkdir(parents=True, exist_ok=True)
		self._db = kuzu.Database(str(db_dir))
		self._conn = kuzu.Connection(self._db)

	@property
	def connection(self) -> Any:
		if self._conn is None:
			self.connect()
		return self._conn

	def initialize_schema(self) -> None:
		conn = self.connection
		for statement in SCHEMA_STATEMENTS:
			conn.execute(statement)

	def merge_paper(self, paper: dict[str, Any]) -> None:
		conn = self.connection
		query = """
		MERGE (p:Paper {id: $id})
		SET
		  p.title = $title,
		  p.year = $year,
		  p.version = $version,
		  p.superseded_by = $superseded_by,
		  p.has_full_text = $has_full_text,
		  p.peer_reviewed = $peer_reviewed,
		  p.retracted = $retracted,
		  p.language_original = $language_original,
		  p.citation_count = $citation_count,
		  p.confidence_score = $confidence_score,
		  p.obsolescence_score = $obsolescence_score,
		  p.conflict_flag = $conflict_flag,
		  p.embedding_model = $embedding_model,
		  p.embedding_version = $embedding_version,
		  p.source = $source,
		  p.added_to_graph = COALESCE(p.added_to_graph, CURRENT_TIMESTAMP),
		  p.last_updated = CURRENT_TIMESTAMP
		"""
		conn.execute(query, paper)

	def merge_citation(self, from_paper_id: str, to_paper_id: str) -> None:
		conn = self.connection
		self.merge_paper(_reference_stub(to_paper_id))
		query = """
		MATCH (a:Paper {id: $from_id}), (b:Paper {id: $to_id})
		MERGE (a)-[:CITES]->(b)
		"""
		conn.execute(query, {"from_id": from_paper_id, "to_id": to_paper_id})

	def merge_similarity(
		self,
		from_paper_id: str,
		to_paper_id: str,
		score: float,
		similarity_type: str,
	) -> None:
		conn = self.connection
		query = """
		MATCH (a:Paper {id: $from_id}), (b:Paper {id: $to_id})
		MERGE (a)-[r:SIMILAR_TO]->(b)
		SET r.score = $score, r.type = $similarity_type
		"""
		conn.execute(
			query,
			{
				"from_id": from_paper_id,
				"to_id": to_paper_id,
				"score": float(score),
				"similarity_type": similarity_type,
			},
		)

	def merge_concept(self, concept: dict[str, Any]) -> None:
		conn = self.connection
		query = """
		MERGE (c:Concept {id: $id})
		SET
		  c.label = $label,
		  c.aliases = $aliases,
		  c.domain = $domain,
		  c.openAlex_id = $openAlex_id,
		  c.custom = $custom
		"""
		conn.execute(query, concept)

	def merge_method(self, method: dict[str, Any]) -> None:
		conn = self.connection
		query = """
		MERGE (m:Method {id: $id})
		SET
		  m.label = $label,
		  m.domain = $domain,
		  m.description = $description
		"""
		conn.execute(query, method)

	def merge_has_concept(self, paper_id: str, concept_id: str, weight: float) -> None:
		conn = self.connection
		query = """
		MATCH (p:Paper {id: $paper_id}), (c:Concept {id: $concept_id})
		MERGE (p)-[r:HAS_CONCEPT]->(c)
		SET r.weight = $weight
		"""
		conn.execute(query, {"paper_id": paper_id, "concept_id": concept_id, "weight": float(weight)})

	def merge_has_method(self, paper_id: str, method_id: str, weight: float) -> None:
		conn = self.connection
		query = """
		MATCH (p:Paper {id: $paper_id}), (m:Method {id: $method_id})
		MERGE (p)-[r:HAS_METHOD]->(m)
		SET r.weight = $weight
		"""
		conn.execute(query, {"paper_id": paper_id, "method_id": method_id, "weight": float(weight)})

	def merge_related_concept(self, subject_id: str, object_id: str, relation_type: str) -> None:
		conn = self.connection
		query = """
		MATCH (a:Concept {id: $subject_id}), (b:Concept {id: $object_id})
		MERGE (a)-[r:RELATED_CONCEPT]->(b)
		SET r.relation = $relation_type
		"""
		conn.execute(query, {"subject_id": subject_id, "object_id": object_id, "relation_type": relation_type})


def initialize_kuzu_schema(db_path: str = "data/graphs/global_kg") -> KuzuGraph:
	graph = KuzuGraph(KuzuConfig(db_path=db_path))
	graph.initialize_schema()
	return graph


def _reference_stub(reference_id: str) -> dict[str, Any]:
	return {
		"id": reference_id,
		"title": "",
		"year": None,
		"version": 1,
		"superseded_by": None,
		"has_full_text": False,
		"peer_reviewed": False,
		"retracted": False,
		"language_original": "unknown",
		"citation_count": 0,
		"confidence_score": 0.5,
		"obsolescence_score": 0.0,
		"conflict_flag": False,
		"embedding_model": "",
		"embedding_version": 0,
		"source": "citation_reference",
	}

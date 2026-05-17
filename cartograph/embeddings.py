"""
Fragrance Embedding Engine — Semantic similarity via sentence-transformers.
Embeds scent note profiles (not names) into vector space.
Precomputes cosine similarity matrix for instant lookups.
"""

import json
import numpy as np
from pathlib import Path

from .taxonomy import FRAGRANCE_TAXONOMY, get_taxonomy_for_embedding

CACHE_DIR = Path.home() / "osint" / "data"
CACHE_FILE = CACHE_DIR / "fragrance_embeddings.npz"
SIMILARITY_FILE = CACHE_DIR / "fragrance_similarity.json"

# Model config
MODEL_NAME = "all-MiniLM-L6-v2"  # 384-dim, 22M params, <100MB


class FragranceEmbedder:
    """Manages fragrance embeddings and similarity lookups."""

    def __init__(self, use_cache=True):
        self.names = []
        self.embeddings = None
        self.similarity_matrix = None
        self._model = None

        if use_cache and CACHE_FILE.exists():
            self._load_cache()
        else:
            self._build()

    def _get_model(self):
        """Lazy-load the sentence transformer model."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(MODEL_NAME)
        return self._model

    def _build(self):
        """Build embeddings from taxonomy and cache them."""
        taxonomy = get_taxonomy_for_embedding()
        self.names = [name for name, _ in taxonomy]
        texts = [notes for _, notes in taxonomy]

        print(f"  Embedding {len(texts)} fragrance profiles with {MODEL_NAME}...")
        model = self._get_model()
        self.embeddings = model.encode(texts, normalize_embeddings=True)

        # Precompute cosine similarity matrix (since embeddings are normalized, dot = cosine)
        self.similarity_matrix = np.dot(self.embeddings, self.embeddings.T)

        self._save_cache()
        print(f"  Done. Cached to {CACHE_FILE}")

    def _save_cache(self):
        """Save embeddings to disk."""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            CACHE_FILE,
            names=np.array(self.names),
            embeddings=self.embeddings,
            similarity_matrix=self.similarity_matrix,
        )

        # Also save human-readable similarity JSON
        sim_data = {}
        for i, name in enumerate(self.names):
            neighbors = []
            scores = self.similarity_matrix[i]
            ranked = np.argsort(scores)[::-1]
            for j in ranked[1:11]:  # Top 10 neighbors (skip self)
                neighbors.append({
                    "name": self.names[j],
                    "similarity": round(float(scores[j]), 4),
                    "family": FRAGRANCE_TAXONOMY[self.names[j]]["family"],
                })
            sim_data[name] = {
                "family": FRAGRANCE_TAXONOMY[name]["family"],
                "neighbors": neighbors,
            }

        with open(SIMILARITY_FILE, "w") as f:
            json.dump(sim_data, f, indent=2)

    def _load_cache(self):
        """Load cached embeddings."""
        data = np.load(CACHE_FILE, allow_pickle=True)
        self.names = list(data["names"])
        self.embeddings = data["embeddings"]
        self.similarity_matrix = data["similarity_matrix"]

    def find_similar(self, fragrance_name, top_n=5, threshold=0.0):
        """
        Find fragrances similar to the given one.

        Returns: list of (name, similarity_score, family) tuples
        """
        # Find index of this fragrance
        try:
            idx = self.names.index(fragrance_name)
        except ValueError:
            # Try case-insensitive match
            lower_names = [n.lower() for n in self.names]
            clean = fragrance_name.lower().replace("  plus", "").replace(" plus", "")
            if clean in lower_names:
                idx = lower_names.index(clean)
            else:
                return []

        scores = self.similarity_matrix[idx]
        ranked = np.argsort(scores)[::-1]

        results = []
        for j in ranked[1:top_n + 1]:  # Skip self
            score = float(scores[j])
            if score >= threshold:
                results.append((
                    self.names[j],
                    round(score, 4),
                    FRAGRANCE_TAXONOMY[self.names[j]]["family"],
                ))

        return results

    def get_similarity(self, frag_a, frag_b):
        """Get cosine similarity between two fragrances."""
        try:
            idx_a = self.names.index(frag_a)
            idx_b = self.names.index(frag_b)
            return float(self.similarity_matrix[idx_a][idx_b])
        except ValueError:
            return 0.0

    def get_family_members(self, family):
        """Get all fragrances in a scent family."""
        return [
            name for name, data in FRAGRANCE_TAXONOMY.items()
            if data["family"] == family
        ]

    def get_similar_set(self, fragrance_name, threshold=0.6):
        """
        Get all fragrances above similarity threshold.
        Used for SQL IN clauses.
        """
        similar = self.find_similar(fragrance_name, top_n=50, threshold=threshold)
        return [name for name, score, family in similar]

    def embed_custom(self, text):
        """Embed arbitrary text (for custom fragrance descriptions)."""
        model = self._get_model()
        return model.encode([text], normalize_embeddings=True)[0]

    def stats(self):
        """Return summary stats about the embedding space."""
        return {
            "total_fragrances": len(self.names),
            "embedding_dim": self.embeddings.shape[1] if self.embeddings is not None else 0,
            "model": MODEL_NAME,
            "families": list(set(
                FRAGRANCE_TAXONOMY[n]["family"] for n in self.names
            )),
            "avg_similarity": float(np.mean(self.similarity_matrix)) if self.similarity_matrix is not None else 0,
            "cache_file": str(CACHE_FILE),
        }

"""
Persistent Recursion State Infrastructure & Storage
===================================================
Manages persistence and retrieval of task-level probes, behavioral contrasts,
and cross-task institutional memory in `.jobs/recursion/`.
"""

import os
import json
import time
import hashlib
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Union, Tuple


@dataclass
class CandidateProbe:
    """
    Represents an individual execution probe candidate run for a ChildCard.
    """
    probe_id: str
    card_id: str
    iteration: int = 0
    harness_config: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending | running | success | failed | error
    stdout: str = ""
    stderr: str = ""
    test_results: Dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    fault_frames: List[Dict[str, Any]] = field(default_factory=list)
    deliverables_diff: Dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CandidateProbe":
        valid_fields = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class ContrastMatrix:
    """
    Records behavioral contrast analysis across candidate probes for a single iteration.
    """
    contrast_id: str
    card_id: str
    iteration: int = 0
    candidate_ids: List[str] = field(default_factory=list)
    differences: List[Dict[str, Any]] = field(default_factory=list)
    fault_frames: List[Dict[str, Any]] = field(default_factory=list)
    winner_probe_id: Optional[str] = None
    composed_deliverables: Dict[str, str] = field(default_factory=dict)
    synthesis_rationale: str = ""
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContrastMatrix":
        valid_fields = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class LessonLearned:
    """
    An institutional lesson extracted from completed tasks and contrast evaluations.
    """
    lesson_id: str
    category: str = "general"  # library_quirk | compiler_flag | error_resolution | architecture | test_strategy
    summary: str = ""
    details: str = ""
    source_card_id: str = ""
    task_fingerprint: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    confidence: float = 1.0
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LessonLearned":
        valid_fields = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


class RecursionStateManager:
    """
    Persistent storage engine and runtime state manager for Harness² recursion.
    Maintains:
      - .jobs/recursion/probes/<card_id>/
      - .jobs/recursion/contrasts/<card_id>/
      - .jobs/recursion/lessons_learned.json
    """

    def __init__(self, workspace_root: str = ".", storage_dir: Optional[str] = None):
        self.workspace_root = Path(workspace_root)
        self.storage_dir = Path(storage_dir or (self.workspace_root / ".jobs" / "recursion"))
        self.probes_dir = self.storage_dir / "probes"
        self.contrasts_dir = self.storage_dir / "contrasts"
        self.lessons_file = self.storage_dir / "lessons_learned.json"

        # Initialize directory layout
        self.probes_dir.mkdir(parents=True, exist_ok=True)
        self.contrasts_dir.mkdir(parents=True, exist_ok=True)

        if not self.lessons_file.exists():
            self._write_json(self.lessons_file, [])

    def _write_json(self, path: Path, data: Any):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _read_json(self, path: Path, default: Any = None) -> Any:
        if not path.exists():
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    # -------------------------------------------------------------------------
    # Candidate Probes
    # -------------------------------------------------------------------------

    def _get_card_probes_file(self, card_id: str) -> Path:
        sanitized = card_id.replace("/", "_").replace("\\", "_")
        return self.probes_dir / f"{sanitized}.json"

    def save_candidate_probe(self, probe: Union[CandidateProbe, Dict[str, Any]]) -> str:
        """
        Saves or updates a single candidate probe record.
        """
        if isinstance(probe, dict):
            probe_obj = CandidateProbe.from_dict(probe)
        else:
            probe_obj = probe

        card_file = self._get_card_probes_file(probe_obj.card_id)
        existing = self._read_json(card_file, [])
        if not isinstance(existing, list):
            existing = []

        # Update if exists, else append
        updated = False
        for i, item in enumerate(existing):
            if item.get("probe_id") == probe_obj.probe_id:
                existing[i] = probe_obj.to_dict()
                updated = True
                break

        if not updated:
            existing.append(probe_obj.to_dict())

        self._write_json(card_file, existing)
        return probe_obj.probe_id

    def save_candidate_probes(
        self,
        card_id: str,
        probes: List[Union[CandidateProbe, Dict[str, Any]]],
        iteration: Optional[int] = None
    ) -> List[str]:
        """
        Saves multiple candidate probes for a card, optionally tagging iteration.
        """
        saved_ids = []
        for p in probes:
            if isinstance(p, dict):
                p_obj = CandidateProbe.from_dict(p)
            else:
                p_obj = p
            p_obj.card_id = card_id
            if iteration is not None:
                p_obj.iteration = iteration
            self.save_candidate_probe(p_obj)
            saved_ids.append(p_obj.probe_id)
        return saved_ids

    def load_candidate_probes(
        self,
        card_id: str,
        iteration: Optional[int] = None
    ) -> List[CandidateProbe]:
        """
        Loads all candidate probes for a given card_id, optionally filtered by iteration.
        """
        card_file = self._get_card_probes_file(card_id)
        raw_list = self._read_json(card_file, [])
        if not isinstance(raw_list, list):
            return []

        probes = [CandidateProbe.from_dict(item) for item in raw_list]
        if iteration is not None:
            probes = [p for p in probes if p.iteration == iteration]
        return probes

    def get_probe(self, card_id: str, probe_id: str) -> Optional[CandidateProbe]:
        """
        Retrieves a single candidate probe by card_id and probe_id.
        """
        probes = self.load_candidate_probes(card_id)
        for p in probes:
            if p.probe_id == probe_id:
                return p
        return None

    def list_probes(self, card_id: Optional[str] = None) -> List[CandidateProbe]:
        """
        Lists all probes across all cards, or for a specific card.
        """
        if card_id:
            return self.load_candidate_probes(card_id)

        all_probes: List[CandidateProbe] = []
        for file in self.probes_dir.glob("*.json"):
            raw_list = self._read_json(file, [])
            if isinstance(raw_list, list):
                all_probes.extend(CandidateProbe.from_dict(item) for item in raw_list)
        return all_probes

    # -------------------------------------------------------------------------
    # Behavioral Contrast Matrices
    # -------------------------------------------------------------------------

    def _get_card_contrasts_file(self, card_id: str) -> Path:
        sanitized = card_id.replace("/", "_").replace("\\", "_")
        return self.contrasts_dir / f"{sanitized}.json"

    def save_contrast_matrix(self, contrast: Union[ContrastMatrix, Dict[str, Any]]) -> str:
        """
        Saves or updates a behavioral contrast matrix.
        """
        if isinstance(contrast, dict):
            c_obj = ContrastMatrix.from_dict(contrast)
        else:
            c_obj = contrast

        card_file = self._get_card_contrasts_file(c_obj.card_id)
        existing = self._read_json(card_file, [])
        if not isinstance(existing, list):
            existing = []

        updated = False
        for i, item in enumerate(existing):
            if (
                item.get("contrast_id") == c_obj.contrast_id
                or (item.get("iteration") == c_obj.iteration and item.get("card_id") == c_obj.card_id)
            ):
                existing[i] = c_obj.to_dict()
                updated = True
                break

        if not updated:
            existing.append(c_obj.to_dict())

        self._write_json(card_file, existing)
        return c_obj.contrast_id

    def load_contrast_matrix(
        self,
        card_id: str,
        iteration: Optional[int] = None
    ) -> Optional[ContrastMatrix]:
        """
        Loads the contrast matrix for a card, optionally matching iteration (or latest if None).
        """
        card_file = self._get_card_contrasts_file(card_id)
        raw_list = self._read_json(card_file, [])
        if not isinstance(raw_list, list) or not raw_list:
            return None

        contrasts = [ContrastMatrix.from_dict(item) for item in raw_list]
        if iteration is not None:
            for c in contrasts:
                if c.iteration == iteration:
                    return c
            return None
        return contrasts[-1]

    def list_contrasts(self, card_id: Optional[str] = None) -> List[ContrastMatrix]:
        """
        Lists all contrast matrices across all cards, or for a specific card.
        """
        if card_id:
            card_file = self._get_card_contrasts_file(card_id)
            raw_list = self._read_json(card_file, [])
            if isinstance(raw_list, list):
                return [ContrastMatrix.from_dict(item) for item in raw_list]
            return []

        all_contrasts: List[ContrastMatrix] = []
        for file in self.contrasts_dir.glob("*.json"):
            raw_list = self._read_json(file, [])
            if isinstance(raw_list, list):
                all_contrasts.extend(ContrastMatrix.from_dict(item) for item in raw_list)
        return all_contrasts

    # -------------------------------------------------------------------------
    # Institutional Lessons Learned (Cross-Task Meta-Memory)
    # -------------------------------------------------------------------------

    def record_lesson(
        self,
        lesson: Union[LessonLearned, Dict[str, Any]]
    ) -> LessonLearned:
        """
        Records a new institutional lesson. If a lesson with the same hash/id exists, updates it.
        """
        if isinstance(lesson, dict):
            l_obj = LessonLearned.from_dict(lesson)
        else:
            l_obj = lesson

        if not l_obj.lesson_id:
            # Generate deterministic hash-based id if missing
            digest = hashlib.sha256(f"{l_obj.category}:{l_obj.summary}:{l_obj.source_card_id}".encode("utf-8")).hexdigest()[:12]
            l_obj.lesson_id = f"lesson-{digest}"

        existing_lessons = self._read_json(self.lessons_file, [])
        if not isinstance(existing_lessons, list):
            existing_lessons = []

        updated = False
        for i, item in enumerate(existing_lessons):
            if item.get("lesson_id") == l_obj.lesson_id or (
                item.get("category") == l_obj.category and item.get("summary") == l_obj.summary
            ):
                existing_lessons[i] = l_obj.to_dict()
                updated = True
                break

        if not updated:
            existing_lessons.append(l_obj.to_dict())

        self._write_json(self.lessons_file, existing_lessons)
        return l_obj

    def save_lessons(self, lessons: List[Union[LessonLearned, Dict[str, Any]]]):
        """
        Persists a full list of lessons to lessons_learned.json.
        """
        serializable = []
        for l in lessons:
            if isinstance(l, dict):
                serializable.append(LessonLearned.from_dict(l).to_dict())
            else:
                serializable.append(l.to_dict())
        self._write_json(self.lessons_file, serializable)

    def load_lessons(
        self,
        category: Optional[str] = None,
        tag: Optional[str] = None
    ) -> List[LessonLearned]:
        """
        Loads all institutional lessons, optionally filtered by category or tag.
        """
        raw_list = self._read_json(self.lessons_file, [])
        if not isinstance(raw_list, list):
            return []

        lessons = [LessonLearned.from_dict(item) for item in raw_list]
        if category:
            lessons = [l for l in lessons if l.category == category]
        if tag:
            lessons = [l for l in lessons if tag in l.tags]
        return lessons

    def get_relevant_lessons(
        self,
        query_or_task: str,
        limit: int = 5
    ) -> List[LessonLearned]:
        """
        Retrieves top relevant lessons matching query keywords.
        """
        all_lessons = self.load_lessons()
        if not all_lessons:
            return []

        query_tokens = set(query_or_task.lower().split())
        scored: List[Tuple[float, LessonLearned]] = []
        for l in all_lessons:
            text = f"{l.summary} {l.details} {' '.join(l.tags)} {l.category}".lower()
            lesson_tokens = set(text.split())
            intersection = query_tokens.intersection(lesson_tokens)
            score = len(intersection) * l.confidence
            scored.append((score, l))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [l for score, l in scored[:limit]]

    def deduplicate_lessons(self) -> int:
        """
        Removes duplicates from lessons_learned.json based on (category, summary).
        Returns count of removed duplicates.
        """
        lessons = self.load_lessons()
        seen = {}
        unique = []
        removed = 0
        for l in lessons:
            key = (l.category, l.summary.strip().lower())
            if key not in seen:
                seen[key] = True
                unique.append(l)
            else:
                removed += 1

        if removed > 0:
            self.save_lessons(unique)
        return removed

    def extract_lessons_from_task(
        self,
        child: Any,
        contrast: Optional[ContrastMatrix] = None,
        probes: Optional[List[CandidateProbe]] = None,
        task_fingerprint: str = "",
        execution_result: Optional[Dict[str, Any]] = None,
    ) -> List[LessonLearned]:
        """
        Synthesizes evidence-supported institutional lessons from completed ChildCard execution,
        behavioral contrast matrices, probe logs, and resolved fault frames.
        """
        lessons: List[LessonLearned] = []
        child_id = getattr(child, "id", "") if hasattr(child, "id") else (child.get("id", "") if isinstance(child, dict) else "")
        child_name = getattr(child, "name", "") if hasattr(child, "name") else (child.get("name", "") if isinstance(child, dict) else "Task")
        tactical_obj = getattr(child, "tactical_objective", "") if hasattr(child, "tactical_objective") else (child.get("tactical_objective", "") if isinstance(child, dict) else "")
        deliverables = getattr(child, "deliverables", []) if hasattr(child, "deliverables") else (child.get("deliverables", []) if isinstance(child, dict) else [])
        parent_squad = getattr(child, "parent_squad_id", "") if hasattr(child, "parent_squad_id") else (child.get("parent_squad_id", "") if isinstance(child, dict) else "")

        # Extract file paths and tags
        deliv_paths = [d.get("path", "") if isinstance(d, dict) else str(d) for d in deliverables]
        deliv_str = ", ".join(deliv_paths[:3]) if deliv_paths else "deliverables"

        base_tags = set()
        if parent_squad:
            base_tags.add(parent_squad.lower().replace("squad-", ""))
        for p in deliv_paths:
            ext = Path(p).suffix.lstrip(".")
            if ext:
                base_tags.add(ext.lower())

        # Technical keyword extraction
        keywords = [
            "jwt", "auth", "token", "docker", "fastapi", "react", "vue", "typescript",
            "python", "git", "worktree", "rag", "ast", "sqlite", "postgres", "redis",
            "async", "pydantic", "flask", "django", "pytest", "unittest", "compiler",
            "syntax", "harness", "mock", "rbac", "encryption", "schema"
        ]
        text_corpus = f"{tactical_obj} {child_name}".lower()
        for kw in keywords:
            if kw in text_corpus:
                base_tags.add(kw)

        # 1. Lesson from Behavioral Contrast Matrix
        if contrast is not None:
            if contrast.synthesis_rationale or contrast.winner_probe_id:
                summary = (
                    f"Harness contrast synthesis for '{child_name}': {contrast.synthesis_rationale[:160]}"
                    if contrast.synthesis_rationale
                    else f"Harness probe {contrast.winner_probe_id} validated deliverables for '{child_name}'."
                )
                category = "architecture"
                if contrast.fault_frames:
                    category = "error_resolution"

                contrast_tags = set(base_tags)
                contrast_tags.update(["contrast", "harness2"])

                diff_summary = f"{len(contrast.differences)} behavioral delta(s) evaluated across {len(contrast.candidate_ids)} candidates."
                details = f"{summary}\nDifferences: {diff_summary}\nDeliverables: {deliv_str}"

                lessons.append(LessonLearned(
                    lesson_id="",
                    category=category,
                    summary=summary,
                    details=details,
                    source_card_id=child_id,
                    task_fingerprint=task_fingerprint,
                    evidence={
                        "contrast_id": contrast.contrast_id,
                        "winner_probe_id": contrast.winner_probe_id,
                        "candidate_ids": contrast.candidate_ids,
                        "fault_frames_count": len(contrast.fault_frames),
                    },
                    tags=sorted(list(contrast_tags)),
                    confidence=0.95,
                ))

        # 2. Lessons from Probes and Fault Frames
        if probes:
            failed_probes = [p for p in probes if p.status in ["failed", "error"] or p.fault_frames]
            for p in failed_probes:
                for ff in p.fault_frames:
                    file_hint = ff.get("file", "") or ff.get("path", "")
                    reason = ff.get("reason", "") or ff.get("error", "") or ff.get("message", "")
                    if reason or file_hint:
                        cat = "library_quirk" if any(k in reason.lower() for k in ["import", "module", "version", "deprecated"]) else "error_resolution"
                        if any(k in reason.lower() for k in ["syntax", "compiler", "typeerror", "indentation"]):
                            cat = "compiler_flag"

                        ff_tags = set(base_tags)
                        ff_tags.update(["fault_frame", "self_healing"])

                        summary = f"Resolved fault in probe {p.probe_id}: {reason[:120]}" if reason else f"Resolved fault in {file_hint} for '{child_name}'"
                        lessons.append(LessonLearned(
                            lesson_id="",
                            category=cat,
                            summary=summary,
                            details=f"Fault frame detected in {file_hint}: {reason}. Overcome in composed harness candidate.",
                            source_card_id=child_id,
                            task_fingerprint=task_fingerprint,
                            evidence={"probe_id": p.probe_id, "fault_frame": ff},
                            tags=sorted(list(ff_tags)),
                            confidence=0.90,
                        ))

        # 3. High-Level Architectural / Strategy Lesson for Completed Task
        summary_arch = f"Verified implementation pattern for {child_name}: {tactical_obj[:140]}"
        arch_details = f"Task '{child_name}' [{child_id}] completed successfully.\nDeliverables target: {deliv_str}.\nObjective: {tactical_obj}"
        arch_tags = set(base_tags)
        arch_tags.add("task_success")

        lessons.append(LessonLearned(
            lesson_id="",
            category="architecture",
            summary=summary_arch,
            details=arch_details,
            source_card_id=child_id,
            task_fingerprint=task_fingerprint,
            evidence={"deliverables": deliv_paths, "squad": parent_squad},
            tags=sorted(list(arch_tags)),
            confidence=0.85,
        ))

        return lessons

    def extract_and_record_lessons(
        self,
        child: Any,
        contrast: Optional[ContrastMatrix] = None,
        probes: Optional[List[CandidateProbe]] = None,
        task_fingerprint: str = "",
        execution_result: Optional[Dict[str, Any]] = None,
    ) -> List[LessonLearned]:
        """
        Extracts institutional lessons from task execution, records them into storage,
        and runs deduplication. Returns the list of recorded lessons.
        """
        extracted = self.extract_lessons_from_task(
            child=child,
            contrast=contrast,
            probes=probes,
            task_fingerprint=task_fingerprint,
            execution_result=execution_result,
        )
        recorded = []
        for l in extracted:
            saved = self.record_lesson(l)
            recorded.append(saved)
        self.deduplicate_lessons()
        return recorded

    # -------------------------------------------------------------------------
    # Runtime ChildCard State Attachment Helper
    # -------------------------------------------------------------------------

    def attach_recursion_state(
        self,
        child_card: Any,
        recursion_depth: int = 0,
        enable_harness2: bool = False,
        probes: Optional[List[CandidateProbe]] = None,
        contrast: Optional[ContrastMatrix] = None
    ) -> Dict[str, Any]:
        """
        Attaches runtime recursion metadata to ChildCard._recursion_state.
        """
        state = {
            "recursion_depth": recursion_depth,
            "enable_harness2": enable_harness2,
            "probes_count": len(probes) if probes else 0,
            "last_contrast_id": contrast.contrast_id if contrast else None,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        if hasattr(child_card, "_recursion_state"):
            child_card._recursion_state.update(state)
        return state

    def get_recursion_state(self, child_card: Any) -> Dict[str, Any]:
        """
        Returns the runtime recursion state from ChildCard._recursion_state if present.
        """
        if hasattr(child_card, "_recursion_state") and isinstance(child_card._recursion_state, dict):
            return child_card._recursion_state
        return {}

    def update_recursion_state(self, child_card: Any, **kwargs) -> Dict[str, Any]:
        """
        Updates runtime recursion state on a ChildCard.
        """
        if not hasattr(child_card, "_recursion_state") or not isinstance(child_card._recursion_state, dict):
            child_card._recursion_state = {}
        child_card._recursion_state.update(kwargs)
        child_card._recursion_state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return child_card._recursion_state

"""Dataverse environment adapter for SkillOpt."""
from __future__ import annotations

import json
import os

from skillopt.datasets.base import BatchSpec
from skillopt.envs.base import EnvAdapter
from skillopt.envs.dataverse.dataloader import DataverseSkillDataLoader


class DataverseSkillAdapter(EnvAdapter):
    """Dataverse environment adapter."""

    def __init__(
        self,
        skill_name: str,
        plugin_src_dir: str,
        split_dir: str = "",
        data_path: str = "",
        split_mode: str = "split_dir",
        split_seed: int = 42,
        split_output_dir: str = "",
        max_turns: int = 1,
        exec_timeout: int = 180,
        workers: int = 24,
        live_workers: int = 4,
        analyst_workers: int = 16,
        failure_only: bool = False,
        minibatch_size: int = 8,
        edit_budget: int = 4,
        seed: int = 42,
        limit: int = 0,
        max_completion_tokens: int = 16384,
        live_enabled: bool = True,
        live_env_url: str = "",
        live_solution: str = "SkillOptEvals",
        live_prefix: str = "sko",
        record_bodies: bool = False,
    ) -> None:
        self.skill_name = skill_name
        self.plugin_src_dir = plugin_src_dir
        self.max_turns = max_turns
        self.exec_timeout = exec_timeout
        self.workers = workers
        self.live_workers = live_workers
        self.max_completion_tokens = int(max_completion_tokens)
        self.analyst_workers = analyst_workers
        self.failure_only = failure_only
        self.minibatch_size = minibatch_size
        self.edit_budget = edit_budget
        self.live_enabled = live_enabled
        self.live_env_url = live_env_url
        self.live_solution = live_solution
        self.live_prefix = live_prefix
        self.record_bodies = record_bodies
        self.dataloader = DataverseSkillDataLoader(
            split_dir=split_dir,
            data_path=data_path,
            split_mode=split_mode,
            split_seed=split_seed,
            split_output_dir=split_output_dir,
            seed=seed,
            limit=limit,
        )

    def setup(self, cfg: dict) -> None:
        super().setup(cfg)
        self.dataloader.setup(cfg)

    def get_dataloader(self):
        return self.dataloader

    def build_env_from_batch(self, batch: BatchSpec, **kwargs):
        return list(batch.payload or [])

    def build_train_env(self, batch_size: int, seed: int, **kwargs):
        batch = self.dataloader.build_train_batch(batch_size=batch_size, seed=seed, **kwargs)
        return self.build_env_from_batch(batch, **kwargs)

    def build_eval_env(self, env_num: int, split: str, seed: int, **kwargs):
        batch = self.dataloader.build_eval_batch(env_num=env_num, split=split, seed=seed, **kwargs)
        return self.build_env_from_batch(batch, **kwargs)

    def rollout(
        self,
        env_manager,
        skill_content: str,
        out_dir: str,
        **kwargs,
    ) -> list[dict]:
        raise NotImplementedError("Task 17 wires this to dataverse.rollout.run_batch")

    def reflect(
        self,
        results: list[dict],
        skill_content: str,
        out_dir: str,
        **kwargs,
    ) -> list[dict | None]:
        prediction_dir = kwargs.get("prediction_dir", os.path.join(out_dir, "predictions"))
        patches_dir = kwargs.get("patches_dir", os.path.join(out_dir, "patches"))
        random_seed = kwargs.get("random_seed")
        step_buffer_context = kwargs.get("step_buffer_context", "")
        meta_skill_context = kwargs.get("meta_skill_context", "")

        from skillopt.gradient.reflect import run_minibatch_reflect
        raw_patches = run_minibatch_reflect(
            results=results,
            skill_content=skill_content,
            prediction_dir=prediction_dir,
            patches_dir=patches_dir,
            workers=self.analyst_workers,
            failure_only=self.failure_only,
            minibatch_size=self.minibatch_size,
            edit_budget=self.edit_budget,
            random_seed=random_seed,
            error_system=self.get_error_minibatch_prompt(),
            success_system=self.get_success_minibatch_prompt(),
            step_buffer_context=step_buffer_context,
            meta_skill_context=meta_skill_context,
            update_mode=getattr(self, "_cfg", {}).get("skill_update_mode", "patch"),
        )
        return self._filter_in_scope(raw_patches, out_dir)


    def _filter_in_scope(self, patches: list, out_dir: str) -> list:
        """Drop patches whose target_file is not the configured skill's SKILL.md.

        Bare "SKILL.md" (no path separators) is in-scope. Any path that
        references a subdirectory (e.g., "references/foo.md", "../dv-query/SKILL.md")
        is out-of-scope.
        """
        in_scope = []
        out_of_scope = []
        for p in patches:
            if p is None:
                in_scope.append(p)
                continue
            target = ""
            if isinstance(p, dict):
                target = (p.get("target_file") or "SKILL.md")
            target_norm = target.replace("\\", "/").strip().lower()
            # In-scope: empty string, "skill.md", or "./skill.md" — no subdirs.
            if target_norm in ("", "skill.md", "./skill.md"):
                in_scope.append(p)
            else:
                out_of_scope.append(p)
        if out_of_scope:
            path = os.path.join(out_dir, "out_of_scope_patches.jsonl")
            os.makedirs(out_dir, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                for p in out_of_scope:
                    f.write(json.dumps(p) + "\n")
        return in_scope

    def get_task_types(self) -> list[str]:
        return ["dataverse"]
